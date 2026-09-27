"""EAGLE adapter: XML design reading, BOM/netlist, CAM command lines and the sign-in guard (no EAGLE needed)."""
import csv
from pathlib import Path

import pytest

from mcp_adapter.adapters import eagle as eg
from mcp_adapter.adapters.base import RunResult
from mcp_adapter.adapters.eagle import eagle_adapter

SCH = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="9.6.0"><drawing>
<layers><layer number="91" name="Nets" color="2" fill="1" visible="yes" active="yes"/>
<layer number="94" name="Symbols" color="4" fill="1" visible="yes" active="yes"/></layers>
<schematic><libraries>
<library name="rcl"><packages><package name="R0402"/><package name="C0402"/></packages>
<devicesets>
<deviceset name="R-EU_" prefix="R"><devices><device name="R0402" package="R0402"/></devices></deviceset>
<deviceset name="C-EU" prefix="C"><devices><device name="C0402" package="C0402"/></devices></deviceset>
</devicesets></library>
<library name="frames"><devicesets><deviceset name="A4L-LOC"><devices><device name=""/></devices></deviceset></devicesets></library>
</libraries>
<variantdefs><variantdef name="lite"/></variantdefs>
<parts>
<part name="FRAME1" library="frames" deviceset="A4L-LOC" device=""/>
<part name="R2" library="rcl" deviceset="R-EU_" device="R0402" value="10k"><attribute name="MPN" value="ERJ-2RKF1002X"/><attribute name="MANUFACTURER" value="Panasonic"/></part>
<part name="R10" library="rcl" deviceset="R-EU_" device="R0402" value="10k"><attribute name="MPN" value="ERJ-2RKF1002X"/><attribute name="MANUFACTURER" value="Panasonic"/></part>
<part name="R1" library="rcl" deviceset="R-EU_" device="R0402" value="10k"><attribute name="MPN" value="ERJ-2RKF1002X"/><attribute name="MANUFACTURER" value="Panasonic"/></part>
<part name="C1" library="rcl" deviceset="C-EU" device="C0402"/>
</parts>
<sheets><sheet><instances/><nets>
<net name="VCC" class="0"><segment><pinref part="R1" gate="G$1" pin="1"/><pinref part="C1" gate="G$1" pin="1"/></segment></net>
<net name="GND" class="0"><segment><pinref part="R2" gate="G$1" pin="2"/></segment><segment><pinref part="C1" gate="G$1" pin="2"/></segment></net>
</nets></sheet><sheet/></sheets>
</schematic></drawing></eagle>
"""

BRD = """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.0"><drawing>
<layers><layer number="1" name="Top" color="4" fill="1" visible="yes" active="yes"/>
<layer number="16" name="Bottom" color="1" fill="1" visible="yes" active="yes"/>
<layer number="20" name="Dimension" color="24" fill="1" visible="yes" active="yes"/></layers>
<board><plain>
<wire x1="0" y1="0" x2="50" y2="0" width="0" layer="20"/><wire x1="50" y1="0" x2="50" y2="30" width="0" layer="20"/>
<wire x1="50" y1="30" x2="0" y2="30" width="0" layer="20"/><wire x1="0" y1="30" x2="0" y2="0" width="0" layer="20"/>
</plain>
<libraries><library name="rcl"/></libraries>
<designrules name="default"><param name="layerSetup" value="(1*2*15*16)"/><param name="mdWireWire" value="8mil"/></designrules>
<elements>
<element name="R1" library="rcl" package="R0402" value="10k" x="10" y="5" rot="R90"><attribute name="MPN" value="ERJ-2RKF1002X"/></element>
<element name="C1" library="rcl" package="C0402" value="100n" x="20" y="5"/>
</elements>
<signals>
<signal name="VCC"><contactref element="R1" pad="1"/><contactref element="C1" pad="1"/><wire x1="10" y1="5" x2="20" y2="5" width="0.2" layer="1"/></signal>
<signal name="GND"><contactref element="R1" pad="2"/><contactref element="C1" pad="2"/></signal>
</signals></board></drawing></eagle>
"""


@pytest.fixture
def designs(tmp_path):
    (tmp_path / "demo.sch").write_text(SCH, encoding="utf-8")
    (tmp_path / "demo.brd").write_text(BRD, encoding="utf-8")
    (tmp_path / "old.sch").write_bytes(b"\x10\x80\x00\x00binary pre-6.0 data")
    return tmp_path


def test_read_schematic_board_and_bom(designs):
    s = eg.read_design(designs / "demo.sch")
    assert s["kind"] == "schematic" and s["sheets"] == 2 and s["part_count"] == 5 and s["variants"] == ["lite"]
    c1 = next(p for p in s["parts"] if p["name"] == "C1")
    assert c1["package"] == "C0402" and c1["value"] == "C-EUC0402", "no user value -> device set + device"
    assert s["nets"][1] == {"name": "GND", "class": "0", "pins": ["R2.2", "C1.2"]}

    b = eg.read_design(designs / "demo.brd")
    assert b["kind"] == "board" and b["copper_layers"] == 4 and b["size_mm"] == [50.0, 30.0]
    assert b["elements"][0] == {"name": "R1", "value": "10k", "library": "rcl", "package": "R0402", "x_mm": 10.0,
                                "y_mm": 5.0, "rotation": "R90", "attributes": {"MPN": "ERJ-2RKF1002X"}}
    assert b["unrouted_signals"] == ["GND"] and b["design_rules"]["mdWireWire"] == "8mil"

    out = eg.bom(designs / "demo.sch", str(designs / "out" / "bom.csv"))
    assert out["parts"] == 4 and out["lines"] == 2, "the frame has no package and is not assembled"
    r = next(i for i in out["items"] if i["mpn"])
    assert r["designators"] == ["R1", "R2", "R10"] and r["quantity"] == 3 and r["manufacturer"] == "Panasonic"
    rows = list(csv.reader(open(out["csv"], encoding="utf-8")))
    assert rows[0][0] == "Quantity" and len(rows) == 3


def test_netlist_and_errors(designs):
    n = eg.netlist(designs / "demo.brd", str(designs / "nets.txt"))
    assert n["net_count"] == 2 and n["nets"][0]["nodes"] == ["R1.1", "C1.1"]
    assert (designs / "nets.txt").read_text(encoding="utf-8").startswith("VCC\tR1.1 C1.1")
    with pytest.raises(ValueError, match="binary format"):
        eg.read_design(designs / "old.sch")
    with pytest.raises(ValueError, match=r"\.sch, \.brd or \.lbr"):
        eg.read_design(designs / "nets.txt")
    with pytest.raises(FileNotFoundError):
        eg.read_design(designs / "missing.brd")


def test_layer_count_and_devices(designs):
    assert eg.copper_layer_count(designs / "demo.brd") == 4
    text = '[GERBER_RS274X]\n\n@GERBERAUTO\nLong = "Gerber RS-274-X photoplotter"\n[EXCELLON]\nType = DrillStation\nLong = "Excellon drill station"\n'
    assert eg.parse_devices(text) == [
        {"name": "GERBER_RS274X", "type": "", "description": "Gerber RS-274-X photoplotter"},
        {"name": "EXCELLON", "type": "DrillStation", "description": "Excellon drill station"}]


@pytest.fixture
def fake_eagle(monkeypatch, tmp_path):
    root = tmp_path / "EAGLE 9.6.0"
    (root / "examples" / "cam" / "examples").mkdir(parents=True)
    for n in (2, 4):
        (root / "examples" / "cam" / "examples" / f"example_{n}_layer.cam").write_text("{}", encoding="utf-8")
    exe = root / "eaglecon.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(eagle_adapter, "executable", lambda: str(exe))
    monkeypatch.setattr(eagle_adapter, "is_available", lambda: True)
    monkeypatch.setattr(eagle_adapter, "scripts_dir", lambda: tmp_path / "out")
    calls = []

    def fake_run(args, cwd=None, timeout=None, **kw):
        calls.append([str(a) for a in args])
        if "-?" in args:
            return RunResult(ok=False, software="eagle", command="", returncode=1,
                             stdout="EAGLE Version 9.6.0 Copyright (c) 1988-2020 Autodesk, Inc.\nSyntax: eagle ...")
        out = next(a[2:] for a in map(str, args) if a.startswith("-o"))
        target = Path(out)
        if "-dCAMJOB" in args:
            (target / "CAMOutputs" / "GerberFiles").mkdir(parents=True, exist_ok=True)
            (target / "CAMOutputs" / "GerberFiles" / "copper_top.gbr").write_text("G04*", encoding="utf-8")
        else:
            target.write_text("M48", encoding="utf-8")
        return RunResult(ok=True, software="eagle", command="", returncode=0)

    monkeypatch.setattr(eagle_adapter, "run_command", fake_run)
    return tmp_path, calls


def test_version_and_cam_job_command_lines(fake_eagle, designs):
    tmp_path, calls = fake_eagle
    v = eagle_adapter.version()
    assert v.ok and v.data["version"] == "9.6.0", "eaglecon -? exits non-zero but prints the banner"
    res = eagle_adapter.cam_job(str(designs / "demo.brd"), variant="lite")
    assert res.ok and res.data["gerber_files"][0].endswith("copper_top.gbr")
    args = calls[-1]
    assert args[1:4] == ["-X", "-N", "-dCAMJOB"] and args[4].endswith("example_4_layer.cam"), "4 copper layers"
    assert "-Alite" in args and args[-1].endswith("demo.brd") and "used the shipped job" in res.data["note"]
    assert eagle_adapter.cam_job(str(designs / "demo.brd"), job="example_2_layer").artifacts["cam_job"].endswith(
        "example_2_layer.cam")
    assert not eagle_adapter.cam_job(str(designs / "demo.sch")).ok
    leg = eagle_adapter.cam_output(str(designs / "demo.brd"), "EXCELLON", [44, 45], str(tmp_path / "x" / "d.drd"))
    assert leg.ok and calls[-1][-3:] == [str(designs / "demo.brd"), "44", "45"]


def test_editor_route_reports_the_sign_in_window(fake_eagle, designs, monkeypatch):
    class FakeProc:
        pid, returncode = 4242, None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def communicate(self, timeout=None):
            return "", ""

    started = []
    monkeypatch.setattr(eg.subprocess, "Popen", lambda args, **kw: started.append(args) or FakeProc())
    monkeypatch.setattr(eg, "blocking_window", lambda pid: "Sign in")
    res = eagle_adapter.run_commands(str(designs / "demo.brd"), "RUN bom.ulp")
    assert not res.ok and "Sign in" in res.error and "never performs" in res.error
    assert started[0][2:4] == ["-C", "RUN bom.ulp; QUIT;"]
    assert not eagle_adapter.run_commands(str(designs / "nets.txt"), "QUIT").ok
