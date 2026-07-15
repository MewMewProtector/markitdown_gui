"""
Wrapper around `markitdown.MarkItDown` that respects settings (LLM, DI, CU).

PDFs are routed through a custom `pymupdf`-based converter that preserves
multi-column layouts, headings, and inline emphasis — the default
`pdfminer` path used by markitdown concatenates two-column papers into a
single stream of unspaced text.

Image descriptions (L2/L3): when `describe_images` is enabled and the
input is a PDF / DOCX / PPTX / XLSX, embedded raster images are extracted
into a fresh temporary directory, described via `image_descriptor` (which
honors the SQLite cache), and the descriptions are appended to the
resulting markdown. The temporary directory is removed in a `finally`
block — only our extracted images live there.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Callable, Iterable, List, Tuple, Optional

try:
    from markitdown import MarkItDown
except Exception as _e:  # pragma: no cover - import-time guard
    MarkItDown = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = _e
else:
    _IMPORT_ERROR = None

from .file_filter import is_http_url
from .image_descriptor import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    describe_image_via_cache,
    describe_image_with_error,
    is_image_file,
)


def is_available() -> bool:
    return MarkItDown is not None


def import_error() -> str | None:
    return None if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


# Extensions for which we extract inline images and describe them.
_INLINE_IMAGE_EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}

# Progress mapping for the inline-image phase. After text extraction the
# conversion job has emitted ~25%; we reserve 25..80 for image work and
# 80..100 for writing the markdown to disk. With N images each one gets
# `(80 - 25) / N` percent of the range, so the user sees motion even on
# large PDFs.
_INLINE_PHASE_START = 25
_INLINE_PHASE_END = 80


# A simple callback the caller (ConversionJob) wires to its `progress`
# signal so the user sees motion while we describe images one by one.
ProgressCallback = Callable[[int, str], None]


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
        # Errors collected during the last `convert()` call. Cleared at
        # the start of each call so callers always see the latest run.
        self._last_errors: list[str] = []
        # Optional callback `cb(percent, message)` fired during long
        # operations (inline-image LLM calls). The conversion job uses
        # it to surface per-image progress to the UI.
        self._progress_cb: Optional[ProgressCallback] = None

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
        # PDF / DOCX / etc. are unaffected by markitdown itself — we
        # handle them via `_describe_inline_images` below.
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
        # Reset the per-call diagnostics buffer.
        self._last_errors = []

        # URLs are handled by markitdown directly — no inline-image
        # extraction makes sense for remote URLs.
        if is_http_url(path):
            result = self.client().convert(path)
            return getattr(result, "text_content", "") or ""

        ext = os.path.splitext(path)[1].lower()

        # PDF: prefer the smart pymupdf extractor (better multi-column
        # layout), then optionally append LLM image descriptions.
        if ext == ".pdf" and self._settings.get("pdf_engine", "smart") == "smart":
            from .pdf_converter import extract_markdown, is_available as pdf_available
            if pdf_available():
                try:
                    markdown = extract_markdown(path)
                    if self._should_describe_inline(ext):
                        inline_block, errors = self._describe_inline_pdf(path)
                        if inline_block:
                            markdown += "\n\n" + inline_block
                        if errors:
                            self._last_errors.extend(errors)
                    return markdown
                except Exception:
                    # Fall back to markitdown if smart extraction fails.
                    pass

        # DOCX / PPTX / XLSX (and PDF fallback): markitdown for text,
        # then we extract + describe embedded images ourselves.
        result = self.client().convert(path)
        markdown = getattr(result, "text_content", "") or ""

        if self._should_describe_inline(ext):
            inline_block, errors = self._describe_inline_office(path, ext)
            if inline_block:
                markdown += "\n\n" + inline_block
            if errors:
                self._last_errors.extend(errors)

        return markdown

    @property
    def last_description_errors(self) -> list[str]:
        """
        List of human-readable error strings collected during the most
        recent `convert()` call. Empty list when everything succeeded or
        when descriptions were skipped. Useful for surfacing in the UI.
        """
        return list(self._last_errors)

    def set_progress_callback(self, cb: Optional[ProgressCallback]) -> None:
        """
        Install / clear a progress callback that receives
        `(percent, message)` updates. Use ``None`` to clear. The callback
        is invoked from the worker thread, so the callee (typically the
        QRunnable) is responsible for marshalling onto the GUI thread.
        """
        self._progress_cb = cb

    def _emit_progress(self, percent: int, message: str) -> None:
        if self._progress_cb is None:
            return
        try:
            self._progress_cb(int(percent), str(message))
        except Exception:
            # A buggy callback must never break the conversion.
            pass

    # ------------------------------------------------------------------
    # Inline-image description (L2/L3)
    # ------------------------------------------------------------------
    def _should_describe_inline(self, ext: str) -> bool:
        if ext not in _INLINE_IMAGE_EXTS:
            return False
        if self._settings.get("describe_images") != "1":
            return False
        # No LLM configured → silently skip (per plan's failure-mode note).
        if not self._settings.get("llm_api_key"):
            return False
        return True

    def _describe_inline_pdf(self, path: str) -> tuple[str, list[str]]:
        """
        Extract every embedded image from the PDF, describe each one, and
        return `(markdown_section, errors)`. The temp directory holding
        the extracted PNGs is removed before returning.
        """
        from .pdf_image_extractor import extract_pdf_images, is_available

        if not is_available():
            return "", []
        tmp = tempfile.mkdtemp(prefix="markitdown_gui_pdf_imgs_")
        try:
            try:
                images = extract_pdf_images(path, tmp)
            except Exception as exc:
                return "", [f"не удалось извлечь изображения из PDF: {exc}"]
            # Materialize once so we know the total and can drive the
            # progress callback proportionally.
            image_list = list(images)
            return self._build_inline_markdown(
                ((img.page_number, img.file_path) for img in image_list),
                group_by_page=True,
                total=len(image_list),
            )
        finally:
            # Surgical cleanup: `tmp` was created empty by mkdtemp and only
            # ever contains the PNGs we just wrote. No other files are
            # touched.
            shutil.rmtree(tmp, ignore_errors=True)

    def _describe_inline_office(
        self, path: str, ext: str
    ) -> tuple[str, list[str]]:
        """
        Extract every embedded image from a DOCX/PPTX/XLSX, describe each
        one, and return `(markdown_section, errors)`. The temp directory
        is removed before returning.
        """
        from .office_image_extractor import (
            extract_office_images,
            is_office_ext,
        )

        if not is_office_ext(ext):
            return "", []
        tmp = tempfile.mkdtemp(prefix="markitdown_gui_office_imgs_")
        try:
            try:
                images = extract_office_images(path, tmp)
            except Exception as exc:
                return "", [
                    f"не удалось извлечь изображения из {ext}: {exc}"
                ]
            image_list = list(images)
            return self._build_inline_markdown(
                ((0, img.file_path) for img in image_list),
                group_by_page=False,
                archive_basename=os.path.basename(path),
                total=len(image_list),
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _build_inline_markdown(
        self,
        items: Iterable[Tuple[int, str]],
        *,
        group_by_page: bool,
        archive_basename: str = "",
        total: int = 0,
    ) -> tuple[str, list[str]]:
        """
        Describe each image via the cache-backed helper and assemble a
        markdown section.

        `items` yields `(page_number, file_path)` tuples. When
        `group_by_page` is True (PDF case), descriptions are grouped under
        `## Page N` headings; otherwise they live in a flat list under a
        single section.

        `total` is the count of items to process (used to drive the
        progress callback proportionally). When 0, we fall back to
        materializing the iterable.

        Returns `(markdown_section, errors)` where `errors` carries
        diagnostic strings for the failures (one per failed image, with
        the underlying reason).
        """
        section: List[str] = []
        current_page: int | None = None
        described = 0
        failed = 0
        processed = 0
        errors: List[str] = []

        # Materialize once so we know the total ahead of time.
        items_list = list(items)
        if not total:
            total = len(items_list)

        phase_span = max(1, _INLINE_PHASE_END - _INLINE_PHASE_START)

        def _percent_for(idx: int) -> int:
            """Map 0..total → _INLINE_PHASE_START.._INLINE_PHASE_END."""
            if total <= 0:
                return _INLINE_PHASE_END
            ratio = min(1.0, max(0.0, idx / total))
            return _INLINE_PHASE_START + int(round(ratio * phase_span))

        for page_num, file_path in items_list:
            processed += 1
            # Tell the UI which image we're working on BEFORE making the
            # (possibly slow) LLM call — the user sees motion even when
            # the LLM takes 30s.
            self._emit_progress(
                _percent_for(processed - 1),
                f"описание картинки {processed}/{total}…",
            )
            description, err = describe_image_with_error(
                file_path, self._settings
            )

            if group_by_page and page_num != current_page:
                section.append(f"## Page {page_num}")
                section.append("")
                current_page = page_num

            section.append(
                f"![{os.path.basename(file_path)}]({file_path})"
            )
            section.append("")
            if description:
                section.append(f"> {description}")
                described += 1
            else:
                section.append(
                    "> [не удалось получить описание изображения]"
                )
                failed += 1
                if err:
                    where = (
                        f"стр. {page_num}" if group_by_page else file_path
                    )
                    errors.append(f"{where}: {err}")
            section.append("")

            self._emit_progress(
                _percent_for(processed),
                f"описание картинки {processed}/{total}: "
                f"{'OK' if description else 'ошибка'}",
            )

        if not processed:
            return "", errors

        header = "## Описания изображений"
        if archive_basename:
            header += f" — {archive_basename}"
        section.insert(0, header)
        section.insert(1, "")
        # Append a small summary line so the user can see the cache hit
        # rate when re-running.
        if described or failed:
            section.append(
                f"*Описано: {described}, не удалось: {failed}, всего: {processed}.*"
            )
        return "\n".join(section).rstrip() + "\n", errors


__all__ = ["Converter", "is_available", "import_error", "is_image_file"]
