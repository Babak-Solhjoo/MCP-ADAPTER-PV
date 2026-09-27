"""OpenAI provider: tool conversion, result flattening and the manual tool loop with a fake client."""
import asyncio
import json
from types import SimpleNamespace

from agent_runner import openai_provider as op
from agent_runner.runner import RunConfig, RunResult, Transcript


def _tool(name, schema=None):
    return SimpleNamespace(name=name, description=f"{name} desc", inputSchema=schema or {"type": "object", "properties": {}})


def test_mcp_tools_to_openai():
    out = op.mcp_tools_to_openai([_tool("time_now", {"type": "object", "properties": {"timezone": {"type": "string"}}})])
    assert out[0]["type"] == "function" and out[0]["function"]["name"] == "time_now"
    assert out[0]["function"]["parameters"]["properties"]["timezone"]["type"] == "string"


def test_mcp_result_to_text():
    res = SimpleNamespace(content=[SimpleNamespace(type="text", text="a"), SimpleNamespace(type="text", text="b")], isError=False)
    assert op.mcp_result_to_text(res) == ("a\nb", False)
    err = SimpleNamespace(content=[SimpleNamespace(type="text", text="boom")], isError=True)
    assert op.mcp_result_to_text(err) == ("boom", True)


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "explode":
            raise RuntimeError("kaboom")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"ok": True, "echo": args}))], isError=False)


def _completion(content=None, tool_calls=None, finish="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def _call(cid, name, args):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeClient:
    """Scripted responses; records the request kwargs."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return self.responses.pop(0)


def _run(cfg, client, session):
    tr = Transcript(cfg)
    res = RunResult()
    asyncio.run(op.run_openai_loop(cfg, session, [_tool("time_now"), _tool("explode")], tr, res,
                                   cfg.approval_handler, client=client))
    return tr, res


def test_loop_runs_tools_then_finishes():
    client = FakeClient([
        _completion(content="Let me check.", tool_calls=[_call("c1", "time_now", {"timezone": "UTC"}), _call("c2", "explode", {})], finish="tool_calls"),
        _completion(content="All done.", finish="stop"),
    ])
    session = FakeSession()
    cfg = RunConfig(task="what time is it", provider="openai", model="gpt-5", effort="xhigh", max_turns=5)
    tr, res = _run(cfg, client, session)
    assert res.turns == 2 and res.tool_calls == 2 and res.final_text == "All done." and res.stop_reason == "stop"
    assert res.usage == {"input_tokens": 20, "output_tokens": 10}
    assert session.calls == [("time_now", {"timezone": "UTC"}), ("explode", {})]
    kinds = [e["kind"] for e in tr.entries]
    assert kinds.count("tool_call") == 2 and kinds.count("tool_result") == 2
    errors = [e for e in tr.entries if e["kind"] == "tool_result" and e["is_error"]]
    assert len(errors) == 1 and "kaboom" in errors[0]["content"]
    first = client.requests[0]
    assert first["reasoning_effort"] == "high" and first["tool_choice"] == "auto"
    assert first["messages"][0]["role"] == "system" and "MCP tools" in first["messages"][0]["content"]
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c2"
    assert second[-3]["role"] == "assistant" and len(second[-3]["tool_calls"]) == 2


def test_loop_respects_approval_and_stop():
    client = FakeClient([
        _completion(tool_calls=[_call("c1", "time_now", {})], finish="tool_calls"),
        _completion(tool_calls=[_call("c2", "time_now", {})], finish="tool_calls"),
        _completion(content="never reached"),
    ])
    session = FakeSession()
    decisions = iter([False])

    async def approval(name, args):
        return next(decisions, True)

    stop_flag = {"v": False}

    def stop_requested():
        stop_flag["v"] = not stop_flag["v"]  # first turn: False -> second: True
        return not stop_flag["v"]

    cfg = RunConfig(task="t", provider="openai", model="gpt-4.1", max_turns=5, approval_handler=approval,
                    stop_requested=stop_requested)
    tr, res = _run(cfg, client, session)
    results = [e for e in tr.entries if e["kind"] == "tool_result"]
    assert results[0]["is_error"] and "declined" in results[0]["content"]
    assert session.calls == [("time_now", {})]  # only the second (approved) call reached the session
    assert any(e["kind"] == "note" and "Stopped by the user" in e["text"] for e in tr.entries)
    assert res.turns == 2 and "reasoning_effort" not in client.requests[0]


def test_loop_hits_max_turns():
    client = FakeClient([_completion(tool_calls=[_call(f"c{i}", "time_now", {})], finish="tool_calls") for i in range(3)])
    cfg = RunConfig(task="t", provider="openai", model="gpt-4.1", max_turns=3)
    tr, res = _run(cfg, client, FakeSession())
    assert res.turns == 3 and any("max_turns" in e.get("text", "") for e in tr.entries if e["kind"] == "note")


def test_run_task_rejects_unknown_provider():
    import pytest

    from agent_runner.runner import run_task

    with pytest.raises(ValueError):
        asyncio.run(run_task(RunConfig(task="x", provider="bogus")))
