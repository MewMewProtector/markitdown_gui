"""
QRunnable that converts a single file using `Converter`.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from . import config, naming
from .converter import Converter
from .image_descriptor import (
    describe_image_via_cache,
    is_image_file,
)


class ConversionSignals(QObject):
    """Qt-friendly signals for a single conversion job."""

    started = Signal(int, str)           # job_id, source_path
    finished = Signal(int, str, str)     # job_id, source_path, output_path
    failed = Signal(int, str, str)       # job_id, source_path, error
    progress = Signal(int, str, int)     # job_id, source_path, percent (0..100)
    # Non-fatal warning emitted after a successful conversion but with
    # image-description failures. MainWindow appends this to the log row
    # so the user can see WHY images weren't described.
    warning = Signal(int, str, str)      # job_id, source_path, warning_text


class ConversionJob(QRunnable):
    """
    Convert one source file into a `.md` file.

    `job_id` is an opaque integer used by the UI to correlate signals with
    rows in the files table.
    """

    ORIGIN_MANUAL = "manual"
    ORIGIN_STREAMING = "streaming"

    def __init__(
        self,
        job_id: int,
        source_path: str,
        output_folder: str,
        settings: dict[str, str],
        origin: str = ORIGIN_MANUAL,
        output_path: str | None = None,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.job_id = job_id
        self.source_path = source_path
        self.output_folder = output_folder
        self.settings = settings
        self.origin = origin
        self.output_path = output_path
        self.signals = ConversionSignals()

    # ------------------------------------------------------------------
    @Slot()
    def run(self) -> None:
        started_at = datetime.utcnow().isoformat(timespec="seconds")
        converter: Converter | None = None
        try:
            self.signals.started.emit(self.job_id, self.source_path)
            self.signals.progress.emit(self.job_id, self.source_path, 5)

            # Caller may pre-reserve a unique output path (used by
            # streaming to prevent collisions between concurrent jobs);
            # otherwise compute one the classic way.
            if self.output_path:
                output_path = self.output_path
            else:
                output_path = naming.build_output_path(
                    self.source_path, self.output_folder
                )
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            self.signals.progress.emit(self.job_id, self.source_path, 25)

            try:
                converter = Converter(self.settings)
                # Wire per-image progress so the user sees motion while
                # the LLM describes each image (otherwise the UI stays
                # at 25% for the entire multi-image phase).
                converter.set_progress_callback(
                    lambda pct, msg: self.signals.progress.emit(
                        self.job_id, self.source_path, pct
                    )
                )
                markdown = converter.convert(self.source_path)
            except Exception as exc:
                # Per-image fault tolerance: if markitdown blows up on a
                # single image (LLM timeout, bad bytes, unsupported
                # variant), write the EXIF fallback / placeholder file
                # instead of failing the whole batch.
                if is_image_file(self.source_path):
                    markdown = self._image_fallback_markdown(exc)
                else:
                    raise

            # Surface image-description diagnostics so the user can see
            # WHY a description didn't happen (e.g. bad API key, wrong
            # model name, rate limit).
            desc_errors = []
            if converter is not None:
                try:
                    desc_errors = converter.last_description_errors or []
                except Exception:
                    pass
            if desc_errors:
                # Emit a warning so the main window can record it on the
                # log row and optionally toast the first line.
                warning_text = "; ".join(desc_errors[:5])
                if len(desc_errors) > 5:
                    warning_text += f" (и ещё {len(desc_errors) - 5})"
                self.signals.warning.emit(
                    self.job_id, self.source_path, warning_text
                )

            self.signals.progress.emit(self.job_id, self.source_path, 80)

            # Never silently overwrite a file that appeared after the output
            # name was selected (for example by another process).
            with open(output_path, "x", encoding="utf-8") as f:
                f.write(markdown)

            self.signals.progress.emit(self.job_id, self.source_path, 100)
            self.signals.finished.emit(self.job_id, self.source_path, output_path)
        except Exception as exc:
            self.signals.failed.emit(
                self.job_id,
                self.source_path,
                f"{exc}\n{traceback.format_exc()}",
            )

    # ------------------------------------------------------------------
    def _image_fallback_markdown(self, exc: Exception) -> str:
        """
        Build a minimal markdown file for an image that couldn't be
        processed. Tries to fetch a cached description first (so the user
        still gets something useful when the LLM is the failing piece);
        otherwise writes the placeholder.
        """
        description = describe_image_via_cache(self.source_path, self.settings)
        try:
            image_url = Path(self.source_path).resolve().as_uri()
        except (OSError, ValueError):
            image_url = self.source_path.replace("\\", "/")
        parts = [
            f"<!-- markitdown_gui: image conversion failed: {exc} -->",
            f"![{os.path.basename(self.source_path)}]({image_url})",
            "",
            "# Description:",
            "",
        ]
        if description:
            parts.append(description)
        else:
            parts.append("> [не удалось получить описание изображения]")
        return "\n".join(parts) + "\n"


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
