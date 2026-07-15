"""
Naming helpers for output `.md` files with auto-increment on collision.
"""
from __future__ import annotations

import os


def md_basename(source_path: str) -> str:
    """`report.pdf` -> `report`"""
    base = os.path.basename(source_path)
    stem, _ext = os.path.splitext(base)
    if not stem:
        return base
    return stem


def build_output_path(source_path: str, output_folder: str | None) -> str:
    """
    Build the destination `.md` path for `source_path`.
    If `output_folder` is empty / None, place the file next to the source.
    On collision, append `_1`, `_2`, ...
    """
    stem = md_basename(source_path)
    if output_folder and output_folder.strip():
        folder = output_folder
    else:
        folder = os.path.dirname(source_path)
    candidate = os.path.join(folder, stem + ".md")
    return _dedupe(candidate)


def _dedupe(path: str) -> str:
    if not os.path.exists(path):
        return path
    folder, fname = os.path.split(path)
    stem, ext = os.path.splitext(fname)
    i = 1
    while True:
        candidate = os.path.join(folder, f"{stem}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1