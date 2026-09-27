"""Secrets and environment handling: .env secrets stay out of os.environ, children get no secrets, workspace
switches only come from the parent, no %VAR% expansion or network paths in tool paths, safe .env writing,
program discovery that cannot be hijacked from the current folder."""
import os
import sys
from pathlib import Path

import pytest

from mcp_adapter import config
from mcp_adapter.adapters.base import resolve_path
from mcp_adapter.setup_wizard import write_env_updates


@pytest.fixture
def clean_secrets(monkeypatch):
    for k in config.SECRET_KEYS | config.PROCESS_ONLY_KEYS:
        monkeypatch.delenv(k, raising=False)
    saved = dict(config._FILE_SECRETS)
    config._FILE_SECRETS.clear()
    yield
    config._FILE_SECRETS.clear()
    config._FILE_SECRETS.update(saved)


def test_env_file_secrets_never_reach_os_environ(tmp_path, monkeypatch, clean_secrets):
    env = tmp_path / ".env"
    env.write_text("TAVILY_API=tvly-secret-1\nANTHROPIC_API_KEY=sk-ant-2\nMCP_ADAPTER_AUTH_TOKEN=tok-3\n"
                   "MCP_ADAPTER_WORK_DIR=C:/somewhere\nMCP_ADAPTER_WORK_DIR_ACCESS=true\n"
                   "MCP_TEST_PLAIN=${ANTHROPIC_API_KEY}\n", encoding="utf-8")
    monkeypatch.delenv("MCP_TEST_PLAIN", raising=False)
    config.load_env_file(env)
    for key in ("TAVILY_API", "ANTHROPIC_API_KEY", "MCP_ADAPTER_AUTH_TOKEN", "MCP_ADAPTER_WORK_DIR",
                "MCP_ADAPTER_WORK_DIR_ACCESS"):
        assert key not in os.environ, key
    assert config.tavily_key() == "tvly-secret-1" and config.auth_token() == "tok-3"
    assert os.environ["MCP_TEST_PLAIN"] == "${ANTHROPIC_API_KEY}", "no ${VAR} interpolation"
    monkeypatch.delenv("MCP_TEST_PLAIN")


