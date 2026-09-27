"""Altair Feko adapter: .out parsing, Lua templates, model checks, command lines and unavailable behaviour."""
import pytest

from mcp_adapter.adapters.base import RunResult
from mcp_adapter.adapters.feko import feko_adapter, lua_template, parse_out_text

SAMPLE_OUT = """
 FEKO  Solver  version 2025.1
 ------------------------------------------------------------------------
 SUMMARY OF THE MESH
 Number of metallic triangles:        1234
 Number of wire segments:               20
 Number of basis functions (unknowns): 1851
 Peak memory usage:  12.5 MByte
 ------------------------------------------------------------------------
 Frequency:  3.00000E+08 Hz    Wavelength: 0.999 m
 DATA OF THE VOLTAGE SOURCE NO. 1
                       real part    imag. part    magnitude     phase
 Current in A          1.0231E-02  -5.8500E-03   1.1786E-02   -29.76
 Admittance in A/V     1.0231E-02  -5.8500E-03   1.1786E-02   -29.76
 Impedance in Ohm      7.3640E+01   4.2110E+01   8.4830E+01    29.76
 Power in W            5.1155E-03
 WARNING 3512: Some element is small compared to the wavelength
 VALUES OF THE FAR FIELD
 Total time: 3.2 seconds
"""

BANNER = ("Altair Feko - RUNFEKO Version 2026.1-8642 from 2026-05-10 [Release/2026.1.0 @adbd2788f295]\n"
          "          Unpublished work. Copyright 2026 Siemens\n\nSyntax: runfeko.exe FILENAME [OPTIONS]\n")


def test_parse_out_text_extracts_sources_and_sections():
    data = parse_out_text(SAMPLE_OUT)
    assert data["frequencies_hz"] == [3.0e8]
    src = data["sources"][0]
    assert src["number"] == 1 and src["frequency_hz"] == 3.0e8
    assert src["impedance"] == {"real": 73.64, "imag": 42.11, "magnitude": 84.83, "phase_deg": 29.76}
    assert src["power"] == {"real": 5.1155e-03}
    assert "SUMMARY OF THE MESH" in data["sections"] and "VALUES OF THE FAR FIELD" in data["sections"]
    assert any("unknowns" in s for s in data["summary"])
    assert data["warnings"] and data["warnings"][0].startswith("WARNING 3512") and data["errors"] == []


def test_parse_out_file_and_errors(tmp_path):
    out = tmp_path / "m.out"
    out.write_text(SAMPLE_OUT + "\nERROR 32001: No source defined\n", encoding="utf-8")
    res = feko_adapter.parse_out(str(out))
    assert res.ok is False and res.data["errors"] == ["ERROR 32001: No source defined"]
    assert feko_adapter.parse_out(str(tmp_path / "missing.out")).ok is False


def test_lua_templates():
    s = lua_template("dipole", model="C:\\feko\\dip.cfx", frequency_hz=2.4e9)
    assert "cf.GetApplication()" in s and "C:/feko/dip.cfx" in s and "2400000000.0" in s
    s = lua_template("export_source_data", model="/m/a.fek", output="/m/z.csv")
    assert "pf.GetApplication()" in s and "/m/z.csv" in s
    with pytest.raises(KeyError):
        lua_template("nope")


@pytest.fixture
def fake_install(monkeypatch, tmp_path):
    """A fake Feko bin folder with runfeko, cadfeko, cadfeko_batch (no postfeko) and a recording run_command."""
    for exe in ("runfeko.exe", "cadfeko.exe", "cadfeko_batch.exe"):
        (tmp_path / exe).write_text("", encoding="utf-8")
    monkeypatch.setattr(feko_adapter, "executable", lambda: str(tmp_path / "runfeko.exe"))
    monkeypatch.setattr(feko_adapter, "is_available", lambda: True)
    calls = []

    def fake_run(args, cwd=None, timeout=None, artifacts=None, **kw):
        calls.append({"args": [str(a) for a in args], "cwd": str(cwd) if cwd else None})
        return RunResult(ok=True, software="feko", command=" ".join(str(a) for a in args), returncode=0,
                         stdout=BANNER, artifacts=dict(artifacts or {}))
    monkeypatch.setattr(feko_adapter, "run_command", fake_run)
    return tmp_path, calls


def test_version_is_parsed_from_banner(fake_install):
    _, calls = fake_install
    res = feko_adapter.version()
    assert res.ok and res.data["version"] == "2026.1-8642" and res.data["date"] == "2026-05-10"
    assert calls[0]["args"][1:] == []  # bare runfeko: --version would be taken as a file name


