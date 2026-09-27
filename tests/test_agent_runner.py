"""Agent runner: tool selection, transcript/report, dry-run against the real MCP server (no API calls)."""
import asyncio
import os
from types import SimpleNamespace

import pytest

from agent_runner import runner
from agent_runner.__main__ import main


def _tool(name):
    return SimpleNamespace(name=name, description=f"{name} description")


def test_select_tools_keeps_core_and_filters_prefixes():
    tools = [_tool(n) for n in ("list_software", "adapter_status", "time_now", "search_tools",
                                 "matlab_eval", "drawio_flowchart", "vivado_run_tcl")]
    names = [t.name for t in runner.select_tools(tools, ["drawio"])]
    assert "drawio_flowchart" in names and "list_software" in names and "time_now" in names
    assert "matlab_eval" not in names and "vivado_run_tcl" not in names
    assert [t.name for t in runner.select_tools(tools, [])] == [t.name for t in tools]
    assert "matlab_eval" in [t.name for t in runner.select_tools(tools, ["matlab_", " drawio "])]


def test_transcript_report(tmp_path):
    cfg = runner.RunConfig(task="do x", report_path=tmp_path / "r.md")
    tr = runner.Transcript(cfg)
    tr.add("assistant", text="Starting.")
    tr.add("tool_call", name="time_now", input={"timezone": "UTC"})
    tr.add("tool_result", name="time_now", content='{"iso": "2026-09-19T00:00:00+00:00"}', is_error=False)
    tr.add("note", text="done")
    res = runner.RunResult(final_text="All good.", turns=2, tool_calls=1, stop_reason="end_turn",
                           usage={"input_tokens": 10, "output_tokens": 5})
    path = tr.save(res)
    text = path.read_text(encoding="utf-8")
    assert "# Agent run report" in text and "do x" in text and "time_now" in text and "All good." in text
    assert "> done" in text


def test_tool_result_text_variants():
    assert runner._tool_result_text("plain") == "plain"
    assert runner._tool_result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert '"x": 1' in runner._tool_result_text({"x": 1})


def test_server_params_default():
    p = runner.server_params(None)
    assert p.args[-2:] == ["-m", "mcp_adapter.server"]
    assert runner.server_params(["python", "-m", "x"]).command == "python"


def test_dry_run_lists_real_tools():
    tools = asyncio.run(runner.list_available_tools(runner.RunConfig(only=["drawio"])))
    names = {t["name"] for t in tools}
    assert {"list_software", "adapter_status", "drawio_flowchart", "time_now"} <= names
    assert not any(n.startswith("matlab_") for n in names)


def test_cli_dry_run(capsys):
    assert main(["--dry-run", "--only", "drawio"]) == 0
    out = capsys.readouterr().out
    assert "tools would be given to the agent" in out and "drawio_flowchart" in out


def test_cli_requires_task(capsys):
    assert main([]) == 2
    assert "give a task" in capsys.readouterr().err


@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="paid live call: set RUN_LIVE_TESTS=1 (and an Anthropic API key) to run it")
def test_live_tiny_task(tmp_path):
    cfg = runner.RunConfig(task="Call time_now for UTC and reply with just the ISO timestamp.",
                           only=["nothing_"], max_turns=4, report_path=tmp_path / "live.md", quiet=True)
    res = runner.run(cfg)
    assert res.turns >= 1 and res.report_path.exists()
