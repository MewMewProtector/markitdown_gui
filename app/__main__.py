"""
Entry point: `python -m app`
"""
from __future__ import annotations

import logging
import sys
import warnings

# Silence noisy third-party warnings BEFORE we import anything that
# pulls them in transitively. `pydub` (via `speech_recognition` and
# `markitdown[all]`) emits a `RuntimeWarning` complaining that ffmpeg
# isn't on PATH. We don't need ffmpeg for text / image / pdf conversion
# and the warning just spams the console.
warnings.filterwarnings(
    "ignore",
    message=r"Couldn't find ffmpeg or avconv.*",
    category=RuntimeWarning,
)

# `pydub` also logs a "Could not find ffprobe" message. Suppress the
# matching loggers so startup stays clean.
logging.getLogger("pydub.utils").setLevel(logging.CRITICAL)

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import __version__
from app.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MarkItDown GUI")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MarkItDownGUI")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())