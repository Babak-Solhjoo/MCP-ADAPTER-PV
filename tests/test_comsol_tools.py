"""COMSOL adapter additions: compile-failure detection, real output path, Java programs, installation index."""
import json
from pathlib import Path

import pytest

from mcp_adapter.adapters import comsol_library as lib
from mcp_adapter.adapters.base import JSON_END, JSON_START, RunResult
from mcp_adapter.adapters.comsol import comsol_adapter
from mcp_adapter.adapters.comsol_java import EVAL_TYPES, evaluate_java, summary_java


@pytest.fixture
def fake_comsol(monkeypatch, tmp_path):
    """Fake comsolbatch/comsolcompile: records calls, writes .class files and batch logs like COMSOL 6.4."""
    exe = tmp_path / "bin" / "win64" / "comsolbatch.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(comsol_adapter, "executable", lambda: str(exe))
    monkeypatch.setattr(comsol_adapter, "is_available", lambda: True)
    monkeypatch.setattr(comsol_adapter, "scripts_dir", lambda: tmp_path / "out")
    monkeypatch.setattr(comsol_adapter, "cache_dir", lambda: tmp_path / "out")
    (tmp_path / "out").mkdir()
    state = {"calls": [], "compile_fails": False, "payload": {"ok": 1}}

    def fake_run(args, cwd=None, timeout=None, artifacts=None, **kw):
        args = [str(a) for a in args]
        state["calls"].append(args)
        if args[0].endswith("comsolcompile.exe") or args[0].endswith("comsol.exe"):
            java = Path(args[-1])
            state["java_src"] = java.read_text(encoding="utf-8")
            if state["compile_fails"]:
                return RunResult(ok=True, software="comsol", command="compile", returncode=0,
                                 stdout="Failed to compile java file.\nERROR: Compilation failed.",
                                 artifacts=dict(artifacts or {}))
            java.with_suffix(".class").write_text("class", encoding="utf-8")
            return RunResult(ok=True, software="comsol", command="compile", returncode=0, stdout="",
                             artifacts=dict(artifacts or {}))
        log = Path(args[args.index("-batchlog") + 1])
        out = Path(args[args.index("-outputfile") + 1])
        text = f"Running\n{JSON_START}{json.dumps(state['payload'])}{JSON_END}\n"
        if args[args.index("-inputfile") + 1].endswith(".class"):
            saved = out.with_name(out.stem + "_Model.mph")
            saved.write_text("mph", encoding="utf-8")
            text += f"Saving model: {saved}\n"
        log.write_text(text, encoding="utf-8")
        return RunResult(ok=True, software="comsol", command="batch", returncode=0, stdout="",
                         artifacts=dict(artifacts or {}))

    monkeypatch.setattr(comsol_adapter, "run_command", fake_run)
    (tmp_path / "bin" / "win64" / "comsolcompile.exe").write_text("", encoding="utf-8")
    return tmp_path, state


def test_compile_failure_is_detected_despite_exit_code_zero(fake_comsol):
    tmp_path, state = fake_comsol
    java = tmp_path / "Model.java"
    java.write_text("class Model {}", encoding="utf-8")
    state["compile_fails"] = True
    res = comsol_adapter.compile_java(str(java))
    assert res.ok is False and "compilation failed" in res.error.lower()
    state["compile_fails"] = False
    assert comsol_adapter.compile_java(str(java)).ok


def test_build_from_java_reports_the_file_comsol_saved(fake_comsol):
    tmp_path, _ = fake_comsol
    java = tmp_path / "busbar.java"
    java.write_text("class busbar {}", encoding="utf-8")
    res = comsol_adapter.build_from_java(str(java))
    assert res.ok and res.artifacts["output_file"].endswith("busbar_solved_Model.mph")


