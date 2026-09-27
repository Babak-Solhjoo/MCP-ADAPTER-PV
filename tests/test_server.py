"""End-to-end checks of the MCP server object: adapter-mode tool registration and in-process tool calls."""
import asyncio
import importlib
import json

import pytest

from mcp_adapter import server as srv

ALL_PREFIXES = ["time_", "matlab_", "simulink_", "mathematica_", "comsol_", "photoshop_", "orcad_",
                "altium_", "proteus_", "vivado_", "autocad_", "hfss_", "drawio_", "feko_", "eagle_"]
CORE_TOOLS = ("list_software", "list_categories", "list_tools", "describe_tool", "search_tools",
              "software_overview", "security_policy", "adapter_status")
ALWAYS_PRESENT = ("orcad_parse_pspice_output", "altium_script_template", "proteus_project_info",
                  "drawio_create_diagram", "drawio_flowchart", "drawio_read", "drawio_codec",
                  "feko_parse_out", "feko_lua_template", "eagle_read_design", "eagle_bom", "eagle_netlist")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _names(module):
    return {t.name for t in _run(module.server.list_tools())}


def _call(tool: str, **kwargs):
    result = _run(srv.server.call_tool(tool, kwargs))
    # mcp 2.x returns (content, structured) or a CallToolResult; mcp 1.x returns a list of content blocks
    if isinstance(result, tuple):
        content, structured = result
        if structured:
            return structured.get("result", structured)
        return json.loads(content[0].text)
    if hasattr(result, "structuredContent") and result.structuredContent:
        sc = result.structuredContent
        return sc.get("result", sc)
    blocks = getattr(result, "content", result)
    text = blocks[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_all_tools_registered_when_exposing_unavailable(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "true")
    mod = importlib.reload(srv)
    names = _names(mod)
    assert len(names) >= 80
    for prefix in ALL_PREFIXES:
        assert any(n.startswith(prefix) for n in names), prefix
    for core in CORE_TOOLS:
        assert core in names
    for t in _run(mod.server.list_tools()):
        assert t.description, f"{t.name} has no description"
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    importlib.reload(srv)


def test_adapter_mode_hides_tools_of_missing_software(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    mod = importlib.reload(srv)
    names = _names(mod)
    for core in CORE_TOOLS + ALWAYS_PRESENT:
        assert core in names, core
    status = _call("adapter_status")
    for st in status["adapters"]:
        exposed = set(st["exposed_tools"])
        hidden = {h["tool"] for h in st["hidden_tools"]}
        assert exposed <= names
        assert not (hidden & names), f"hidden tools of {st['software']} leaked into the tool list"
        if not st["available"]:
            assert not [n for n in exposed if n not in ALWAYS_PRESENT], st["software"]
            assert all(st["env_var"] in h["reason"] for h in st["hidden_tools"])
    sw = _call("list_software")
    for row in sw["software"]:
        if row["installed"] is False:
            assert all(t in ALWAYS_PRESENT for t in row["automation_tools_available"]), row["id"]
        assert set(row["automation_tools_available"]) <= names


def test_time_tool_call():
    out = _call("time_now", timezone="Asia/Tehran")
    assert out["utc_offset"] == "+03:30"
    out = _call("time_difference", start="2026-01-01", end="2026-01-02")
    assert out["days"] == 1


def test_catalog_tool_calls():
    sw = _call("list_software")
    assert sw["total_catalog_entries"] > 1000
    cats = _call("list_categories", software="matlab")
    assert cats["categories"]
    hits = _call("search_tools", query="eigenvalues", software="matlab")
    assert hits["count"] > 0
    desc = _call("describe_tool", software="matlab", name="eig")
    assert isinstance(desc, str) and "eig" in desc
    missing = _call("describe_tool", software="matlab", name="definitely_not_a_function_xyz")
    assert missing["ok"] is False


def test_adapter_status_shape():
    st = _call("adapter_status")
    assert {a["software"] for a in st["adapters"]} >= {"matlab", "vivado", "drawio"}
    assert "security" in st and "adapter_mode" in st


def test_drawio_tool_call(tmp_path):
    out = _call("drawio_flowchart", steps=["Read", "Valid?", "Write"], output_file=str(tmp_path / "f.drawio"))
    assert out["ok"] and (tmp_path / "f.drawio").exists()


@pytest.mark.skipif(not srv.matlab_adapter.is_available(), reason="MATLAB not installed")
def test_matlab_live_eval():
    out = _call("matlab_eval", expression="sum(1:10)", timeout_seconds=300)
    assert out["ok"] and out["data"]["mcp_value__"] == 55
