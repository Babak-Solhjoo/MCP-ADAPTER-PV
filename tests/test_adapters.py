"""Adapter unit tests that do not require the vendor software to be installed."""
import json

from mcp_adapter.adapters import base, drawio, matlab, orcad, vivado
from mcp_adapter.adapters.altium import altium_adapter
from mcp_adapter.adapters.proteus import proteus_adapter


def test_run_command_captures_output():
    import sys

    res = base.BaseAdapter().run_command([sys.executable, "-c", "print('hi'); import sys; sys.exit(3)"])
    assert res.stdout.strip() == "hi"
    assert res.returncode == 3 and not res.ok


def test_json_payload_extraction():
    out = f"noise\n{base.JSON_START}{{\"a\": 1}}{base.JSON_END}\nmore"
    assert base.extract_json_payload(out) == {"a": 1}
    assert base.strip_json_payload(out) == "noise\n\nmore"


def test_matlab_literals_and_wrapper():
    assert matlab.to_matlab_literal({"a": [1, 2], "b": "it's", "c": True}) == "struct('a', [1 2], 'b', 'it''s', 'c', true)"
    script = matlab.MatlabAdapter()._wrap_script("x = 1;", ["x"])
    assert "jsonencode" in script and "catch" in script and "exit(1)" in script


def test_drawio_generation_roundtrip(tmp_path):
    nodes = [{"id": "a", "label": "A", "shape": "start"}, {"id": "b", "label": "B?", "shape": "decision"},
             {"id": "c", "label": "C"}]
    edges = [{"source": "a", "target": "b"}, {"source": "b", "target": "c", "label": "yes"}]
    xml = drawio.build_diagram_xml(nodes, edges, "LR")
    assert xml.count('vertex="1"') == 3 and xml.count('edge="1"') == 2
    assert drawio.decode_diagram(drawio.encode_diagram(xml)) == xml
    out = tmp_path / "d.drawio"
    res = drawio.drawio_adapter.create_diagram(nodes, edges, str(out))
    assert res.ok and out.exists()
    info = drawio.read_diagram_file(str(out))
    assert info["pages"][0]["vertices"] == 3 and "B?" in info["pages"][0]["labels"]


def test_drawio_rejects_duplicate_ids():
    res = drawio.drawio_adapter.create_diagram([{"id": "x"}, {"id": "x"}], [])
    assert not res.ok and "unique" in res.error


def test_pspice_output_parser(tmp_path):
    out = tmp_path / "rc.out"
    out.write_text(
        "**** SMALL SIGNAL BIAS SOLUTION  TEMPERATURE = 27.000 DEG C\n"
        " NODE   VOLTAGE     NODE   VOLTAGE\n(    1)    5.0000  (  out)    2.5000\n"
        "    VOLTAGE SOURCE CURRENTS\n    TOTAL POWER DISSIPATION   2.50E-03  WATTS\nWARNING -- something odd\n",
        encoding="utf-8",
    )
    parsed = orcad.parse_pspice_output(out)
    assert parsed["node_voltages"] == {"1": 5.0, "out": 2.5}
    assert parsed["total_power_w"] == 0.0025
    assert len(parsed["warnings"]) == 1 and not parsed["errors"]


def test_vivado_tcl_helpers():
    assert vivado.tcl_string("abc") == "{abc}"
    assert vivado.tcl_string("a{b") == '"a{b"'
    assert vivado.tcl_path("C:\\x\\y.xpr") == "{C:/x/y.xpr}"


def test_altium_templates_mention_paths():
    code = altium_adapter.script_export_bom("C:/p/x.PrjPcb", "C:/p/bom.csv")
    assert "procedure Main" in code and "bom.csv" in code


def test_proteus_project_info_on_zip(tmp_path):
    import zipfile

    prj = tmp_path / "demo.pdsprj"
    with zipfile.ZipFile(prj, "w") as zf:
        zf.writestr("design.xml", "<design mcu='ATMEGA328P' firmware='blink.hex'/>")
    res = proteus_adapter.project_info(str(prj))
    assert res.ok and "ATMEGA328P" in res.data["microcontrollers"]
    assert any(f.endswith(".hex") for f in res.data["firmware_files"])


def test_unavailable_adapter_reports_hint(monkeypatch):
    monkeypatch.setattr(vivado.vivado_adapter, "executable", lambda: None)
    res = vivado.vivado_adapter.run_tcl("puts hi")
    assert not res.ok and "VIVADO_EXE" in res.error
    assert json.dumps(res.to_dict())
