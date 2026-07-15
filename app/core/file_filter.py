"""
Supported file extensions and URL detection helpers.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

SUPPORTED_EXTS: set[str] = {
    "pdf",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "html",
    "htm",
    "md",
    "markdown",
    "csv",
    "json",
    "xml",
    "txt",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "bmp",
    "webp",
    "tiff",
    "tif",
    "mp3",
    "wav",
    "m4a",
    "epub",
    "zip",
    "msg",
    "eml",
}


def extension_of(path: str) -> str:
    _, ext = os.path.splitext(path)
    return ext.lstrip(".").lower()


def is_supported(path: str) -> bool:
    return extension_of(path) in SUPPORTED_EXTS


def is_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def is_http_url(text: str) -> bool:
    return is_url(text)


def filter_supported(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split a list of paths into (supported, unsupported)."""
    supported: list[str] = []
    unsupported: list[str] = []
    for p in paths:
        if is_supported(p):
            supported.append(p)
        else:
            unsupported.append(p)
    return supported, unsupported


def expand_paths(paths: list[str]) -> list[str]:
    """
    Expand a list of paths. If a path is a directory, walk it recursively
    and return all regular files. URLs are passed through unchanged.
    """
    result: list[str] = []
    for p in paths:
        if is_url(p):
            result.append(p)
            continue
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    result.append(os.path.join(root, f))
        elif os.path.isfile(p):
            result.append(p)
    return result