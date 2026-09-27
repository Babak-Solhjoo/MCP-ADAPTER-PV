"""Run-time output folder: adapters write into the folder the user gave for the task, after checks in code."""
from pathlib import Path

import pytest

from mcp_adapter import config
from mcp_adapter import output_folder as of
from mcp_adapter.adapters.base import RunResult, resolve_path
from mcp_adapter.adapters.orcad import pspice_adapter


@pytest.fixture
def folders(monkeypatch, tmp_path):
    default = tmp_path / "default"
    monkeypatch.setenv("MCP_ADAPTER_OUTPUT_DIR", str(default))
    monkeypatch.delenv("MCP_ADAPTER_OUTPUT_ROOTS", raising=False)
    monkeypatch.delenv("MCP_ADAPTER_WORK_DIR", raising=False)
    protected = tmp_path / "protected"
    protected.mkdir()
    # pytest's tmp_path lives under %LOCALAPPDATA% on Windows, which the real list protects.
    monkeypatch.setattr(of, "protected_locations", lambda: [protected.resolve()])
    task = tmp_path / "Electronics"
    task.mkdir()
    yield {"default": default, "task": task, "protected": protected, "tmp": tmp_path}
    config.set_runtime_output(None)


def test_adapters_write_into_the_chosen_folder(folders, monkeypatch):
    task = folders["task"]
    out = of.set_output_folder(str(task))
    assert out["ok"] and out["output_folder"] == str(task.resolve()) and out["previous"] == str(folders["default"])
    assert config.output_dir() == task.resolve()
    assert pspice_adapter.scripts_dir() == task.resolve() / "orcad"
    assert resolve_path("buck.cir") == task.resolve() / "buck.cir"
    assert pspice_adapter.cache_dir() == folders["default"] / "orcad", "caches stay in the default folder"

    monkeypatch.setattr(pspice_adapter, "simulate",
                        lambda path, timeout=None: RunResult(ok=True, software="orcad", command="psp", artifacts={}))
    res = pspice_adapter.simulate_netlist("V1 1 0 1\nR1 1 0 1k\n.OP\n", name="buck")
    assert Path(res.artifacts["circuit_file"]) == task.resolve() / "orcad" / "buck.cir"
    assert (task / "orcad" / "buck.cir").read_text(encoding="utf-8").rstrip().endswith(".END")

    of.set_output_folder(str(task), per_application_subfolders=False)
    assert pspice_adapter.scripts_dir() == task.resolve()
    back = of.set_output_folder("")
    assert back["default"] and config.output_dir() == folders["default"] and config.app_subfolders()
    assert of.describe()["source"].startswith("MCP_ADAPTER_OUTPUT_DIR")


@pytest.mark.parametrize("case, message", [
    ("relative", "not an absolute path"),
    ("missing", "does not exist"),
    ("root", "drive or filesystem root"),
    ("home", "home folder itself"),
    ("hidden", "hidden folder"),
    ("protected", "protected location"),
    ("unc", "network location"),
    ("empty", "No folder given"),
])
def test_unsafe_folders_are_refused(folders, case, message):
    tmp = folders["tmp"]
    (tmp / ".hidden").mkdir()
    (folders["protected"] / "inner").mkdir()
    value = {
        "relative": "Electronics",
        "missing": str(tmp / "nope"),
        "root": tmp.anchor,
        "home": str(Path.home()),
        "hidden": str(tmp / ".hidden"),
        "protected": str(folders["protected"] / "inner"),
        "unc": r"\\server\share\results",
        "empty": "  ",
    }[case]
    with pytest.raises(of.OutputFolderError, match=message):
        of.check_output_folder(value)
    assert config.output_dir() == folders["default"]


def test_allowed_roots_and_workspace_confinement(folders, monkeypatch):
    tmp, task = folders["tmp"], folders["task"]
    allowed = tmp / "allowed"
    (allowed / "run1").mkdir(parents=True)
    monkeypatch.setenv("MCP_ADAPTER_OUTPUT_ROOTS", f"{allowed};{tmp / 'other'}")
    assert of.check_output_folder(str(allowed / "run1")) == (allowed / "run1").resolve()
    with pytest.raises(of.OutputFolderError, match="MCP_ADAPTER_OUTPUT_ROOTS"):
        of.check_output_folder(str(task))
    monkeypatch.delenv("MCP_ADAPTER_OUTPUT_ROOTS")
    monkeypatch.setenv("MCP_ADAPTER_WORK_DIR", str(allowed))
    with pytest.raises(of.OutputFolderError, match="confined to the task's working directory"):
        of.check_output_folder(str(task))
    assert of.check_output_folder(str(allowed / "run1"))


def test_real_protected_list_covers_system_and_source_folders():
    locs = [str(p).lower() for p in of.protected_locations()]
    assert str(config.PACKAGE_DIR).lower() in locs
    if config.IS_WINDOWS:
        assert any("appdata" in p for p in locs) and any("program files" in p for p in locs)
    assert of._inside(Path("/a/b/c"), Path("/a/b")) and not of._inside(Path("/a/bc"), Path("/a/b"))


def test_server_tool_and_status(folders, monkeypatch):
    import importlib

    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    from mcp_adapter import server as srv

    mod = importlib.reload(srv)
    ok = mod.set_output_folder(str(folders["task"]))
    assert ok["ok"] and mod.adapter_status()["output_folder"]["current"] == str(folders["task"].resolve())
    assert mod.security_policy()["output_folder"]["source"] == "set_output_folder"
    bad = mod.set_output_folder(str(folders["tmp"] / "nope"))
    assert bad["ok"] is False and "does not exist" in bad["error"]
    assert bad["output_folder"] == str(folders["task"].resolve()), "a refused folder leaves the current one in place"
    assert "set_output_folder" in mod.server.instructions
