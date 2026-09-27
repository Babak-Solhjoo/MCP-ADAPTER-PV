"""Model lists per provider: a static fallback plus a live query of the provider's models endpoint."""
from __future__ import annotations

import re
import time
from typing import Any

STATIC_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-fable-5",
        "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
    ],
    "openai": [
        "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "gpt-4o", "gpt-4o-mini", "o3", "o3-mini", "o4-mini",
    ],
}

# OpenAI lists every model of the account; keep the ones usable for chat/tool calling.
_OPENAI_KEEP = re.compile(r"^(gpt-|o\d)")
_OPENAI_DROP = re.compile(r"(embedding|tts|whisper|dall-e|moderation|realtime|audio|image|transcri|search|instruct|"
                          r"davinci|babbage|codex|computer-use|preview-\d{4}|-\d{4}-\d{2}-\d{2}$)")

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_SECONDS = 600


def _rank(model: str) -> tuple[int, str]:
    order = ["gpt-5", "gpt-4.1", "gpt-4o", "o4", "o3", "o1", "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5",
             "claude-fable-5", "claude-opus-4", "claude-sonnet-4", "claude-haiku"]
    for i, prefix in enumerate(order):
        if model.startswith(prefix):
            return (i, model)
    return (len(order), model)


def filter_openai_models(ids: list[str]) -> list[str]:
    keep = [m for m in ids if _OPENAI_KEEP.match(m) and not _OPENAI_DROP.search(m)]
    return sorted(set(keep), key=_rank)


def _live_openai(base_url: str | None, timeout: float) -> list[str]:
    from openai import OpenAI

    from .openai_provider import client_kwargs

    kwargs: dict[str, Any] = {"timeout": timeout, "max_retries": 0, **client_kwargs(base_url)}
    client = OpenAI(**kwargs)
    return filter_openai_models([m.id for m in client.models.list().data])


def _live_anthropic(timeout: float) -> list[str]:
    import anthropic

    client = anthropic.Anthropic(timeout=timeout, max_retries=0)
    ids = [m.id for m in client.models.list(limit=100)]
    return sorted(set(ids), key=_rank)


def list_models(provider: str, base_url: str | None = None, refresh: bool = False,
                timeout: float = 8.0) -> dict[str, Any]:
    """Return {"models": [...], "source": "live"|"static", "error": optional} for *provider*.

    The live query needs the provider's SDK and API key in the environment; otherwise the static list is used.
    Results are cached for a few minutes; ``refresh=True`` bypasses the cache.
    """
    key = f"{provider}|{base_url or ''}"
    now = time.time()
    if not refresh and key in _CACHE and now - _CACHE[key][0] < CACHE_SECONDS:
        return _CACHE[key][1]
    static = list(STATIC_MODELS.get(provider, []))
    result: dict[str, Any]
    try:
        live = _live_openai(base_url, timeout) if provider == "openai" else _live_anthropic(timeout)
        if live:
            result = {"models": live, "source": "live"}
        else:
            result = {"models": static, "source": "static", "error": "the provider returned no models"}
    except Exception as exc:  # no SDK, no key, no network - fall back to the static list
        result = {"models": static, "source": "static", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    _CACHE[key] = (now, result)
    return result