def test_child_env_has_no_secrets_or_workspace_switches(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")
    monkeypatch.setenv("MCP_ADAPTER_WORK_DIR_ACCESS", "true")
    env = config.child_env({"EXTRA": "1"})
    assert "OPENAI_API_KEY" not in env and "MCP_ADAPTER_WORK_DIR_ACCESS" not in env and env["EXTRA"] == "1"
    assert "PATH" in env or "Path" in env


def test_workspace_run_does_not_see_secrets(tmp_path, monkeypatch):
    from mcp_adapter.workspace import ws_run

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    out = ws_run(tmp_path, "set" if os.name == "nt" else "env")
    assert "sk-ant-should-not-leak" not in out["stdout"]


def test_runner_passes_explicit_workspace_values_and_no_keys(monkeypatch, tmp_path):
    from agent_runner.runner import server_params

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("MCP_ADAPTER_WORK_DIR_ACCESS", "true")
    p = server_params(work_dir=None)
    assert "ANTHROPIC_API_KEY" not in p.env
    assert p.env["MCP_ADAPTER_WORK_DIR_ACCESS"] == "false" and p.env["MCP_ADAPTER_WORK_DIR"] == ""
    p = server_params(work_dir=tmp_path, work_dir_access=False)
    assert p.env["MCP_ADAPTER_WORK_DIR_ACCESS"] == "false" and p.env["MCP_ADAPTER_WORK_DIR"] == str(tmp_path)
    assert server_params(work_dir=tmp_path, work_dir_access=True).env["MCP_ADAPTER_WORK_DIR_ACCESS"] == "true"


def test_tool_paths_do_not_expand_variables_or_reach_the_network(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy-123")
    assert "sk-dummy-123" not in str(resolve_path("%OPENAI_API_KEY%.sch"))
    assert "sk-dummy-123" not in str(resolve_path("$OPENAI_API_KEY/x.sch"))
    for unc in (r"\\evil@80\share\x.drawio", "//evil/share/x", r"\\?\C:\x"):
        with pytest.raises(ValueError):
            resolve_path(unc)


def test_output_folder_errors_do_not_echo_variables(monkeypatch):
    from mcp_adapter import output_folder as of

    monkeypatch.setenv("TAVILY_API", "FAKE-TAVILY-KEY-123")
    with pytest.raises(of.OutputFolderError) as exc:
        of.check_output_folder("C:\\%TAVILY_API%" if os.name == "nt" else "/tmp/$TAVILY_API")
    assert "FAKE-TAVILY-KEY-123" not in str(exc.value)


def test_env_writer_blocks_every_line_separator_and_quotes_when_needed(tmp_path):
    from dotenv import dotenv_values

    env = tmp_path / ".env"
    env.write_text("MCP_ADAPTER_NETWORK_MODE=local\n", encoding="utf-8")
    for sep in ("\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"):
        write_env_updates(env, {"TAVILY_API": f"tvly-abc{sep}MCP_ADAPTER_NETWORK_MODE=network"})
        write_env_updates(env, {"MCP_ADAPTER_TIMEOUT": "600"})
        assert dotenv_values(env, interpolate=False)["MCP_ADAPTER_NETWORK_MODE"] == "local", repr(sep)
    tricky = {"MATLAB_EXE": "'", "OPENAI_BASE_URL": "x'", "MCP_ADAPTER_ALLOWED_HOSTS": "a #b",
              "MCP_ADAPTER_OUTPUT_DIR": r"C:\Program Files\x"}
    write_env_updates(env, tricky)
    parsed = dotenv_values(env, interpolate=False)
    for k, v in tricky.items():
        assert parsed[k] == v, k
    assert parsed["MCP_ADAPTER_NETWORK_MODE"] == "local"


def test_program_lookup_ignores_the_current_folder_and_prefers_program_files(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    fake = work / ("fakeapp.bat" if os.name == "nt" else "fakeapp")
    fake.write_text("@echo pwned\n" if os.name == "nt" else "#!/bin/sh\necho pwned\n", encoding="utf-8")
    if os.name != "nt":
        fake.chmod(0o755)
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", os.pathsep.join([str(work), os.environ.get("PATH", "")]) if os.name != "nt"
                       else os.environ.get("PATH", ""))
    monkeypatch.delenv("FAKEAPP_EXE", raising=False)
    found = config.find_executable("FAKEAPP_EXE", ["fakeapp"])
    assert found is None or Path(found).parent != work, "never the copy in the current folder"

    pf, root = tmp_path / "pf", tmp_path / "root"
    (pf / "Vendor" / "1.0").mkdir(parents=True)
    (root / "Vendor" / "9.9").mkdir(parents=True)
    (pf / "Vendor" / "1.0" / "tool.exe").write_text("", encoding="utf-8")
    (root / "Vendor" / "9.9" / "tool.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "_program_dirs", lambda: [str(pf), str(root)])
    got = config.find_executable("NOPE_EXE", ["definitely-not-on-path-xyz"], ["Vendor/*/tool.exe"])
    assert Path(got).parent.name == "1.0", "Program Files is trusted before drive roots"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_workspace_listing_does_not_follow_junctions(tmp_path):
    import _winapi

    from mcp_adapter.workspace import ws_list, ws_search

    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    (root / "a.txt").write_text("hello", encoding="utf-8")
    try:
        _winapi.CreateJunction(str(outside), str(root / "jn"))
    except OSError as exc:
        pytest.skip(f"cannot create a junction here: {exc}")
    listed = [e["path"] for e in ws_list(root.resolve(), ".", recursive=True)["entries"]]
    assert "a.txt" in listed and not any(p.startswith("jn") for p in listed)
    assert ws_search(root.resolve(), "TOP-SECRET")["hits"] == []
