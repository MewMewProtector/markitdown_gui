"""
Folder watcher based on `QFileSystemWatcher` with debouncing and recursive
sub-folder tracking. All disk I/O happens on a background thread so the UI
never freezes, even on very large folder trees.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime

from PySide6.QtCore import (
    QFileSystemWatcher,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

from . import config
from .file_filter import is_supported

# Safety limits: don't try to watch absurdly large folder trees.
MAX_WATCHED_DIRS = 500
MAX_WALK_DEPTH = 8


class FolderWatcher(QObject):
    """
    Watches a directory (and a limited set of its sub-directories) for new
    files. The initial population and subsequent diffs run on a worker thread
    so the UI thread is never blocked.
    """

    new_files = Signal(list)        # list[str] of new supported paths
    folder_changed = Signal(str)    # absolute path of new watch folder

    # Internal signal used by worker tasks to marshal results back to the UI
    # thread (signals are thread-safe in Qt; queued connections auto-marshall).
    _scan_result = Signal(list, list)  # (new_files, new_dirs)

    DEBOUNCE_MS = 500

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_dir_changed)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._schedule_scan)

        self._scan_result.connect(self._on_scan_complete)

        self._root: str | None = None
        self._known: set[str] = set()
        self._watched_dirs: set[str] = set()
        self._scan_in_progress = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_root(self, root: str | None) -> None:
        """Change the watched root directory (or stop watching if None)."""
        self.stop()
        self._root = None
        self._known.clear()
        self._watched_dirs.clear()
        if root and os.path.isdir(root):
            self._root = os.path.abspath(root)
            # Watch the root immediately so events that fire before the
            # background walk finishes are still caught.
            self._fs_watcher.addPath(self._root)
            self._watched_dirs.add(self._root)
            # Sub-directory watching + baseline population happen in background.
            self._schedule_initial_scan()
            self.folder_changed.emit(self._root)

    def refresh_known(self) -> None:
        """Rebuild the baseline snapshot from disk (no signals emitted)."""
        self._known.clear()
        if self._root:
            self._populate_quick(self._root)

    def current_root(self) -> str | None:
        return self._root

    def stop(self) -> None:
        dirs = list(self._fs_watcher.directories())
        if dirs:
            self._fs_watcher.removePaths(dirs)
        self._debounce.stop()
        self._watched_dirs.clear()

    # ------------------------------------------------------------------
    # Internals — scheduling
    # ------------------------------------------------------------------
    def _on_dir_changed(self, _path: str) -> None:
        # Coalesce bursts of events into one scan.
        self._debounce.start()

    def _schedule_initial_scan(self) -> None:
        if not self._root:
            return
        task = _ScanTask(self, initial=True)
        QThreadPool.globalInstance().start(task)

    def _schedule_scan(self) -> None:
        if self._scan_in_progress or not self._root:
            return
        self._scan_in_progress = True
        task = _ScanTask(self, initial=False)
        QThreadPool.globalInstance().start(task)

    # ------------------------------------------------------------------
    # Slot: receives scan results on the UI thread.
    # ------------------------------------------------------------------
    @Slot(list, list)
    def _on_scan_complete(self, new_files: list[str], new_dirs: list[str]) -> None:
        self._scan_in_progress = False
        try:
            if new_dirs:
                room = max(0, MAX_WATCHED_DIRS - len(self._watched_dirs))
                if room > 0:
                    self._fs_watcher.addPaths(new_dirs[:room])
                    self._watched_dirs.update(new_dirs[:room])
            if new_files:
                supported = [p for p in new_files if is_supported(p)]
                if supported:
                    now = datetime.utcnow().isoformat(timespec="seconds")
                    for p in supported:
                        try:
                            config.mark_seen(p, now)
                        except Exception:
                            pass
                    self.new_files.emit(supported)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Walks called from a worker thread.
    # ------------------------------------------------------------------
    def _walk(self, root: str, depth: int) -> tuple[set[str], list[str]]:
        files: set[str] = set()
        new_dirs: list[str] = []
        try:
            start_depth = root.count(os.sep)
            for r, dirs, filenames in os.walk(root):
                cur_depth = r.count(os.sep) - start_depth
                if cur_depth > MAX_WALK_DEPTH:
                    dirs[:] = []
                    continue
                if r not in self._watched_dirs and len(self._watched_dirs) < MAX_WATCHED_DIRS:
                    new_dirs.append(r)
                for f in filenames:
                    try:
                        files.add(os.path.join(r, f))
                    except Exception:
                        continue
        except Exception:
            traceback.print_exc()
        return files, new_dirs

    def _populate_quick(self, root: str) -> None:
        try:
            self._walk(root, depth=0)
        except Exception:
            traceback.print_exc()


class _ScanTask(QRunnable):
    """
    Performs a single scan of the watched root on a worker thread and
    delivers the result back to the watcher via a queued signal.
    """

    def __init__(self, watcher: FolderWatcher, initial: bool):
        super().__init__()
        self.setAutoDelete(True)
        self._watcher = watcher
        self._initial = initial

    def run(self) -> None:
        try:
            root = self._watcher._root
            if not root or not os.path.isdir(root):
                self._watcher._scan_result.emit([], [])
                return

            current, new_dirs = self._watcher._walk(root, depth=0)

            if self._initial:
                self._watcher._known.update(current)
                self._watcher._scan_result.emit([], new_dirs)
                return

            new_files = sorted(current - self._watcher._known)
            self._watcher._known.update(current)
            self._watcher._scan_result.emit(new_files, new_dirs)
        except Exception:
            traceback.print_exc()
            try:
                self._watcher._scan_result.emit([], [])
            except Exception:
                pass