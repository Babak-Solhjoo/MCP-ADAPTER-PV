"""Adapter hardening: bounded decompression, bounded ZIP reads, escaped names/paths in generated code,
unforgeable COMSOL result markers, unpredictable result files."""
import base64
import zipfile
import zlib

import pytest

from mcp_adapter.adapters import altium, comsol_java, drawio, feko, proteus
from mcp_adapter.adapters.simulink import simulink_adapter


def test_drawio_decode_refuses_decompression_bombs():
    xml = "<mxGraphModel><root><mxCell id='0'/></root></mxGraphModel>"
    assert drawio.decode_diagram(drawio.encode_diagram(xml)) == xml
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    bomb = base64.b64encode(c.compress(b"A" * (60 * 1024 * 1024)) + c.flush()).decode()
    with pytest.raises(ValueError, match="expands beyond"):
        drawio.decode_diagram(bomb)
    with pytest.raises(ValueError):
        drawio.decode_diagram(bomb, max_bytes=1000)


def test_proteus_reader_reads_bounded_prefixes(tmp_path):
    p = tmp_path / "big.pdsprj"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ROOT.xml", "<x>PIC16F877A firmware.hex</x>" + " " * (8 * 1024 * 1024))
    res = proteus.proteus_adapter.project_info(str(p))
    assert res.ok and "PIC16F877A" in res.data["microcontrollers"] and any("firmware.hex" in f for f in res.data["firmware_files"])


def test_comsol_json_strings_cannot_contain_markers():
    assert '"\\\\u003c"' in comsol_java._HELPERS and '"\\\\u003e"' in comsol_java._HELPERS


def test_simulink_block_names_are_escaped_and_positions_checked(monkeypatch):
    captured = {}

    def fake_run(code, capture=None, timeout=None):
        captured["code"] = code
        from mcp_adapter.adapters.base import RunResult

        return RunResult(ok=True, software="simulink", command="")

    monkeypatch.setattr(simulink_adapter, "run_code", fake_run)
    blocks = [{"name": "Gain'); system('calc'); %", "library": "simulink/Math Operations/Gain",
               "position": [30, 40, 60, 70]}]
    assert simulink_adapter.build_model("m1", blocks, []).ok
    assert "[mdl '/Gain''); system(''calc''); %']" in captured["code"]
    assert "'Position', [30.0 40.0 60.0 70.0]" in captured["code"]
    bad = simulink_adapter.build_model("m1", [{"name": "G", "library": "x", "position": "[1 2 3 4]); system('x"}], [])
    assert not bad.ok and "four numbers" in bad.error


def test_altium_and_feko_templates_quote_paths():
    code = altium.altium_adapter.script_export_bom("C:/p/it's.PrjPcb", "C:/o/o'x.csv")
    assert "it''s.PrjPcb" in code and "o''x.csv" in code
    lua = feko.lua_template(next(iter(feko.LUA_TEMPLATES)), model="C:/a]]b.cfx", output="C:/o/r.csv")
    assert "[=[C:/a]]b.cfx]=]" in lua and feko.lua_long_string("x]=]y]]") == "[==[x]=]y]]]==]"


def test_result_files_have_unpredictable_names(tmp_path, monkeypatch):
    from mcp_adapter.adapters.photoshop import photoshop_adapter

    monkeypatch.setattr(photoshop_adapter, "scripts_dir", lambda: tmp_path)
    a, b = photoshop_adapter._result_path(), photoshop_adapter._result_path()
    assert a != b and len(a.stem) > 30
