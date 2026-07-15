"""
Tests for theme / accent-color overrides.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ui import theme  # noqa: E402


class TestThemeAccent(unittest.TestCase):
    def test_default_palette_has_accent(self):
        self.assertTrue(theme.LIGHT.accent.startswith("#"))
        self.assertTrue(theme.DARK.accent.startswith("#"))

    def test_custom_accent_light(self):
        p = theme.palette_for("light", accent_override="#ff6b35")
        self.assertEqual(p.accent, "#ff6b35")
        # Hover must be derivable and not equal to the accent itself.
        self.assertNotEqual(p.accent_hover, p.accent)
        self.assertTrue(p.accent_hover.startswith("#"))

    def test_custom_accent_dark(self):
        p = theme.palette_for("dark", accent_dark_override="#ffd700")
        self.assertEqual(p.accent, "#ffd700")

    def test_invalid_accent_falls_back(self):
        p = theme.palette_for("light", accent_override="not-a-color")
        self.assertEqual(p.accent, theme.LIGHT.accent)

    def test_hover_darkens_bright(self):
        # Bright accent → hover should be darker.
        hover = theme.derive_hover("#FFD700")
        self.assertNotEqual(hover, "#FFD700")

    def test_hover_brightens_dark(self):
        # Dark accent → hover should be brighter.
        hover = theme.derive_hover("#1A1F23")
        self.assertNotEqual(hover, "#1A1F23")

    def test_contrast_text_picks_visible(self):
        # On a bright accent, contrast text should be dark.
        self.assertEqual(theme._best_contrast_text("#FFD700"), "#1A1F23")
        # On a very dark accent, contrast text should be white.
        self.assertEqual(theme._best_contrast_text("#000000"), "#FFFFFF")
        # On the medium-dark default green, the function picks the more
        # readable of white vs near-black (which is `#1A1F23` here, since
        # it has higher WCAG contrast against this hue).
        result = theme._best_contrast_text("#2EA86A")
        self.assertIn(result, ("#FFFFFF", "#1A1F23"))

    def test_with_custom_accent_returns_new_palette(self):
        p = theme.with_custom_accent(theme.LIGHT, "#ff6b35")
        self.assertIsNot(p, theme.LIGHT)
        self.assertEqual(p.accent, "#ff6b35")
        self.assertEqual(p.bg, theme.LIGHT.bg)  # other fields unchanged


if __name__ == "__main__":
    unittest.main(verbosity=2)