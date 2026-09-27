"""Task -> application routing and domain-tagged tool descriptions."""
import asyncio
import importlib

from mcp_adapter import domains


def _top(task, installed=None):
    rows = domains.recommend(task, installed)
    return rows[0]["application"] if rows else None


def test_recommendations_pick_specialised_tools():
    assert _top("design a patch antenna at 2.4 GHz and report S11 and gain") == "hfss"
    assert _top("simulate the transient response of an RC low-pass circuit to a 1 kHz square wave in SPICE") == "orcad"
    assert _top("thermal stress of a heated aluminium beam with convection") == "comsol"
    assert _top("route the PCB, generate Gerber files and the BOM") == "altium"
    assert _top("synthesize this Verilog design for an Artix-7 FPGA and generate the bitstream") == "vivado"
    assert _top("find the closed form of the integral of x^2 e^{-x} and simplify") == "mathematica"
    assert _top("design a Butterworth IIR filter and plot its frequency response") == "matlab"
    assert _top("simulate the Simulink model vdp.slx for 20 seconds") == "simulink"
    assert _top("run the Arduino firmware with the LCD in simulation") == "proteus"
    assert _top("export this DWG drawing to DXF") == "autocad"
    assert _top("draw a flowchart of the release process") == "drawio"
    assert _top("batch resize and retouch these photos") == "photoshop"
    assert domains.recommend("hello there") == []


def test_recommendations_for_capabilities_added_with_the_larger_catalogs():
    assert _top("BER curve of a 16-QAM OFDM link over an AWGN channel") == "matlab"
    assert _top("fuse IMU and GPS data with a Kalman filter for a UAV") == "matlab"
    assert _top("train a reinforcement learning agent for a cart-pole") == "matlab"
    assert _top("discharge curve and heating of a lithium-ion battery cell") == "comsol"
    assert _top("topology optimization of a bracket under load") == "comsol"
    assert _top("Floquet unit cell of a reflectarray element in HFSS") == "hfss"
    assert _top("indoor radio coverage map and path loss with WinProp") == "feko"
    assert _top("phase margin and Monte Carlo yield of an op-amp circuit in PSpice") == "orcad"
    assert _top("set a creepage rule and run DRC on the PCB") == "altium"
    assert _top("simulate a Raspberry Pi Pico running MicroPython with an OLED") == "proteus"
    assert _top("create a partial reconfiguration (DFX) region and a BOOT.BIN with bootgen") == "vivado"
    assert _top("add a Stateflow state machine and generate AUTOSAR code") == "simulink"
    assert _top("shortest path and centrality of a graph, exact uncertainty propagation") == "mathematica"
    assert _top("update the attribute values of a dynamic block and batch plot the sheet set") == "autocad"
    assert _top("use Generative Fill and Camera Raw on a raw photo") == "photoshop"
    assert _top("AWS architecture diagram from a CSV") == "drawio"


def test_comsol_covers_the_breadth_of_multiphysics():
    for task in ("induction heating of a steel billet", "sound transmission loss of a car muffler",
                 "Li-ion battery thermal runaway in a pack", "groundwater contaminant transport in an aquifer",
                 "electron gun beam trajectories", "semiconductor pn junction doping profile",
                 "DC corona discharge in air", "fluid-structure interaction of a flexible flap",
                 "quenching and phase transformation of a steel gear", "microwave heating of food in an oven"):
        assert _top(task) == "comsol", task
    rows = domains.recommend("design a patch antenna at 2.4 GHz", installed={"comsol"})
    assert rows[0]["application"] == "hfss" and any(r["application"] == "comsol" and r["installed"] for r in rows)


def test_capability_areas_come_from_the_catalog():
    for app in APP_PROFILES_IDS:
        p = domains.profile(app, with_areas=True)
        assert p["capability_areas"] and p["catalog_entries_total"] >= 100, app
        assert all(a["catalog_entries"] > 0 for a in p["capability_areas"])


APP_PROFILES_IDS = sorted(domains.APP_PROFILES)


def test_installed_flag_and_note():
    rows = domains.recommend("patch antenna S11", installed={"matlab"})
    assert rows[0]["application"] == "hfss" and rows[0]["installed"] is False and "not installed" in rows[0]["note"]
    rows = domains.recommend("patch antenna S11", installed={"hfss"})
    assert rows[0]["installed"] is True and "note" not in rows[0]


def test_profiles_cover_all_apps():
    from mcp_adapter.catalog import KNOWN_SOFTWARE

    for app in KNOWN_SOFTWARE:
        p = domains.profile(app)
        assert p["tag"] and p["best_for"] and p["typical_tasks"], app
    assert "ROUTING" not in domains.ROUTING_GUIDE and "HFSS" in domains.ROUTING_GUIDE


def test_tool_descriptions_carry_domain_tags(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "true")
    from mcp_adapter import server as srv

    mod = importlib.reload(srv)
    tools = {t.name: t for t in asyncio.new_event_loop().run_until_complete(mod.server.list_tools())}
    assert tools["hfss_run_script"].description.startswith("[Ansys HFSS:")
    assert tools["orcad_pspice_simulate"].description.startswith("[OrCAD PSpice")
    assert tools["matlab_run_code"].description.startswith("[MATLAB:")
    assert "recommend_application" in tools and "antennas -> HFSS" in tools["recommend_application"].description
    assert "Choosing the right application" in mod.server.instructions
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    importlib.reload(srv)


def test_recommend_tool_reports_installation(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    from mcp_adapter import server as srv

    mod = importlib.reload(srv)
    out = mod.recommend_application("simulate a patch antenna at 2.4 GHz")
    assert out["candidates"][0]["application"] == "hfss" and "advice" in out
    assert mod.list_software()["software"][0]["best_for"]
    assert "best_for" in mod.software_overview("hfss")
