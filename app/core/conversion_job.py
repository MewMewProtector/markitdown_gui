"""
QRunnable that converts a single file using `Converter`.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from . import config, naming
from .converter import Converter


class ConversionSignals(QObject):
    """Qt-friendly signals for a single conversion job."""

    started = Signal(int, str)           # job_id, source_path
    finished = Signal(int, str, str)     # job_id, source_path, output_path
    failed = Signal(int, str, str)       # job_id, source_path, error
    progress = Signal(int, str, int)     # job_id, source_path, percent (0..100)


class ConversionJob(QRunnable):
    """
    Convert one source file into a `.md` file.

    `job_id` is an opaque integer used by the UI to correlate signals with
    rows in the files table.
    """

    def __init__(
        self,
        job_id: int,
        source_path: str,
        output_folder: str,
        settings: dict[str, str],
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.job_id = job_id
        self.source_path = source_path
        self.output_folder = output_folder
        self.settings = settings
        self.signals = ConversionSignals()

    # ------------------------------------------------------------------
    @Slot()
    def run(self) -> None:
        started_at = datetime.utcnow().isoformat(timespec="seconds")
        try:
            self.signals.started.emit(self.job_id, self.source_path)
            self.signals.progress.emit(self.job_id, self.source_path, 5)

            output_path = naming.build_output_path(self.source_path, self.output_folder)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            self.signals.progress.emit(self.job_id, self.source_path, 25)

            converter = Converter(self.settings)
            markdown = converter.convert(self.source_path)

            self.signals.progress.emit(self.job_id, self.source_path, 80)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown)

            self.signals.progress.emit(self.job_id, self.source_path, 100)
            self.signals.finished.emit(self.job_id, self.source_path, output_path)
        except Exception as exc:
            self.signals.failed.emit(
                self.job_id,
                self.source_path,
                f"{exc}\n{traceback.format_exc()}",
            )


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")