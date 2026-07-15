"""
Wrapper around `markitdown.MarkItDown` that respects settings (LLM, DI, CU).

PDFs are routed through a custom `pymupdf`-based converter that preserves
multi-column layouts, headings, and inline emphasis — the default
`pdfminer` path used by markitdown concatenates two-column papers into a
single stream of unspaced text.
"""
from __future__ import annotations

import os
from typing import Any

try:
    from markitdown import MarkItDown
except Exception as _e:  # pragma: no cover - import-time guard
    MarkItDown = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = _e
else:
    _IMPORT_ERROR = None

from .file_filter import is_http_url
from .image_descriptor import DEFAULT_MODEL, DEFAULT_PROMPT


def is_available() -> bool:
    return MarkItDown is not None


def import_error() -> str | None:
    return None if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


class Converter:
    """
    Thin wrapper that lazily creates a MarkItDown client with plugin flags
    sourced from settings.

    The instance is built on demand because plugin availability depends on
    the currently installed optional packages.
    """

    def __init__(self, settings: dict[str, str]):
        self._settings = dict(settings)
        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _build(self) -> Any:
        if MarkItDown is None:
            raise RuntimeError(
                "markitdown is not installed. Install with `pip install 'markitdown[all]'`."
            )
        kwargs: dict[str, Any] = {}
        s = self._settings

        enable_plugins = s.get("use_plugins") == "1"
        if enable_plugins:
            kwargs["enable_plugins"] = True

        if s.get("enable_docintel") == "1" and s.get("docintel_endpoint"):
            kwargs["docintel_endpoint"] = s["docintel_endpoint"]

        if s.get("enable_cu") == "1" and s.get("cu_endpoint"):
            kwargs["az_content_understanding_endpoint"] = s["cu_endpoint"]

        # LLM (OpenAI-compatible). Always pass when populated.
        if s.get("llm_api_key"):
            kwargs["llm_client"] = self._make_llm_client(s)

        # When the user opts into image descriptions, also surface the model
        # and prompt to markitdown so its ImageConverter appends a
        # `# Description:` block for `.jpg` / `.jpeg` / `.png` inputs.
        # PDF / DOCX / etc. are unaffected.
        if s.get("describe_images") == "1":
            kwargs["llm_model"] = (s.get("llm_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
            prompt = (s.get("llm_prompt") or DEFAULT_PROMPT).strip()
            if prompt:
                kwargs["llm_prompt"] = prompt

        return MarkItDown(**kwargs)

    def _make_llm_client(self, s: dict[str, str]) -> Any:
        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return None
        base_url = s.get("llm_base_url") or None
        api_key = s["llm_api_key"]
        try:
            return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build()
        return self._client

    def convert(self, path: str) -> str:
        # URLs and non-PDF files use the standard markitdown path.
        if not (is_http_url(path) if path.lower().startswith(("http://", "https://")) else False):
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf" and self._settings.get("pdf_engine", "smart") == "smart":
                from .pdf_converter import extract_markdown, is_available
                if is_available():
                    try:
                        return extract_markdown(path)
                    except Exception:
                        # Fall back to markitdown if smart extraction fails.
                        pass
        result = self.client().convert(path)
        return getattr(result, "text_content", "") or ""