def test_inspect_and_evaluate_return_the_java_payload(fake_comsol):
    tmp_path, state = fake_comsol
    model = tmp_path / "m.mph"
    model.write_text("mph", encoding="utf-8")
    state["payload"] = {"title": "Computing Capacitance", "derived_values": [{"values": [[4.3e-11]]}]}
    res = comsol_adapter.inspect_model(str(model))
    assert res.ok and res.data["title"] == "Computing Capacitance"
    java = state["java_src"]
    assert "ModelUtil.load" in java and "D:/" not in java and JSON_START in java
    assert not list((tmp_path / "out" / "programs").glob("McpInspect*")), "successful runs clean up after themselves"
    assert "java" not in res.artifacts and "batch_log" not in res.artifacts
    state["payload"] = {"error": "Undefined_post_expression"}
    res = comsol_adapter.evaluate(str(model), ["es.C11"], ["pF"])
    assert res.ok is False and res.error == "Undefined_post_expression"
    assert comsol_adapter.evaluate(str(model), ["x"], kind="nonsense").ok is False
    assert comsol_adapter.inspect_model(str(tmp_path / "missing.mph")).ok is False


def test_java_sources_are_well_formed():
    src = evaluate_java("McpEvalX", "C:\\m odels\\a.mph", ['es.C11', 'a"b'], ["pF"], "volume_maximum", "dset1", [1, 3])
    assert "public class McpEvalX" in src and '"C:/m odels/a.mph"' in src
    assert 'new String[] {"es.C11", "a\\"b"}' in src and 'new String[] {"pF", ""}' in src
    assert '"MaxVolume"' in src and "new int[] {1, 3}" in src and src.count("{") == src.count("}")
    s = summary_java("McpSumX", "/tmp/x.mph", evaluate=False)
    assert "if (false)" in s and s.count("{") == s.count("}")
    assert EVAL_TYPES["surface_integral"] == "IntSurface"


@pytest.fixture
def fake_install(tmp_path):
    root = tmp_path / "Multiphysics"
    ex_dir = root / "applications" / "ACDC_Module" / "Introductory_Electrostatics"
    ex_dir.mkdir(parents=True)
    (ex_dir / "capacitor_dc.mph").write_bytes(b"x" * 1000)
    (root / "applications" / "Heat_Transfer_Module" / "Tutorials").mkdir(parents=True)
    (root / "applications" / "Heat_Transfer_Module" / "Tutorials" / "heat_sink.mph").write_bytes(b"x")
    (root / "doc" / "pdf" / "ACDC_Module").mkdir(parents=True)
    (root / "doc" / "pdf" / "ACDC_Module" / "ACDCModuleUsersGuide.pdf").write_bytes(b"%PDF")
    help_dir = root / lib.HELP_REL / "com.comsol.help.models.acdc.capacitor_dc"
    help_dir.mkdir(parents=True)
    (help_dir / "capacitor_dc.html").write_text(
        "<html><title>COMSOL 6.4 - Computing Capacitance</title><body><h1>Computing Capacitance</h1>"
        "<h2>Introduction</h2><p>A simple capacitor.</p><h2>Model Definition</h2>"
        "<p>Application Library path: ACDC_Module/Introductory_Electrostatics/capacitor_dc</p>"
        "<p>Modeling Instructions</p></body></html>", encoding="utf-8")
    (help_dir / "capacitor_dc.java").write_text(
        'model.component("comp1").physics().create("es", "Electrostatics", "geom1");\n'
        'model.study("std1").create("stat", "Stationary");\n', encoding="utf-8")
    (help_dir / "models.acdc.capacitor_dc.pdf").write_bytes(b"%PDF")
    exe = root / "bin" / "win64" / "comsolbatch.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    return root, exe


def test_errors_in_the_batch_log_fail_the_run(fake_comsol, monkeypatch):
    tmp_path, state = fake_comsol
    model = tmp_path / "m.mph"
    model.write_text("mph", encoding="utf-8")
    real_run = comsol_adapter.run_command

    def run_with_error(args, **kw):
        res = real_run(args, **kw)
        log = Path([str(a) for a in args][[str(a) for a in args].index("-batchlog") + 1])
        log.write_text("Opening file\n/******************/\n/*****Error********/\n/******************/\n"
                       "This is a COMSOL Application Libraries preview file.\nTotal time: 1 s.\n", encoding="utf-8")
        return res

    monkeypatch.setattr(comsol_adapter, "run_command", run_with_error)
    res = comsol_adapter.run_batch(str(model))
    assert res.ok is False and "preview file" in res.error and "output_file" not in res.artifacts


