"""
Extract embedded raster images from a PDF using pymupdf.

The caller owns the destination directory — typically a freshly created
`tempfile.mkdtemp()` directory that will be deleted in a `finally` block
after the LLM descriptions are produced. This module never deletes
anything itself.

Public entry point:
    extract_pdf_images(pdf_path, out_dir) -> list[PdfImage]
where each `PdfImage` carries `(page_number, file_path)` so the caller
can group output by page in the markdown.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

try:
    import fitz  # type: ignore  # pymupdf
    _HAVE_FITZ = True
    _IMPORT_ERROR: Exception | None = None
except Exception as _e:  # pragma: no cover - import-time guard
    fitz = None  # type: ignore[assignment]
    _HAVE_FITZ = False
    _IMPORT_ERROR = _e


# Image extensions we actually try to extract. pymupdf itself handles
# arbitrary image types, but we skip tiny icons and unsupported formats.
_EXTS = {".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class PdfImage:
    page_number: int  # 1-based
    file_path: str    # absolute path on disk
    width: int
    height: int


def is_available() -> bool:
    return _HAVE_FITZ


def import_error() -> str | None:
    return None if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


def extract_pdf_images(pdf_path: str, out_dir: str) -> List[PdfImage]:
    """
    Walk every page of `pdf_path` and save each embedded image to `out_dir`.

    The directory must exist beforehand. Filenames are derived from the
    page and image indices so they're stable across runs (`p1_i0.png`,
    `p1_i1.png`, `p2_i0.png`, ...). Duplicate byte-identical images
    referenced multiple times are written only once per page.
    """
    if not _HAVE_FITZ:
        raise RuntimeError(
            "pymupdf is not installed. Install with `pip install pymupdf`."
        )
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)
    os.makedirs(out_dir, exist_ok=True)

    out: List[PdfImage] = []
    doc = fitz.open(pdf_path)
    try:
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            for img_idx, info in enumerate(page.get_images(full=True)):
                xref = info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception:
                    # Corrupted XRef / unsupported colorspace — skip.
                    continue
                try:
                    if pix.n - pix.alpha > 3:  # CMYK or other odd colorspace
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    # Skip absurdly tiny images (likely bullets / separators).
                    if pix.width < 24 or pix.height < 24:
                        continue
                    ext = ".png"
                    out_name = f"p{page_num}_i{img_idx}{ext}"
                    out_path = os.path.join(out_dir, out_name)
                    if pix.alpha:
                        pix.save(out_path)
                    else:
                        # Default RGB save.
                        pix.save(out_path)
                    out.append(
                        PdfImage(
                            page_number=page_num,
                            file_path=out_path,
                            width=pix.width,
                            height=pix.height,
                        )
                    )
                finally:
                    pix = None
    finally:
        doc.close()
    return out
