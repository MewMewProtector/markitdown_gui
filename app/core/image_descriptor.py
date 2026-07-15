"""
Per-image LLM description with SQLite caching.

The actual conversion flow still goes through markitdown's `ImageConverter`
(because it already produces the `# Description:` block the user expects and
handles EXIF metadata). This module exists so the cache lookup logic can be
exercised in isolation — see `tests/test_image_descriptor.py` — and reused by
future pre-conversion steps that want to render descriptions without paying
the markitdown overhead.

Cache key: `SHA256(image_bytes) + model + prompt`.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from datetime import datetime
from typing import Any, Mapping

from . import config


# A modest default prompt; matches markitdown's fallback wording closely.
DEFAULT_PROMPT = "Describe this image in detail."

# Default model used when the user hasn't set one.
DEFAULT_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_image_file(path: str) -> bool:
    """Return True for the L1 image extensions the plan covers."""
    if not path or path.lower().startswith(("http://", "https://")):
        return False
    _, ext = os.path.splitext(path)
    return ext.lower().lstrip(".") in {"jpg", "jpeg", "png"}


def compute_hash(path: str) -> str:
    """SHA-256 of the file's bytes (streamed; safe for large images)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _resolve_model(settings: Mapping[str, str]) -> str:
    return (settings.get("llm_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _resolve_prompt(settings: Mapping[str, str]) -> str:
    return (settings.get("llm_prompt") or DEFAULT_PROMPT).strip() or DEFAULT_PROMPT


def _data_uri(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# Cache lookup (no LLM call)
# ---------------------------------------------------------------------------


def get_cached(path: str, settings: Mapping[str, str]) -> str | None:
    """Look up a description in the cache only. Returns None on miss/error."""
    if not os.path.isfile(path):
        return None
    try:
        hash_key = compute_hash(path)
    except OSError:
        return None
    return config.get_cached_description(
        hash_key, _resolve_model(settings), _resolve_prompt(settings)
    )


# ---------------------------------------------------------------------------
# LLM invocation
# ---------------------------------------------------------------------------


def _make_openai_client(settings: Mapping[str, str]) -> Any:
    """Create an OpenAI-compatible client mirroring `Converter._make_llm_client`."""
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None
    api_key = settings.get("llm_api_key") or ""
    if not api_key:
        return None
    base_url = settings.get("llm_base_url") or None
    try:
        return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    except Exception:
        return None


def _call_llm(
    client: Any,
    model: str,
    prompt: str,
    data_uri: str,
    timeout: float = 60.0,
) -> str | None:
    """Invoke the chat.completions endpoint the same way markitdown does."""
    try:
        # The SDK accepts `timeout=` on `.create()` in openai>=1.x.
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            timeout=timeout,
        )
    except Exception:
        return None
    try:
        return (response.choices[0].message.content or "").strip() or None
    except (AttributeError, IndexError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def describe_image_via_cache(
    path: str,
    settings: Mapping[str, str],
    *,
    force_refresh: bool = False,
) -> str | None:
    """
    Return an LLM-generated description for an image, using the cache.

    Returns ``None`` if the file is missing, no API key is configured, the
    call fails, or the response is empty. Never raises — callers can treat
    ``None`` as "fall back to a placeholder block".
    """
    if not os.path.isfile(path):
        return None

    model = _resolve_model(settings)
    prompt = _resolve_prompt(settings)

    try:
        hash_key = compute_hash(path)
    except OSError:
        return None

    if not force_refresh:
        cached = config.get_cached_description(hash_key, model, prompt)
        if cached:
            return cached

    if not settings.get("llm_api_key"):
        return None

    client = _make_openai_client(settings)
    if client is None:
        return None

    try:
        data_uri = _data_uri(path)
    except OSError:
        return None

    description = _call_llm(client, model, prompt, data_uri)
    if description:
        config.store_description(hash_key, model, prompt, description, _now_iso())
    return description
