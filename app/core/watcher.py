"""
Folder watcher based on `QFileSystemWatcher` with debouncing and recursive
sub-folder tracking. All disk I/O happens on a background thread so the UI
never freezes, even on very large folder trees.

Design notes for the streaming flow:

* The initial population populates `_known` but emits NO `new_files` —
  files that existed at startup are the baseline.
* Subsequent scans REPLACE `_known` with the latest snapshot (rather
  than just extending it), so a file that was deleted and recreated
  counts as new again.
* If filesystem events arrive while a scan is in progress, the next
  rescan is forced instead of being silently dropped.
* Each scan is tagged with a generation counter; results from a stale
  generation (after `set_root`) are ignored.
"""
from __future__ import annotations

import os
import time
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
    # Tuple shape: (generation, new_files, new_dirs, current_snapshot).
    _scan_result = Signal(int, list, list, list)

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
        self._scan_dirty = False
        self._generation: int = 0
        self._watch_started_ns: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_root(self, root: str | None) -> None:
        """Change the watched root directory (or stop watching if None)."""
        self.stop()
        self._root = None
        self._known.clear()
        self._watched_dirs.clear()
        self._scan_in_progress = False
        self._scan_dirty = False
        if root and os.path.isdir(root):
            self._root = os.path.abspath(root)
            # Files created while the asynchronous initial scan is running
            # still count as new. The worker uses this cutoff to distinguish
            # them from the pre-existing baseline.
            self._watch_started_ns = time.time_ns()
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
        # Invalidate all workers before detaching paths. A scan that finishes
        # during shutdown or after a root switch must not emit new files or
        # overwrite the next root's baseline.
        self._generation += 1
        self._root = None
        self._scan_in_progress = False
        self._scan_dirty = False
        dirs = list(self._fs_watcher.directories())
        if dirs:
            self._fs_watcher.removePaths(dirs)
        self._debounce.stop()
        self._watched_dirs.clear()
        self._known.clear()

    # ------------------------------------------------------------------
    # Internals — scheduling
    # ------------------------------------------------------------------
    def _on_dir_changed(self, _path: str) -> None:
        # Coalesce bursts of events into one scan. Always mark dirty so
        # an event arriving during an active scan is not lost: the rescan
        # is forced once the current one finishes.
        self._scan_dirty = True
        self._debounce.start()

    def _schedule_initial_scan(self) -> None:
        if not self._root:
            return
        self._scan_in_progress = True
        self._scan_dirty = False
        task = _ScanTask(self, generation=self._generation, initial=True)
        QThreadPool.globalInstance().start(task)

    def _schedule_scan(self) -> None:
        if self._scan_in_progress or not self._root:
            # If a scan is currently running and dirty is already set,
            # the completion handler will trigger another scan.
            return
        self._scan_in_progress = True
        self._scan_dirty = False
        task = _ScanTask(self, generation=self._generation, initial=False)
        QThreadPool.globalInstance().start(task)

    # ------------------------------------------------------------------
    # Slot: receives scan results on the UI thread.
    # ------------------------------------------------------------------
    @Slot(int, list, list, list)
    def _on_scan_complete(
        self,
        generation: int,
        new_files: list[str],
        new_dirs: list[str],
        current_snapshot: list[str] | None = None,
    ) -> None:
        if generation != self._generation:
            # Stale result from a previous watch root: drop it.
            # Do not clear ``_scan_in_progress`` here: a scan for the current
            # generation may already be running.
            return
        self._scan_in_progress = False
        if current_snapshot is not None:
            self._known = set(current_snapshot)
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
        finally:
            # If filesystem events arrived while this scan was running,
            # schedule another pass so we don't miss late-arriving files.
            if self._scan_dirty and self._root is not None:
                self._scan_dirty = False
                # Restart the debounce timer; on expiry a new scan will
                # pick up any in-progress changes.
                self._debounce.start()

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
            files, _new_dirs = self._walk(root, depth=0)
            self._known = files
        except Exception:
            traceback.print_exc()


class _ScanTask(QRunnable):
    """
    Performs a single scan of the watched root on a worker thread and
    delivers the result back to the watcher via a queued signal. Each
    task is tagged with the watcher's current generation so the UI
    thread can drop stale results from a previous root.
    """

    def __init__(self, watcher: FolderWatcher, generation: int, initial: bool):
        super().__init__()
        self.setAutoDelete(True)
        self._watcher = watcher
        self._generation = generation
        self._initial = initial
        self._root = watcher._root
        self._known_snapshot = set(watcher._known)
        self._watch_started_ns = watcher._watch_started_ns

    def run(self) -> None:
        try:
            root = self._root
            if not root or not os.path.isdir(root):
                self._watcher._scan_result.emit(
                    self._generation, [], [], []
                )
                return

            current, new_dirs = self._watcher._walk(root, depth=0)

            if self._initial:
                # Most files form the baseline, but a file created or modified
                # after watching began must not disappear merely because the
                # asynchronous initial walk happened to see it.
                new_files: list[str] = []
                for path in current:
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    changed_ns = max(stat.st_ctime_ns, stat.st_mtime_ns)
                    if changed_ns >= self._watch_started_ns:
                        new_files.append(path)
                self._watcher._scan_result.emit(
                    self._generation,
                    sorted(new_files),
                    new_dirs,
                    sorted(current),
                )
                return

            new_files = sorted(current - self._known_snapshot)
            self._watcher._scan_result.emit(
                self._generation, new_files, new_dirs, sorted(current)
            )
        except Exception:
            traceback.print_exc()
            try:
                self._watcher._scan_result.emit(
                    self._generation, [], [], []
                )
            except Exception:
                pass
