"""Agent runner hardening: only offered tools run, keys only go to api.openai.com, secrets are redacted,
workspace_run always asks, the CLI shows the whole tool input."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from agent_runner import openai_provider as op
from agent_runner import runner
from agent_runner.runner import RunConfig, RunResult, Transcript


def _tool(name):
    return SimpleNamespace(name=name, description=name, inputSchema={"type": "object", "properties": {}})


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")], isError=False)


def _completion(content=None, tool_calls=None, finish="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], usage=None)


def _call(cid, name, args):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        return self.responses.pop(0)


def test_openai_loop_refuses_tools_that_were_not_offered():
    client = FakeClient([_completion(tool_calls=[_call("c1", "matlab_eval", {"code": "system('calc')"})],
                                     finish="tool_calls"),
                         _completion(content="done")])
    session = FakeSession()
    cfg = RunConfig(task="t", provider="openai", model="gpt-4.1", max_turns=3)
    tr, res = Transcript(cfg), RunResult()
    asyncio.run(op.run_openai_loop(cfg, session, [_tool("time_now")], tr, res, None, client=client))
    assert session.calls == [], "matlab_eval was not offered, so it must never reach the MCP session"
    result = next(e for e in tr.entries if e["kind"] == "tool_result")
    assert result["is_error"] and "not available" in result["content"]


def test_openai_key_only_goes_to_the_official_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    assert op.client_kwargs(None) == {}
    assert op.client_kwargs("https://api.openai.com/v1") == {"base_url": "https://api.openai.com/v1"}
    other = op.client_kwargs("https://gateway.example.com/v1")
    assert other["api_key"] == "not-needed" and other["api_key"] != "sk-real"
    assert op.client_kwargs("http://127.0.0.1:11434/v1")["api_key"] == "not-needed"
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "gw-key")
    assert op.client_kwargs("https://gateway.example.com/v1")["api_key"] == "gw-key"
    for bad in ("http://192.168.1.50:11434/v1", "ftp://x/v1", "gateway.example.com"):
        with pytest.raises(ValueError):
            op.client_kwargs(bad)


def test_transcripts_redact_secret_values(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECRETSECRET")
    cfg = RunConfig(task="t")
    tr = Transcript(cfg)
    e = tr.add("tool_result", name="workspace_run", content="PATH=x\nANTHROPIC_API_KEY=sk-ant-api03-SECRETSECRET",
               extra={"nested": ["sk-ant-api03-SECRETSECRET"]})
    assert "SECRETSECRET" not in json.dumps(e) and "[REDACTED]" in e["content"]


def test_workspace_run_always_asks_even_with_approvals_off(monkeypatch):
    asked = []

    async def handler(name, tool_input):
        asked.append(name)
        return False

    cfg = RunConfig(task="t", approve=False, approval_handler=handler)
    # the approval function run_task builds: replicate its rule
    assert "workspace_run" in runner.ALWAYS_ASK

    async def approval(name, tool_input):
        if cfg.approve or name in runner.ALWAYS_ASK:
            return await cfg.approval_handler(name, tool_input)
        return True

    assert asyncio.run(approval("time_now", {})) is True and asked == []
    assert asyncio.run(approval("workspace_run", {"command": "dir"})) is False and asked == ["workspace_run"]


def test_cli_approval_prints_the_whole_input(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    ask = runner._cli_approval_factory()
    code = "x = 1;\n" * 200 + "system('del /q C:\\\\important')"
    assert asyncio.run(ask("matlab_run_code", {"code": code})) is False
    assert "system('del /q" in capsys.readouterr().out, "the part after 600 characters must be visible"
