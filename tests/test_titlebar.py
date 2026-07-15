"""Tests for title-bar button contrast in dark and light themes."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtGui import QColor

from app.ui import theme  # noqa: E402


def _button_bg_hex(palette, is_dark):
    if is_dark:
        return theme._blend(palette.bg, palette.surface, 0.85)
    return theme._blend(palette.bg, palette.surface, 0.55)


def _is_dark_bg(palette) -> bool:
    return QColor(palette.bg).lightnessF() < 0.5


class TestTitleBarContrast(unittest.TestCase):
    def test_dark_title_button_bg_differs_from_titlebar_bg(self):
        p = theme.palette_for("dark")
        btn_bg = QColor(_button_bg_hex(p, _is_dark_bg(p)))
        bar_bg = QColor(p.bg)
        self.assertFalse(btn_bg.name() == bar_bg.name(),
                         "Title button bg must differ from title bar bg in dark theme")

    def test_light_title_button_bg_differs_from_titlebar_bg(self):
        p = theme.palette_for("light")
        btn_bg = QColor(_button_bg_hex(p, _is_dark_bg(p)))
        bar_bg = QColor(p.bg)
        self.assertFalse(btn_bg.name() == bar_bg.name(),
                         "Title button bg must differ from title bar bg in light theme")

    def test_dark_stylesheet_uses_p_text_for_icons(self):
        """The icon color used by title buttons must be `p.text` (high contrast)
        rather than `p.text_muted` (which can be too dim on dark themes)."""
        p = theme.palette_for("dark")
        css = theme.stylesheet_for(p)
        # Find the TitleButton block and inspect its color line.
        match = re.search(
            r"QPushButton#TitleButton\s*\{[^}]*color:\s*(#[0-9a-fA-F]+)\s*;",
            css,
        )
        self.assertIsNotNone(match, "TitleButton rule must define a color")
        self.assertEqual(match.group(1).lower(), p.text.lower())

    def test_dark_hover_lifts_button_bg(self):
        p = theme.palette_for("dark")
        css = theme.stylesheet_for(p)
        # Extract background-color values from the TitleButton rules.
        rules = re.findall(
            r"QPushButton#TitleButton[^{]*\{[^}]*background-color:\s*(#[0-9a-fA-F]+)\s*;",
            css,
        )
        self.assertGreaterEqual(len(rules), 2,
                                "Need at least default + hover background values")
        default_bg = QColor(rules[0])
        hover_bg = QColor(rules[1])
        # Hover should be distinct from default.
        self.assertNotEqual(default_bg.name(), hover_bg.name(),
                            "Hover background must differ from default")
        # Hover should be lighter than default in dark mode (so it stands out).
        self.assertGreaterEqual(hover_bg.lightnessF(), default_bg.lightnessF(),
                                "Hover background should be at least as bright as default in dark mode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
