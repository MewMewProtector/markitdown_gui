"""
Extract embedded raster images from Office Open XML documents.

DOCX / PPTX / XLSX are just ZIP containers — the actual image binaries
live in `word/media/`, `ppt/media/`, and `xl/media/` respectively. Using
`zipfile` (stdlib) keeps the dependency surface flat: no python-docx,
python-pptx, or openpyxl required.

The caller owns the destination directory — typically a freshly created
`tempfile.mkdtemp()` directory that will be deleted in a `finally` block
after the LLM descriptions are produced. This module never deletes
anything itself.
"""
from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from typing import List

# Where each format stores its media.
_MEDIA_PATHS = {
    ".docx": "word/media/",
    ".pptx": "ppt/media/",
    ".xlsx": "xl/media/",
}

# Extensions we accept for description. Other Office media (emf, wmz)
# are skipped — LLM APIs don't understand them.
_ACCEPTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# Minimum dimensions (in bytes) to bother describing. Anything smaller is
# almost certainly a bullet / icon / accent graphic.
_MIN_BYTES = 256


@dataclass(frozen=True)
class OfficeImage:
    file_path: str   # absolute path on disk
    source_name: str # original entry name inside the archive (e.g. "word/media/image1.png")
    size_bytes: int


def is_office_ext(ext: str) -> bool:
    return ext.lower() in _MEDIA_PATHS


def extract_office_images(office_path: str, out_dir: str) -> List[OfficeImage]:
    """
    Extract every embedded image from a DOCX/PPTX/XLSX into `out_dir`.

    The directory must exist beforehand. Filenames are namespaced by
    the source archive entry to avoid collisions across documents.
    Returns an empty list if the archive contains no embedded images
    or is not a recognised Office format.
    """
    _, ext = os.path.splitext(office_path)
    ext = ext.lower()
    if ext not in _MEDIA_PATHS:
        return []

    if not os.path.isfile(office_path):
        raise FileNotFoundError(office_path)
    os.makedirs(out_dir, exist_ok=True)

    media_prefix = _MEDIA_PATHS[ext]
    out: List[OfficeImage] = []

    with zipfile.ZipFile(office_path, "r") as z:
        for entry in z.namelist():
            if not entry.startswith(media_prefix):
                continue
            if entry.endswith("/"):  # directory entry
                continue
            entry_ext = os.path.splitext(entry)[1].lower()
            if entry_ext not in _ACCEPTED_EXTS:
                continue
            try:
                info = z.getinfo(entry)
            except KeyError:
                continue
            if info.file_size < _MIN_BYTES:
                continue
            base = os.path.basename(entry)
            # Avoid collisions if two images share a basename.
            out_name = f"{ext.lstrip('.')}_{base}"
            dest = os.path.join(out_dir, out_name)
            counter = 1
            while os.path.exists(dest):
                stem, e = os.path.splitext(base)
                dest = os.path.join(out_dir, f"{ext.lstrip('.')}_{stem}_{counter}{e}")
                counter += 1
            try:
                with z.open(entry, "r") as src, open(dest, "wb") as dst:
                    data = src.read()
                    dst.write(data)
            except (zipfile.BadZipFile, OSError):
                continue
            out.append(
                OfficeImage(
                    file_path=dest,
                    source_name=entry,
                    size_bytes=len(data),
                )
            )
    return out
