"""Safe Markdown-to-HTML rendering with offline LaTeX/MathML support."""
from __future__ import annotations

import html
import re
from typing import Any

import markdown
from latex2mathml import converter as latex_converter


_CODE_RE = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)",
    re.MULTILINE,
)

_MATH_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (re.compile(r"\$\$([\s\S]+?)\$\$"), True),
    (re.compile(r"\\\[([\s\S]+?)\\\]"), True),
    (re.compile(r"\\\((.+?)\\\)"), False),
    (re.compile(r"(?<!\\)\$(?!\$)([^$\n]+?)(?<!\\)\$"), False),
)


def _protect_code(text: str) -> tuple[str, list[str]]:
    regions: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = f"MARKITDOWNCODETOKEN{len(regions)}END"
        regions.append(match.group(0))
        return token

    return _CODE_RE.sub(replace, text), regions


def _restore_code(text: str, regions: list[str]) -> str:
    for index, region in enumerate(regions):
        text = text.replace(f"MARKITDOWNCODETOKEN{index}END", region)
    return text


def _extract_math(text: str) -> tuple[str, list[str]]:
    rendered: list[str] = []

    for pattern, block in _MATH_PATTERNS:
        def replace(match: re.Match[str], *, is_block: bool = block) -> str:
            latex = match.group(1).strip()
            if not latex:
                return match.group(0)
            try:
                mathml = latex_converter.convert(
                    latex,
                    display="block" if is_block else "inline",
                )
                css_class = "math-block" if is_block else "math-inline"
                fragment = f'<span class="{css_class}">{mathml}</span>'
            except Exception:
                # Keep malformed formulas visible instead of dropping text.
                delimiter = "$$" if is_block else "$"
                fragment = (
                    '<code class="math-error">'
                    f"{delimiter}{html.escape(latex)}{delimiter}</code>"
                )
            token = f"MARKITDOWNMATHTOKEN{len(rendered)}END"
            rendered.append(fragment)
            return token

        text = pattern.sub(replace, text)
    return text, rendered


def _palette_value(palette: Any, name: str, fallback: str) -> str:
    return str(getattr(palette, name, fallback) or fallback)


def render_markdown_document(
    markdown_text: str,
    palette: Any = None,
    *,
    base_url: str = "",
) -> str:
    """Render Markdown into a self-contained, script-free HTML document."""
    protected, code_regions = _protect_code(markdown_text or "")
    protected, math_regions = _extract_math(protected)
    protected = _restore_code(protected, code_regions)

    # Raw HTML from converted documents is shown as text. This keeps the
    # preview script-free even when an input document contains HTML tags.
    safe_markdown = html.escape(protected, quote=False)
    body = markdown.markdown(
        safe_markdown,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    for index, fragment in enumerate(math_regions):
        body = body.replace(f"MARKITDOWNMATHTOKEN{index}END", fragment)

    bg = _palette_value(palette, "surface", "#FFFFFF")
    text = _palette_value(palette, "text", "#1A1F23")
    muted = _palette_value(palette, "text_muted", "#66717A")
    accent = _palette_value(palette, "accent", "#2EA86A")
    border = _palette_value(palette, "border", "#E1E5EA")
    code_bg = _palette_value(palette, "code_bg", "#EEF1F4")
    base_tag = (
        f'<base href="{html.escape(base_url, quote=True)}">'
        if base_url
        else ""
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  {base_tag}
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; img-src file: data: http: https:; style-src 'unsafe-inline'">
  <style>
    :root {{ color-scheme: light dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0 auto; padding: 32px 40px 64px; max-width: 980px;
      background: {bg}; color: {text};
      font: 16px/1.68 "Segoe UI Variable Text", "Segoe UI", sans-serif;
    }}
    h1, h2, h3, h4 {{ line-height: 1.25; margin: 1.5em 0 .55em; }}
    h1 {{ font-size: 2em; border-bottom: 1px solid {border}; padding-bottom: .3em; }}
    h2 {{ font-size: 1.5em; }} h3 {{ font-size: 1.2em; }}
    p, ul, ol, blockquote, table, pre {{ margin: .8em 0; }}
    a {{ color: {accent}; }}
    blockquote {{ margin-left: 0; padding: .2em 1em; color: {muted};
                  border-left: 4px solid {accent}; }}
    code {{ font-family: "Cascadia Code", Consolas, monospace;
            background: {code_bg}; border-radius: 5px; padding: .12em .35em; }}
    pre {{ overflow-x: auto; background: {code_bg}; border: 1px solid {border};
           border-radius: 10px; padding: 14px 16px; }}
    pre code {{ padding: 0; background: transparent; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid {border}; padding: 8px 11px; text-align: left; }}
    th {{ background: {code_bg}; }}
    img {{ max-width: 100%; border-radius: 10px; }}
    .math-inline {{ display: inline-flex; vertical-align: -.18em; margin: 0 .12em; }}
    .math-block {{ display: flex; justify-content: center; overflow-x: auto;
                   margin: 1.1em 0; padding: 14px; background: {code_bg};
                   border: 1px solid {border}; border-radius: 10px; }}
    math {{ font-size: 1.12em; color: {text}; }}
    .math-block math {{ font-size: 1.3em; }}
    .math-error {{ color: #D9534F; }}
  </style>
</head>
<body>{body}</body>
</html>"""


__all__ = ["render_markdown_document"]
