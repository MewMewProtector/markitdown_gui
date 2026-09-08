"""
Naming helpers for output `.md` files with auto-increment on collision.
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def md_basename(source_path: str) -> str:
    """`report.pdf` -> `report`"""
    parsed = urlparse(source_path.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        base = os.path.basename(unquote(parsed.path.rstrip("/")))
        if not base:
            base = parsed.netloc
        base = _INVALID_FILENAME_CHARS.sub("_", base).strip(" .")
        if not base:
            base = "webpage"
    else:
        base = os.path.basename(source_path)
    stem, _ext = os.path.splitext(base)
    if not stem:
        return base
    return stem


def build_output_path(
    source_path: str,
    output_folder: str | None,
    reserved: set[str] | None = None,
) -> str:
    """
    Build the destination `.md` path for `source_path`.

    * If `output_folder` is empty / None, the file is placed next to the
      source.
    * On collision with an EXISTING file, append `_1`, `_2`, ...
    * If `reserved` is given, paths inside that set also count as taken
      (used to keep concurrent streaming jobs from clobbering each other
      or from clobbering an in-flight manual conversion).
    """
    stem = md_basename(source_path)
    if output_folder and output_folder.strip():
        folder = output_folder
    elif urlparse(source_path.strip()).scheme in {"http", "https"}:
        # A URL has no adjacent local directory. Use the process working
        # directory when the user did not choose an output folder.
        folder = os.getcwd()
    else:
        folder = os.path.dirname(source_path)
    candidate = os.path.join(folder, stem + ".md")
    return _dedupe(candidate, reserved)


def _dedupe(path: str, reserved: set[str] | None = None) -> str:
    # Windows paths are case-insensitive. Compare normalized absolute keys so
    # ``Report.md`` and ``report.md`` cannot be reserved by concurrent jobs.
    taken = {_path_key(p) for p in reserved} if reserved else set()
    if _path_key(path) not in taken and not os.path.exists(path):
        return path
    folder, fname = os.path.split(path)
    stem, ext = os.path.splitext(fname)
    i = 1
    while True:
        candidate = os.path.join(folder, f"{stem}_{i}{ext}")
        if _path_key(candidate) not in taken and not os.path.exists(candidate):
            return candidate
        i += 1


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))
