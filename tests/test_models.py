"""Model lists: static fallback, OpenAI filtering/sorting and the UI endpoint."""
from agent_runner import models as m


def test_static_lists_are_complete_enough():
    assert {"claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5"} <= set(m.STATIC_MODELS["anthropic"])
    assert {"gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "o3", "o4-mini"} <= set(m.STATIC_MODELS["openai"])
    assert m.STATIC_MODELS["anthropic"][0] == "claude-opus-5"


def test_filter_openai_models_keeps_chat_models_only():
    raw = ["gpt-4o-mini", "text-embedding-3-large", "gpt-5", "whisper-1", "dall-e-3", "o3", "gpt-4o-realtime-preview",
           "tts-1", "gpt-4.1", "omni-moderation-latest", "gpt-4o-2024-08-06", "o4-mini", "gpt-image-1", "babbage-002"]
    out = m.filter_openai_models(raw)
    assert out[0] == "gpt-5" and out[1] == "gpt-4.1" and "gpt-4o-mini" in out and "o3" in out and "o4-mini" in out
    assert not any(x in out for x in ("text-embedding-3-large", "whisper-1", "dall-e-3", "gpt-4o-realtime-preview",
                                       "tts-1", "omni-moderation-latest", "gpt-4o-2024-08-06", "gpt-image-1", "babbage-002"))


def test_list_models_falls_back_to_static_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    for provider in ("openai", "anthropic"):
        res = m.list_models(provider, refresh=True, timeout=2)
        assert res["source"] == "static" and res["models"] == m.STATIC_MODELS[provider] and res["error"]


def test_list_models_uses_live_query_when_available(monkeypatch):
    monkeypatch.setattr(m, "_live_openai", lambda base_url, timeout: ["gpt-4.1", "gpt-5"])
    res = m.list_models("openai", refresh=True)
    assert res["source"] == "live" and res["models"] == ["gpt-4.1", "gpt-5"]
    monkeypatch.setattr(m, "_live_openai", lambda base_url, timeout: (_ for _ in ()).throw(RuntimeError("down")))
    assert m.list_models("openai", refresh=False)["source"] == "live"  # cached
    res = m.list_models("openai", refresh=True)
    assert res["source"] == "static" and "down" in res["error"]
