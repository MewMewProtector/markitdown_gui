"""
Tests verifying that accent changes do NOT silently flip the user's
selected theme. Regression test for the bug where `_on_accent_changed`
read the theme from a stale settings dict captured at startup.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Headless platform before any QApplication touches the screen.
import os  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate the SQLite config from any real user settings.
os.environ["APPDATA"] = tempfile.mkdtemp()  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.main_window import MainWindow  # noqa: E402


class TestThemeAccentInteraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self) -> MainWindow:
        return MainWindow()

    def test_accent_change_keeps_dark_theme(self):
        w = self._make_window()
        w._on_theme_changed("dark")
        self.assertEqual(w._current_mode, "dark")
        w._on_accent_changed("#FF6B35", "")
        # Must still be dark after accent tweak.
        self.assertEqual(w._current_mode, "dark")

    def test_accent_change_keeps_light_theme(self):
        w = self._make_window()
        w._on_theme_changed("light")
        self.assertEqual(w._current_mode, "light")
        w._on_accent_changed("#9C27B0", "")
        self.assertEqual(w._current_mode, "light")

    def test_accent_change_after_system_then_explicit_light(self):
        """
        Reproduces the exact user-reported scenario:

        * App starts in `system` mode (the default).
        * User switches to `light`.
        * User changes the accent — theme must NOT revert to whatever
          `system` would resolve to.
        """
        w = self._make_window()
        # Make sure we start clean: simulate startup in `system`.
        w._current_mode = "system"
        w._on_theme_changed("light")
        self.assertEqual(w._current_mode, "light")
        w._on_accent_changed("#FF6B35", "")
        # The currently applied mode must remain light after accent change.
        self.assertEqual(w._current_mode, "light")
        # And the palette must actually be the light one.
        self.assertEqual(w._current_palette.bg.lower(), "#f7f8fa")

    def test_dark_accent_change_after_explicit_dark(self):
        w = self._make_window()
        w._current_mode = "system"
        w._on_theme_changed("dark")
        self.assertEqual(w._current_mode, "dark")
        w._on_accent_changed("#FF6B35", "")
        self.assertEqual(w._current_mode, "dark")
        self.assertEqual(w._current_palette.bg.lower(), "#14181a")

    def test_theme_change_after_unsaved_accent_preserves_accent(self):
        """Switching theme should keep the user's unsaved accent tweak visible."""
        w = self._make_window()
        w._on_accent_changed("#FF6B35", "")  # light accent tweak, not persisted
        # After accent tweak the stored light accent should be remembered.
        self.assertEqual(w._current_accent.lower(), "#ff6b35")
        w._on_theme_changed("dark")
        # The new light accent must still be remembered for later use.
        self.assertEqual(w._current_accent.lower(), "#ff6b35")


if __name__ == "__main__":
    unittest.main(verbosity=2)
