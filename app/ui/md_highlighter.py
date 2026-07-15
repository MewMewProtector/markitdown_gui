"""
Lightweight Markdown syntax highlighter for `QPlainTextEdit`.
Covers headers, bold, italic, inline code, code blocks, links, lists, and
block quotes. Colours are resolved at highlight time via the active palette.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)


class MarkdownHighlighter(QSyntaxHighlighter):
    """Rule-based Markdown highlighter."""

    def __init__(self, parent: QTextDocument | None = None):
        super().__init__(parent)
        self._palette = {
            "header": QColor("#2EA86A"),
            "bold": QColor("#1A1F23"),
            "bold_accent": QColor("#2EA86A"),
            "italic": QColor("#5C646B"),
            "code": QColor("#D9534F"),
            "code_bg": QColor("#EEF1F4"),
            "link": QColor("#2A8BD3"),
            "list": QColor("#43C97A"),
            "quote": QColor("#9099A1"),
            "rule": QColor("#9099A1"),
        }

    # ------------------------------------------------------------------
    def set_palette(self, palette: dict[str, QColor]) -> None:
        self._palette.update(palette)
        self.rehighlight()

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt API
        p = self._palette

        # ATX headers: `# … ###### `
        for m in re.finditer(r"^(#{1,6})\s+.*$", text):
            fmt = QTextCharFormat()
            fmt.setForeground(p["header"])
            fmt.setFontWeight(QFont.Weight.Bold)
            self.setFormat(m.start(), m.end() - m.start(), fmt)

        # Code fences: ``` lang ... ```
        if text.lstrip().startswith("```"):
            fmt = QTextCharFormat()
            fmt.setForeground(p["code"])
            fmt.setBackground(p["code_bg"])
            self.setFormat(0, len(text), fmt)
            return

        # Horizontal rules
        if re.match(r"^\s*([-*_])\1{2,}\s*$", text):
            fmt = QTextCharFormat()
            fmt.setForeground(p["rule"])
            self.setFormat(0, len(text), fmt)
            return

        # Block quote
        if text.lstrip().startswith(">"):
            fmt = QTextCharFormat()
            fmt.setForeground(p["quote"])
            fmt.setFontItalic(True)
            self.setFormat(0, len(text), fmt)

        # Inline code: `…`
        for m in re.finditer(r"`([^`\n]+)`", text):
            fmt = QTextCharFormat()
            fmt.setForeground(p["code"])
            fmt.setBackground(p["code_bg"])
            self.setFormat(m.start(), m.end() - m.start(), fmt)

        # Bold: **…** / __…__. Apply only to the inner text so the
        # surrounding asterisks/underscores stay readable in the base
        # colour, and use the theme accent so the bold style is visually
        # obvious even when the active monospace font has a subtle bold.
        bold_color = p.get("bold_accent", p["bold"])
        for m in re.finditer(r"(\*\*|__)(.+?)\1", text):
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold)
            fmt.setForeground(bold_color)
            inner_start = m.start(2)
            inner_end = m.end(2)
            self.setFormat(inner_start, inner_end - inner_start, fmt)

        # Italic: *…* / _…_. Apply only to the inner text so the
        # surrounding asterisks/underscores stay in the base colour,
        # and combine italic style with the muted colour so the result
        # is legible in both light and dark themes.
        for m in re.finditer(
            r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)",
            text,
        ):
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            if inner is None:
                continue
            inner_start = m.start() + (m.group(0).find(inner))
            fmt = QTextCharFormat()
            fmt.setFontItalic(True)
            fmt.setForeground(p["italic"])
            self.setFormat(inner_start, len(inner), fmt)

        # Links: [text](url) and bare URLs
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
            fmt = QTextCharFormat()
            fmt.setForeground(p["link"])
            fmt.setFontUnderline(True)
            self.setFormat(m.start(), m.end() - m.start(), fmt)

        # List bullets: "- " / "* " / "+ " / "1. "
        for m in re.finditer(r"^\s*([-*+]|\d+\.)\s+", text):
            fmt = QTextCharFormat()
            fmt.setForeground(p["list"])
            fmt.setFontWeight(QFont.Weight.Bold)
            self.setFormat(m.start(), m.end() - m.start(), fmt)