"""
Theme palette + QStyleSheet for the application.

Light/dark tokens, with an optional custom accent color. The accent is
overridable so the user can change the main color scheme beyond the
default green — see Settings → "Цвет акцента".
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    danger: str
    code_bg: str


# ---------------------------------------------------------------------------
# Default accent palettes
# ---------------------------------------------------------------------------
DEFAULT_LIGHT_ACCENT = "#2EA86A"
DEFAULT_DARK_ACCENT = "#43C97A"


LIGHT = Palette(
    bg="#F7F8FA",
    surface="#FFFFFF",
    border="#E1E5EA",
    text="#1A1F23",
    text_muted="#5C646B",
    accent=DEFAULT_LIGHT_ACCENT,
    accent_hover="#24985A",
    danger="#D9534F",
    code_bg="#EEF1F4",
)

DARK = Palette(
    bg="#14181A",
    surface="#1E2326",
    border="#2A2F33",
    text="#E6EAEE",
    text_muted="#9099A1",
    accent=DEFAULT_DARK_ACCENT,
    accent_hover="#52D885",
    danger="#E26965",
    code_bg="#262B2F",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize_hex(value: str) -> str:
    """Return a `#rrggbb` string, or empty if invalid."""
    if not value:
        return ""
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    color = QColor(v)
    if not color.isValid():
        return ""
    return color.name()


def derive_hover(hex_color: str) -> str:
    """Return a slightly darker / brighter hover variant of a hex color."""
    c = QColor(hex_color)
    if not c.isValid():
        return hex_color
    # If the color is dark, lighten it; otherwise darken it. Keep contrast.
    h = c.lightnessF()
    if h < 0.55:
        # make hover brighter
        factor = 1.18
        r = min(255, int(c.red() * factor))
        g = min(255, int(c.green() * factor))
        b = min(255, int(c.blue() * factor))
    else:
        # make hover darker
        factor = 0.82
        r = int(c.red() * factor)
        g = int(c.green() * factor)
        b = int(c.blue() * factor)
    return QColor(r, g, b).name()


def with_custom_accent(p: Palette, accent: str) -> Palette:
    """Return a copy of `p` with `accent` and a derived hover color."""
    if not accent:
        return p
    return replace(p, accent=accent, accent_hover=derive_hover(accent))


def palette_for(
    mode: str,
    accent_override: str | None = None,
    accent_dark_override: str | None = None,
) -> Palette:
    """
    Resolve the active palette.

    `accent_override` applies in light mode (and inside dark mode if
    `accent_dark_override` is empty).
    """
    if mode == "dark":
        base = DARK
        accent = _normalize_hex(accent_dark_override or "") or _normalize_hex(accent_override or "") or base.accent
    elif mode == "light":
        base = LIGHT
        accent = _normalize_hex(accent_override or "") or base.accent
    else:
        # system
        app = QApplication.instance()
        is_dark = app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        if is_dark:
            base = DARK
            accent = _normalize_hex(accent_dark_override or "") or _normalize_hex(accent_override or "") or base.accent
        else:
            base = LIGHT
            accent = _normalize_hex(accent_override or "") or base.accent
    return with_custom_accent(base, accent)


def _qt_palette(p: Palette) -> QPalette:
    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(p.bg))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Base, QColor(p.surface))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(p.bg))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Text, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Button, QColor(p.surface))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.text_muted))
    return qp


def stylesheet_for(p: Palette) -> str:
    # Pick a readable text color for the accent button. White on the
    # default green works; for very light accents, fall back to near-black.
    accent_text = _best_contrast_text(p.accent)
    # Title-bar buttons: derive tinted backgrounds that stand out from
    # the title-bar bg in both themes. In dark mode `bg` and `surface`
    # are very close (e.g. #14181A vs #1E2326), so we blend hard toward
    # `surface` and even past it on hover to keep the buttons clearly
    # visible against the title-bar background.
    is_dark_bg = QColor(p.bg).lightnessF() < 0.5
    # Window controls need stronger separation than ordinary surface cards:
    # use the foreground colour as the blend target so the dark-theme result
    # is not another barely different shade of grey.
    title_btn_bg = _blend(p.bg, p.text, 0.12 if is_dark_bg else 0.08)
    title_btn_border = _blend(p.bg, p.text, 0.28 if is_dark_bg else 0.18)
    title_btn_hover_bg = _blend(p.bg, p.accent, 0.34 if is_dark_bg else 0.18)
    title_btn_pressed_bg = _blend(p.bg, p.accent, 0.24 if is_dark_bg else 0.12)
    accent_soft = _blend(p.surface, p.accent, 0.14 if is_dark_bg else 0.10)
    accent_softer = _blend(p.surface, p.accent, 0.07 if is_dark_bg else 0.05)
    danger_soft = _blend(p.surface, p.danger, 0.12)
    return f"""
    QWidget {{
        color: {p.text};
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QMainWindow, QDialog {{
        background-color: {p.bg};
    }}
    QFrame#Surface, QWidget#Surface, QFrame#Card, QWidget#Card {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QWidget#ContentArea {{
        background-color: {p.bg};
    }}
    QFrame#Navigation {{
        background-color: {p.surface};
        border: 0;
        border-right: 1px solid {p.border};
    }}
    QLabel#NavLogo {{
        background-color: {p.accent};
        color: {accent_text};
        border-radius: 10px;
        font-size: 13pt;
        font-weight: 700;
    }}
    QLabel#NavBrand {{
        font-size: 11pt;
        font-weight: 700;
    }}
    QLabel#NavSection {{
        color: {p.text_muted};
        font-size: 8pt;
        font-weight: 700;
        padding: 0 8px 5px 8px;
    }}
    QPushButton#NavButton {{
        background: transparent;
        color: {p.text_muted};
        border: 0;
        border-radius: 9px;
        padding: 10px 12px;
        min-height: 22px;
        text-align: left;
        font-weight: 500;
    }}
    QPushButton#NavButton:hover {{
        background-color: {accent_softer};
        color: {p.text};
    }}
    QPushButton#NavButton:checked {{
        background-color: {accent_soft};
        color: {p.accent};
        font-weight: 650;
    }}
    QFrame#NavStatus {{
        background-color: {p.code_bg};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    QFrame#NavStatus[active="true"] {{
        background-color: {accent_soft};
        border-color: {p.accent};
    }}
    QLabel#NavStatusTitle {{
        background: transparent;
        font-weight: 650;
    }}
    QLabel#PageTitle {{
        font-family: "Segoe UI";
        font-size: 20pt;
        font-weight: 700;
        padding: 0;
    }}
    QLabel#PageSubtitle {{
        color: {p.text_muted};
        font-size: 10pt;
        padding-bottom: 2px;
    }}
    QFrame#DropZone {{
        background-color: {accent_softer};
        border: 1px dashed {p.accent};
        border-radius: 14px;
    }}
    QLabel#DropTitle {{
        color: {p.text};
        font-size: 12pt;
        font-weight: 650;
        background: transparent;
    }}
    QLabel#StatusBadge {{
        background-color: {p.code_bg};
        border: 1px solid {p.border};
        border-radius: 10px;
        padding: 3px 10px;
        font-weight: 600;
    }}
    QLabel#SectionTitle {{
        font-size: 12pt;
        font-weight: 700;
        background: transparent;
    }}
    QLabel#SectionSubtitle {{
        color: {p.text_muted};
        background: transparent;
    }}
    QFrame#ConnectionStatus {{
        background-color: {p.code_bg};
        border: 1px solid {p.border};
        border-radius: 8px;
    }}
    QPushButton#SegmentButton {{
        border-radius: 7px;
        padding: 5px 11px;
    }}
    QPushButton#SegmentButton:checked {{
        background-color: {accent_soft};
        color: {p.accent};
        border-color: {p.accent};
        font-weight: 650;
    }}
    QTabWidget::pane {{
        border: 0;
        background: transparent;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {p.text_muted};
        padding: 8px 18px;
        margin-right: 4px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        font-weight: 500;
    }}
    QTabBar::tab:hover {{
        color: {p.text};
    }}
    QTabBar::tab:selected {{
        color: {p.accent};
        background: {p.surface};
        border: 1px solid {p.border};
        border-bottom: 0;
    }}
    QPushButton {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 7px 14px;
        min-height: 20px;
    }}
    QPushButton:hover {{
        border-color: {p.accent};
        color: {p.accent};
    }}
    QPushButton:pressed {{
        background-color: {p.code_bg};
    }}
    QPushButton#Primary {{
        background-color: {p.accent};
        color: {accent_text};
        border: 1px solid {p.accent};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{
        background-color: {p.accent_hover};
        border-color: {p.accent_hover};
        color: {accent_text};
    }}
    QPushButton#Danger {{
        color: {p.danger};
        border-color: {p.danger};
    }}
    QPushButton#Danger:hover {{
        background-color: {p.danger};
        color: #FFFFFF;
    }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QListView, QTreeView, QTableView {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px 9px;
        selection-background-color: {p.accent};
        selection-color: #FFFFFF;
    }}
    QPlainTextEdit, QTextEdit {{
        font-family: "Consolas";
        font-size: 10pt;
    }}
    QHeaderView::section {{
        background-color: {p.surface};
        color: {p.text_muted};
        padding: 6px;
        border: 0;
        border-bottom: 1px solid {p.border};
        font-weight: 600;
    }}
    QTableView {{
        gridline-color: {p.border};
        alternate-background-color: {p.bg};
    }}
    QTreeView::item, QTableView::item {{
        min-height: 28px;
        border: 0;
    }}
    QTreeView::item:selected, QTableView::item:selected {{
        background-color: {accent_soft};
        color: {p.text};
    }}
    QCheckBox {{
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {p.border};
        background: {p.surface};
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent};
    }}
    QStatusBar, QMenuBar {{
        background: transparent;
        color: {p.text_muted};
    }}
    QToolTip {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.border};
        border-radius: 5px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p.text_muted};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QLabel#Muted {{
        color: {p.text_muted};
        background: transparent;
    }}
    QLabel#EmptyHint {{
        color: {p.text_muted};
        background: transparent;
        font-size: 11pt;
        padding: 12px;
    }}
    QLabel#TitleBarApp {{
        font-weight: 600;
    }}
    QFrame#Toast, QFrame#ToastError, QFrame#ToastCenter {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-left: 4px solid {p.accent};
        border-radius: 8px;
    }}
    QFrame#ToastError {{
        border-left-color: {p.danger};
        background-color: {danger_soft};
    }}
    QFrame#ToastCenter {{
        background-color: {p.surface};
        /* Thick accent border so the confirmation toast is unmistakably
           linked to the user's chosen accent color. */
        border: 2px solid {p.accent};
        border-radius: 20px;
        padding: 2px;
    }}
    QFrame#ToastCenter QLabel {{
        color: {p.text};
        font-weight: 600;
        padding-left: 4px;
        padding-right: 8px;
    }}
    QFrame#TitleBar {{
        background-color: {p.bg};
        border-bottom: 1px solid {p.border};
    }}
    /* Title-bar buttons need to clearly contrast with the title-bar
       background in BOTH light and dark themes. We give them a subtle
       pill background that is one step lighter/darker than the title
       bar bg, and an icon colour close to `text` so the buttons are
       always legible. The hover state lifts further, and Close uses
       the theme's `danger` colour on hover for an unmistakable cue. */
    QPushButton#TitleButton {{
        background-color: {title_btn_bg};
        border: 1px solid {title_btn_border};
        border-radius: 6px;
        padding: 4px 10px;
        color: {p.text};
        font-size: 11pt;
    }}
    QPushButton#TitleButton:hover, QPushButton#TitleButtonClose:hover {{
        background-color: {title_btn_hover_bg};
        border-color: {p.accent};
        color: {p.accent};
    }}
    QPushButton#TitleButton:pressed, QPushButton#TitleButtonClose:pressed {{
        background-color: {title_btn_pressed_bg};
        color: {p.text};
    }}
    QPushButton#TitleButtonClose {{
        background-color: {title_btn_bg};
        border: 1px solid {title_btn_border};
        border-radius: 6px;
        padding: 4px 10px;
        color: {p.text};
        font-size: 11pt;
    }}
    QPushButton#TitleButtonClose:hover {{
        color: #FFFFFF;
        background-color: {p.danger};
        border-color: {p.danger};
    }}
    """


def _blend(hex_a: str, hex_b: str, t: float) -> str:
    """
    Linearly blend two hex colors in sRGB space.

    `t` is clamped to [0, 1]: 0 returns hex_a unchanged, 1 returns hex_b.
    Used to derive subtle background tints that contrast with the title bar.
    """
    a = QColor(hex_a)
    b = QColor(hex_b)
    if not (a.isValid() and b.isValid()):
        return hex_a
    t = max(0.0, min(1.0, float(t)))
    r = round(a.red() + (b.red() - a.red()) * t)
    g = round(a.green() + (b.green() - a.green()) * t)
    bl = round(a.blue() + (b.blue() - a.blue()) * t)
    return QColor(int(r), int(g), int(bl)).name()


def _best_contrast_text(bg_hex: str) -> str:
    """Return `#FFFFFF` or `#1A1F23` depending on which has higher contrast with `bg_hex`."""
    bg = QColor(bg_hex)
    if not bg.isValid():
        return "#FFFFFF"
    # Compute relative luminance (WCAG).
    def lum(c: QColor) -> float:
        def chan(v: int) -> float:
            x = v / 255.0
            return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * chan(c.red()) + 0.7152 * chan(c.green()) + 0.0722 * chan(c.blue())
    l_bg = lum(bg)
    l_white = 1.0
    l_dark = 0.04
    # Contrast ratio = (L1 + 0.05) / (L2 + 0.05)
    c_white = (l_white + 0.05) / (l_bg + 0.05)
    c_dark = (l_bg + 0.05) / (l_dark + 0.05)
    return "#FFFFFF" if c_white >= c_dark else "#1A1F23"


def apply_theme(
    app: QApplication,
    mode: str,
    accent: str | None = None,
    accent_dark: str | None = None,
) -> Palette:
    p = palette_for(mode, accent_override=accent, accent_dark_override=accent_dark)
    app.setPalette(_qt_palette(p))
    app.setStyleSheet(stylesheet_for(p))
    return p


# Re-export the original helpers other modules might import.
__all__ = [
    "Palette",
    "LIGHT",
    "DARK",
    "DEFAULT_LIGHT_ACCENT",
    "DEFAULT_DARK_ACCENT",
    "palette_for",
    "apply_theme",
    "stylesheet_for",
    "with_custom_accent",
    "derive_hover",
]  # noqa: WPS425