def test_companion_files_are_copied_or_reported(tmp_path):
    from mcp_adapter.adapters.comsol import copy_companion_files

    lib_dir = tmp_path / "applications" / "Mod" / "Topic"
    lib_dir.mkdir(parents=True)
    (lib_dir / "ex.mph").write_text("m", encoding="utf-8")
    (lib_dir / "table.csv").write_text("1,2", encoding="utf-8")
    (tmp_path / "applications" / "Mod" / "Other").mkdir()
    (tmp_path / "applications" / "Mod" / "Other" / "part.step").write_text("s", encoding="utf-8")
    java = tmp_path / "ex.java"
    java.write_text('// browse to "commented.mph"\nx.insertFile("table.csv", "g");\n'
                    'y.importFile("part.step");\nz.insertFile("absent_geom_sequence.mph", "geom1");\n', encoding="utf-8")
    dest = tmp_path / "work"
    dest.mkdir()
    copied, missing = copy_companion_files(java, lib_dir / "ex.mph", dest)
    assert sorted(Path(c).name for c in copied) == ["part.step", "table.csv"]
    assert missing == ["absent_geom_sequence.mph"]


def test_preview_examples_are_rebuilt_from_java(fake_install, fake_comsol, monkeypatch):
    import zipfile

    root, exe = fake_install
    tmp_path, state = fake_comsol
    mph = root / "applications" / "ACDC_Module" / "Introductory_Electrostatics" / "capacitor_dc.mph"
    with zipfile.ZipFile(mph, "w") as z:
        z.writestr("preview", "")
        z.writestr("modelinfo.xml", "<x/>")
    assert lib._is_preview(mph)
    monkeypatch.setattr(comsol_adapter, "executable", lambda: str(exe))
    (exe.parent / "comsolcompile.exe").write_text("", encoding="utf-8")
    index = comsol_adapter.library_index()
    assert lib.find_example(index, "capacitor_dc")["preview"] is True
    res = comsol_adapter.run_example("capacitor_dc", output_dir=str(tmp_path / "ex"))
    assert res.ok and res.data["method"] == "java" and "preview" in res.data["note"]
    assert res.artifacts["output_file"].endswith("capacitor_dc_solved_Model.mph")
    heat = root / "applications" / "Heat_Transfer_Module" / "Tutorials" / "heat_sink.mph"
    with zipfile.ZipFile(heat, "w") as z:
        z.writestr("preview", "")
    (tmp_path / "out" / "example_index.json").unlink()
    res = comsol_adapter.run_example("heat_sink", output_dir=str(tmp_path / "ex"))
    assert res.ok is False and "preview file" in res.error


def test_library_index_search_and_cache(fake_install, tmp_path):
    root, exe = fake_install
    assert lib.find_root(str(exe)) == root
    cache = tmp_path / "cache" / "index.json"
    index = lib.load_index(root, cache)
    assert cache.exists() and lib.load_index(root, cache)["signature"] == index["signature"]
    by = {e["name"]: e for e in index["examples"]}
    cap = by["capacitor_dc"]
    assert cap["title"] == "Computing Capacitance" and cap["intro"] == "A simple capacitor."
    assert cap["physics"] == ["es: Electrostatics"] and cap["studies"] == ["Stationary"]
    assert cap["java"].endswith("capacitor_dc.java") and cap["pdf"].endswith(".pdf")
    assert by["heat_sink"]["title"] == "heat sink" and by["heat_sink"]["java"] == ""
    mods = {m["module"]: m for m in index["modules"]}
    assert mods["ACDC_Module"]["examples"] == 1 and mods["ACDC_Module"]["user_guides"]
    hits = lib.search(index, "electrostatics capacitance")
    assert hits[0]["name"] == "capacitor_dc" and hits[0]["has_java"]
    assert lib.search(index, "capacitance", module="heat") == []
    assert lib.find_example(index, "ACDC_Module/Introductory_Electrostatics/capacitor_dc")["name"] == "capacitor_dc"
    assert lib.find_example(index, "Computing Capacitance")["name"] == "capacitor_dc"
    assert lib.find_example(index, "nope") is None
