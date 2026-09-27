"""Chat store hardening: crafted chat files cannot escape the store or inject markup; model ids are validated;
approvals are bound to one call and Stop declines what follows."""
import asyncio
import json

from agent_runner.chat import ChatManager, ChatSettings
from agent_runner.service import AgentService


def test_crafted_chat_files_are_rejected_or_sanitised(tmp_path):
    store = tmp_path / "chats"
    store.mkdir()
    (store / "chat_evil.json").write_text(json.dumps({"id": "..\\..\\x", "title": "x"}), encoding="utf-8")
    (store / "chat_markup.json").write_text(json.dumps({"id": "<img src=x>", "title": "x"}), encoding="utf-8")
    good = {"id": "0123456789ab", "title": "ok", "messages": [
        {"role": "assistant", "text": "hi", "turns": "<img src=x onerror=alert(1)>", "tool_calls": "2"}],
        "events": [{"kind": "note", "t": "<b>", "seq": "3"}]}
    (store / "chat_0123456789ab.json").write_text(json.dumps(good), encoding="utf-8")
    mgr = ChatManager(store)
    assert list(mgr.chats) == ["0123456789ab"], "ids that are not 12 hex digits never load"
    chat = mgr.chats["0123456789ab"]
    assert chat.messages[0]["turns"] == 0 and chat.messages[0]["tool_calls"] == 2
    assert chat.events[0]["t"] == 0 and chat.events[0]["seq"] == 3


def test_model_ids_are_validated():
    assert ChatSettings.from_dict({"model": "claude-opus-5-5"}).model == "claude-opus-5-5"
    assert ChatSettings.from_dict({"model": "gpt-4.1-mini"}).model == "gpt-4.1-mini"
    base = ChatSettings.from_dict({})
    for bad in ("gpt-${ANTHROPIC_API_KEY}", "x\nMATLAB_EXE=evil.exe", "a b", "'quoted'"):
        assert ChatSettings.from_dict({"model": bad}).model == base.model, bad


def test_approval_needs_the_matching_call_id_and_stop_declines_the_rest():
    svc = AgentService()

    async def scenario():
        task = asyncio.ensure_future(svc._approval("time_now", {}))
        while not svc.pending:
            await asyncio.sleep(0.01)
        assert svc.decide(True, "") is False and svc.decide(True, "deadbeef") is False
        assert svc.decide(True, svc.pending["id"]) is True
        assert await task is True
        svc.stop()
        assert await svc._approval("matlab_run_code", {"code": "x"}) is False, "after Stop nothing is approved"

    asyncio.new_event_loop().run_until_complete(scenario())
