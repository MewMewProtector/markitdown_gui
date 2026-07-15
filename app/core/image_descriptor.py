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


def _is_openrouter(base_url: str | None) -> bool:
    """True when the configured base URL points at OpenRouter."""
    return bool(base_url) and "openrouter.ai" in base_url.lower()


def _make_openai_client(settings: Mapping[str, str]) -> Any:
    """Create an OpenAI-compatible client mirroring `Converter._make_llm_client`.

    When the base URL is an OpenRouter endpoint, we attach the
    `HTTP-Referer` and `X-Title` headers that OpenRouter requires for
    free-tier models (otherwise the request is rejected with 401/403).

    Returns ``None`` on any failure. Use `_make_openai_client_with_error`
    if you need the underlying exception text.
    """
    client, _err = _make_openai_client_with_error(settings)
    return client


def _make_openai_client_with_error(
    settings: Mapping[str, str],
) -> tuple[Any, str | None]:
    """
    Like `_make_openai_client` but also returns the underlying exception
    text on failure (or ``None`` on success). Used by
    `describe_image_with_error` to surface the real reason in the log.
    """
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        return None, f"не удалось импортировать openai: {exc}"
    api_key = settings.get("llm_api_key") or ""
    if not api_key:
        return None, "API-ключ пустой"
    base_url = settings.get("llm_base_url") or None
    try:
        client = (
            OpenAI(api_key=api_key, base_url=base_url)
            if base_url
            else OpenAI(api_key=api_key)
        )
    except Exception as exc:
        return None, f"OpenAI() упал: {type(exc).__name__}: {exc}"
    # OpenRouter identifies the calling app via these headers. Setting them
    # is harmless when pointing at other providers (the SDK forwards all
    # `default_headers` as request headers).
    if _is_openrouter(base_url):
        try:
            client.default_headers.update({
                "HTTP-Referer": "https://github.com/markitdown-gui",
                "X-Title": "markitdown_gui",
            })
        except Exception as exc:
            # Header attachment failure is non-fatal — the request can
            # still be sent, just might be rejected by OpenRouter.
            pass
    return client, None


def _call_llm(
    client: Any,
    model: str,
    prompt: str,
    data_uri: str,
    timeout: float = 30.0,
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


# Per-image timeout for the LLM call. Kept short on purpose — if a single
# image can't be described in 30s we'd rather move on and let the user
# see the failure than block the whole conversion for minutes.
DEFAULT_LLM_TIMEOUT = 30.0


def _classify_llm_error(exc: BaseException) -> str:
    """
    Map an exception raised by the openai SDK to a short user-friendly
    Russian reason. We rely on the SDK's exception class names — they're
    stable across 1.x.
    """
    name = type(exc).__name__
    cls = name.lower()
    if "timeout" in cls:
        return f"таймаут ({DEFAULT_LLM_TIMEOUT:.0f}с)"
    if "connection" in cls:
        return "ошибка соединения с LLM"
    if "authentication" in cls or "apikey" in cls:
        return "неверный API-ключ"
    if "permission" in cls:
        return "нет доступа к модели"
    if "notfound" in cls or "404" in cls:
        return "модель не найдена (404)"
    if "ratelimit" in cls or "429" in cls:
        return "rate limit exceeded (429)"
    status = getattr(exc, "status_code", None)
    if status is not None:
        msg = getattr(exc, "message", None) or str(exc)
        return f"HTTP {status}: {msg}"
    return f"{name}: {exc}"


def _call_llm_with_error(
    client: Any,
    model: str,
    prompt: str,
    data_uri: str,
    timeout: float = DEFAULT_LLM_TIMEOUT,
) -> tuple[str | None, str | None]:
    """Like `_call_llm` but also returns a short error string on failure."""
    try:
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
    except Exception as exc:
        return None, _classify_llm_error(exc)
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError):
        return None, "empty response (no choices)"
    text = (content or "").strip() or None
    if text is None:
        return None, "empty response content"
    return text, None


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

    For diagnostic-friendly output use `describe_image_with_error` instead.
    """
    description, _ = describe_image_with_error(
        path, settings, force_refresh=force_refresh
    )
    return description


def describe_image_with_error(
    path: str,
    settings: Mapping[str, str],
    *,
    force_refresh: bool = False,
    timeout: float | None = None,
) -> tuple[str | None, str | None]:
    """
    Same as `describe_image_via_cache` but also returns a short reason
    string on failure (empty/None on success). Use this from the converter
    so the user can see WHY a description didn't happen.

    `timeout` overrides the default per-image LLM timeout
    (DEFAULT_LLM_TIMEOUT = 30s). Keep it short — a single image rarely
    needs more than that and a long timeout would freeze the UI when
    the endpoint is unresponsive.
    """
    if not path:
        return None, "empty path"
    if not os.path.isfile(path):
        return None, f"file not found: {path}"

    model = _resolve_model(settings)
    prompt = _resolve_prompt(settings)

    try:
        hash_key = compute_hash(path)
    except OSError as exc:
        return None, f"hash error: {exc}"

    if not force_refresh:
        try:
            cached = config.get_cached_description(hash_key, model, prompt)
        except Exception as exc:
            cached = None
            cache_err: str | None = f"cache read error: {exc}"
        else:
            cache_err = None
        if cached:
            return cached, None

    if not settings.get("llm_api_key"):
        return None, "API-ключ LLM не задан"

    client, client_err = _make_openai_client_with_error(settings)
    if client is None:
        reason = client_err or "не удалось создать OpenAI-клиент"
        return None, reason

    try:
        data_uri = _data_uri(path)
    except OSError as exc:
        return None, f"не удалось прочитать файл: {exc}"

    effective_timeout = (
        float(timeout) if timeout is not None else DEFAULT_LLM_TIMEOUT
    )
    description, err = _call_llm_with_error(
        client, model, prompt, data_uri, timeout=effective_timeout
    )
    if description:
        try:
            config.store_description(
                hash_key, model, prompt, description, _now_iso()
            )
        except Exception:
            pass
        return description, None
    if err is None:
        err = cache_err or "неизвестная ошибка"
    return None, err
