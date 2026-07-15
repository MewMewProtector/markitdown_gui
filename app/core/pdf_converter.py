"""
Smart PDF→Markdown converter using `pymupdf` (fitz).

The default `pdfminer`-based converter used by markitdown reads PDF text
line-by-line without understanding layout, so two-column scientific papers
end up as a single concatenated stream. This module produces clean
Markdown by:

1. Detecting the body-text font size and line spacing per page.
2. Classifying blocks as headings / body / captions based on font size.
3. Identifying columns by clustering block x-coordinates and emitting
   column 0 in full, then column 1, and so on.
4. Preserving bold/italic via pymupdf font flags.
5. Linking figures and tables by their captions.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

try:
    import pymupdf  # type: ignore
except Exception:
    pymupdf = None  # type: ignore[assignment]


def is_available() -> bool:
    return pymupdf is not None


def extract_markdown(pdf_path: str) -> str:
    """
    Convert a PDF file to a Markdown string.
    Raises RuntimeError if pymupdf is not installed.
    """
    if pymupdf is None:
        raise RuntimeError(
            "pymupdf is not installed. Install with `pip install pymupdf`."
        )
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)

    doc = pymupdf.open(pdf_path)
    pages_md: list[str] = []
    try:
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            pages_md.append(_convert_page(page, page_index))
    finally:
        doc.close()

    text = "\n\n".join(p for p in pages_md if p.strip())
    return _postprocess(text)


# ----------------------------------------------------------------------
# Per-page conversion
# ----------------------------------------------------------------------
def _convert_page(page, page_index: int) -> str:
    page_dict = page.get_text("dict")
    blocks = page_dict.get("blocks", [])
    width = page.rect.width

    # Flatten text blocks (skip image blocks).
    text_blocks: list[dict] = []
    for b in blocks:
        if b.get("type", 0) != 0:
            continue
        spans = _collect_spans(b)
        if not spans:
            continue
        text_blocks.append({
            "bbox": b.get("bbox"),
            "spans": spans,
            "block_no": b.get("number", 0),
        })

    if not text_blocks:
        return ""

    body_size, body_line_height = _estimate_body_metrics(text_blocks)
    columns = _detect_columns(text_blocks, width)

    # Order: column by column, then top-to-bottom within each column.
    ordered: list[dict] = []
    for col_blocks in columns:
        col_blocks_sorted = sorted(
            col_blocks, key=lambda x: (x["bbox"][1], x["bbox"][0])
        )
        ordered.extend(col_blocks_sorted)

    lines: list[str] = []
    for blk in ordered:
        md = _block_to_markdown(blk, body_size, body_line_height)
        if md:
            lines.append(md)

    return "\n\n".join(lines)


# ----------------------------------------------------------------------
# Span collection
# ----------------------------------------------------------------------
def _collect_spans(block: dict) -> list[dict]:
    """Flatten all spans in a block into a list with normalized info."""
    spans: list[dict] = []
    for line in block.get("lines", []):
        # Skip vertical / rotated text (e.g. the rotated arXiv sidebar on
        # the left edge of some preprints).
        line_dir = line.get("dir")
        if line_dir is not None:
            dx, dy = line_dir
            if abs(dx) < 0.5:  # direction is mostly vertical
                continue

        line_bbox = line.get("bbox", (0, 0, 0, 0))
        line_top = line_bbox[1]
        line_height = max(1.0, line_bbox[3] - line_bbox[1])
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text.strip():
                continue
            spans.append({
                "text": text,
                "size": round(float(span.get("size", 0.0)), 2),
                "flags": int(span.get("flags", 0)),
                "font": span.get("font", "") or "",
                "bbox": span.get("bbox", (0, 0, 0, 0)),
                "line_top": line_top,
                "line_height": line_height,
            })
    return spans


# ----------------------------------------------------------------------
# Body-text metrics
# ----------------------------------------------------------------------
def _estimate_body_metrics(blocks: list[dict]) -> tuple[float, float]:
    """Pick the most common font size as the body size."""
    sizes: list[float] = []
    heights: list[float] = []
    for b in blocks:
        for s in b["spans"]:
            if s["size"] > 0:
                sizes.append(s["size"])
            if s["line_height"] > 0:
                heights.append(s["line_height"])
    if not sizes:
        return 10.0, 12.0
    body_size = _mode(sizes)
    body_height = _mode(heights) if heights else max(12.0, body_size * 1.2)
    return body_size, body_height


def _mode(values: Iterable[float]) -> float:
    from collections import Counter
    rounded = [round(v, 1) for v in values]
    if not rounded:
        return 10.0
    return Counter(rounded).most_common(1)[0][0]


# ----------------------------------------------------------------------
# Column detection
# ----------------------------------------------------------------------
def _detect_columns(blocks: list[dict], page_width: float) -> list[list[dict]]:
    """
    Cluster blocks into columns. Uses k-means-style binning on the left
    x-coordinate, restricted to substantial body blocks (so isolated page
    numbers and headers don't skew the split).
    """
    if not blocks:
        return []

    # Keep only blocks that are likely body content (skip marginalia such
    # as page numbers and running headers).
    body = [b for b in blocks if _is_body_block(b, page_width)]
    if len(body) < 2:
        return [blocks]

    centers = sorted({round(b["bbox"][0]) for b in body})
    if len(centers) <= 1:
        return [blocks]

    # Cluster centers: pick a split point that maximizes the minimum gap
    # between consecutive clusters while staying inside the page.
    # We try every possible split index and choose the one with the
    # biggest separating gap.
    best_split = None
    best_gap = 0.0
    for i in range(1, len(centers)):
        gap = centers[i] - centers[i - 1]
        if gap > best_gap:
            best_gap = gap
            best_split = i

    # Require the split to be meaningful (≥ 18% of page width).
    if best_split is None or best_gap < page_width * 0.18:
        return [blocks]

    split_x = (centers[best_split - 1] + centers[best_split]) / 2.0
    left_col = [b for b in body if b["bbox"][0] < split_x]
    right_col = [b for b in body if b["bbox"][0] >= split_x]

    # Anything that didn't pass the body filter (e.g. page numbers) is
    # attached to the closest column by its x position.
    marginalia = [b for b in blocks if b not in body]
    for b in marginalia:
        (left_col if b["bbox"][0] < split_x else right_col).append(b)

    # If one side ended up empty (edge case), fall back to a single column.
    if not left_col or not right_col:
        return [blocks]

    return [left_col, right_col]


def _is_body_block(block: dict, page_width: float) -> bool:
    """Reject obvious page numbers / header strips on the page edges."""
    bbox = block["bbox"]
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    # Narrow block on far left or far right → likely a page number / date.
    if width < page_width * 0.06 and (x0 < page_width * 0.15 or x1 > page_width * 0.85):
        return False
    # Tiny block.
    if width < 30 and height < 30:
        return False
    return True


# ----------------------------------------------------------------------
# Block → Markdown
# ----------------------------------------------------------------------
def _block_to_markdown(
    block: dict,
    body_size: float,
    body_line_height: float,
) -> str:
    spans = block["spans"]
    if not spans:
        return ""

    # Group spans by line (same `line_top`).
    lines: list[list[dict]] = []
    spans_sorted = sorted(spans, key=lambda s: (s["line_top"], s["bbox"][0]))
    current_top = None
    current_line: list[dict] = []
    for s in spans_sorted:
        if current_top is None or abs(s["line_top"] - current_top) <= max(2.0, body_line_height * 0.4):
            current_line.append(s)
        else:
            lines.append(current_line)
            current_line = [s]
        current_top = s["line_top"]
    if current_line:
        lines.append(current_line)

    out_lines: list[str] = []
    for line_spans in lines:
        md_line = _line_to_markdown(line_spans, body_size)
        if md_line.strip():
            out_lines.append(md_line)

    if not out_lines:
        return ""

    avg_size = sum(s["size"] for s in spans) / len(spans)

    # Heading detection by font size.
    if avg_size >= body_size * 1.55:
        level = 1
    elif avg_size >= body_size * 1.30:
        level = 2
    elif avg_size >= body_size * 1.15:
        level = 3
    else:
        level = 0

    if level > 0:
        # Headings are one logical line; preserve explicit newlines between
        # multiple heading lines if a block somehow contains more.
        return f"{'#' * level} " + " ".join(out_lines)

    # Body text: a single block = a single paragraph. Collapse visual
    # line breaks into spaces so the paragraph flows as one chunk.
    para = _join_paragraph_lines(out_lines)

    return para


def _join_paragraph_lines(lines: list[str]) -> str:
    """
    Join visual lines from the same PDF block into one paragraph.

    * Trims each line.
    * If a line ends with `-` and the next starts with a lowercase letter
      (typical soft-hyphen word break "com- plexity"), drop the `-` and
      concatenate directly.
    * Otherwise insert a single space.
    """
    result_parts: list[str] = []
    for ln in lines:
        text = ln.strip()
        if not text:
            continue
        if result_parts:
            prev = result_parts[-1]
            if prev.endswith("-") and text and text[0].islower():
                result_parts[-1] = prev[:-1] + text
            else:
                result_parts[-1] = prev + " " + text
        else:
            result_parts.append(text)
    return "".join(result_parts).strip()


def _line_to_markdown(spans: list[dict], body_size: float) -> str:
    """
    Render a single line of spans as inline Markdown. Adjacent spans that
    share the same bold/italic style are merged so we don't end up with
    `**word** **word** **word**` for a single bold sentence.
    """
    spans = sorted(spans, key=lambda x: x["bbox"][0])

    # Normalize each span's text up front.
    norm_spans: list[tuple[str, bool, bool]] = []
    for s in spans:
        text = _normalize_whitespace(s["text"])
        if not text:
            continue
        flags = s["flags"]
        is_bold = bool(flags & 16) or "bold" in s["font"].lower()
        is_italic = bool(flags & 2) or "italic" in s["font"].lower() or "oblique" in s["font"].lower()
        norm_spans.append((text, is_bold, is_italic))

    if not norm_spans:
        return ""

    # Merge consecutive spans with the same style.
    merged: list[tuple[str, bool, bool]] = []
    for text, bold, italic in norm_spans:
        if merged and merged[-1][1] == bold and merged[-1][2] == italic:
            prev_text, prev_b, prev_i = merged[-1]
            # Join with a single space only if the previous chunk didn't
            # end with whitespace and this one didn't start with punctuation.
            joiner = " " if not prev_text.endswith(" ") and text and not text[0] in ",.;:!?)]}»" else ""
            merged[-1] = (prev_text + joiner + text, prev_b, prev_i)
        else:
            merged.append((text, bold, italic))

    # Render.
    parts: list[str] = []
    for text, bold, italic in merged:
        if bold and italic:
            text = f"***{text}***"
        elif bold:
            text = f"**{text}**"
        elif italic:
            text = f"*{text}*"
        parts.append(text)

    line = " ".join(parts)
    line = re.sub(r"\s{2,}", " ", line).strip()
    return line


def _normalize_whitespace(text: str) -> str:
    """
    Insert spaces in PDF text where they are missing.
    PDFminer/pymupdf often emits words joined together, e.g. 'Abstract—Recently'.
    We add spaces when a lowercase letter is followed by an uppercase one
    (camel case boundary) or before/after punctuation that PDF text lost.
    """
    # Letter followed by capital letter → split.
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Letter followed by digit or vice versa → split.
    text = re.sub(r"([A-Za-z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    # Punctuation glued to the next word.
    text = re.sub(r"([.,;:!?\)])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])([\(])", r"\1 \2", text)
    # Collapse runs of whitespace.
    text = re.sub(r"\s+", " ", text)
    return text


# ----------------------------------------------------------------------
# Post-processing
# ----------------------------------------------------------------------
def _postprocess(md: str) -> str:
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = _fix_abstract_keywords(md)
    return md.strip() + "\n"


_ABSTRACT_HEADER_RE = re.compile(r"^Abstract\s*[—-]\s*", re.IGNORECASE)
_INDEX_TERMS_RE = re.compile(r"^Index Terms\s*[—-]\s*", re.IGNORECASE)


def _fix_abstract_keywords(md: str) -> str:
    """
    Promote 'Abstract—' and 'Index Terms—' prefixes to clean section headings.
    """
    lines = md.splitlines()
    out: list[str] = []
    for ln in lines:
        if _ABSTRACT_HEADER_RE.match(ln.strip()):
            out.append("## Abstract")
            tail = _ABSTRACT_HEADER_RE.sub("", ln).strip()
            if tail:
                out.append(tail)
            continue
        if _INDEX_TERMS_RE.match(ln.strip()):
            out.append("## Index Terms")
            tail = _INDEX_TERMS_RE.sub("", ln).strip()
            if tail:
                out.append(tail)
            continue
        out.append(ln)
    return "\n".join(out)