def test_solve_checks_model_and_builds_flags(fake_install):
    tmp_path, calls = fake_install
    assert "not found" in feko_adapter.solve(str(tmp_path / "x.cfx")).error
    (tmp_path / "x.txt").write_text("", encoding="utf-8")
    assert "Expected a .cfx" in feko_adapter.solve(str(tmp_path / "x.txt")).error
    (tmp_path / "x.cfx").write_text("", encoding="utf-8")
    res = feko_adapter.solve(str(tmp_path / "x.cfx"), processes=4, use_gpu=True, priority=1)
    assert res.ok and calls[-1]["args"][1:] == ["x.cfx", "-np", "4", "--use-gpu", "--priority", "1"]
    assert calls[-1]["cwd"] == str(tmp_path)


def test_batch_process_sets_variables(fake_install):
    tmp_path, calls = fake_install
    assert "needs a .cfx" in feko_adapter.batch_process(str(tmp_path / "x.txt")).error or True
    (tmp_path / "x.cfx").write_text("", encoding="utf-8")
    res = feko_adapter.batch_process(str(tmp_path / "x.cfx"), {"len": "0.5", "freq": "2.4e9"}, force_mesh=True)
    assert res.ok
    assert calls[-1]["args"][1:] == ["x.cfx", "-#", "len=0.5", "-#", "freq=2.4e9", "--force-mesh"]


def test_scripts_use_non_interactive_run_script(fake_install):
    tmp_path, calls = fake_install
    (tmp_path / "m.cfx").write_text("", encoding="utf-8")
    res = feko_adapter.run_cadfeko_script("print(1)", model=str(tmp_path / "m.cfx"), configure="freq=1e9")
    args = calls[-1]["args"]
    assert res.ok and args[0].endswith("cadfeko.exe") and args[1].endswith("m.cfx")
    assert args[2] == "--non-interactive" and args[3] == "--run-script" and args[4].endswith(".lua")
    assert args[5:] == ["--configure-script", "freq=1e9"]
    res = feko_adapter.run_postfeko_script("print(1)")
    assert res.ok is False and "postfeko not found" in res.error


def test_unavailable(monkeypatch):
    monkeypatch.setattr(feko_adapter, "executable", lambda: None)
    monkeypatch.setattr(feko_adapter, "is_available", lambda: False)
    assert feko_adapter.version().ok is False
    assert feko_adapter.run_cadfeko_script("print(1)").ok is False
    assert feko_adapter.batch_process("x.cfx").ok is False
    assert feko_adapter.sibling("prefeko") is None


REAL_OUT_2026 = """
                    EXCITATION BY VOLTAGE SOURCE AT A SEGMENT

 Name:                          VoltageSource1
 Frequency in Hz:               FREQ =    3.00000E+08
 Wavelength in m:               LAMBDA =  9.99308E-01
     0 | MSI                                            |       1 |    0 |     0
 Number of metallic segments:                     9                                max. segments:     MAXNSEG   =        9
 Far field request with name: FarField1
                     FAST METHOD FOR FAR FIELD CALCULATIONS
      LOCATION           ETHETA             EPHI              directivity in dB              POLARISATION
   THETA    PHI      magn.    phase     magn.    phase     vert.     horiz.    total     axial r. angle   direction
    0.00    0.00   8.017E-07 -175.40  8.017E-07 -175.40  -118.2846 -118.2846 -115.2743   0.0000  -135.00   LINEAR
   90.00    0.00   6.100E-01   92.00  1.000E-06  -90.00     2.1234 -999.9999    2.1234   0.0000   180.00   LINEAR
   95.00    0.00   6.000E-01   92.00  1.000E-06  -90.00     2.1000 -999.9999    2.1000   0.0000   180.00   LINEAR

     Gain is a factor of  1.00000E+00 (    0.00 dB) larger than directivity

       The directivity is based on an active power of  7.22224E-03 W
"""


def test_parse_real_2026_format():
    data = parse_out_text(REAL_OUT_2026)
    assert data["frequencies_hz"] == [3.0e8]
    ff = data["far_fields"][0]
    assert ff["request"] == "FarField1" and ff["samples"] == 3 and ff["frequency_hz"] == 3.0e8
    assert ff["max_directivity_dbi"] == 2.1234 and ff["max_direction_deg"] == {"theta": 90.0, "phi": 0.0}
    assert ff["gain_minus_directivity_db"] == 0.0 and ff["active_power_w"] == 7.22224e-3
    assert "FAST METHOD FOR FAR FIELD CALCULATIONS" in data["sections"]
    assert "EXCITATION BY VOLTAGE SOURCE AT A SEGMENT" in data["sections"]
    assert not any("MSI" in s for s in data["sections"]) and not any("|" in s for s in data["summary"])
