"""Task-area comparisons: suggestions with installed fallbacks."""
from mcp_adapter import comparisons as cmp
from mcp_adapter.catalog import KNOWN_SOFTWARE


def test_matrix_is_well_formed():
    ids = set()
    for area in cmp.COMPARISONS:
        assert area["id"] not in ids
        ids.add(area["id"])
        assert area["keywords"] and area["criteria"] and len(area["candidates"]) >= 2, area["id"]
        apps = [c["app"] for c in area["candidates"]]
        assert len(apps) == len(set(apps)), area["id"]
        for c in area["candidates"]:
            assert c["app"] in KNOWN_SOFTWARE, c["app"]
            assert c["strength"] and c["choose_when"] and c["limits"]
    assert len(cmp.COMPARISONS) >= 20


def _area(task, installed=None):
    rows = cmp.compare(task, installed)
    return rows[0] if rows else None


def test_tasks_match_the_right_area():
    assert _area("simulate a buck converter with PWM control")["area"] == "power_electronics"
    assert _area("design a dipole antenna and plot its radiation pattern")["area"] == "antenna_design"
    assert _area("RCS of an aircraft")["area"] == "antenna_placement_rcs"
    assert _area("route a 6 layer PCB and export gerber")["area"] == "pcb_layout"
    assert _area("closed form of an integral")["area"] == "symbolic_math"
    assert _area("temperature of a heat sink with forced cooling")["area"] == "thermal"
    assert _area("draw a flowchart of the test procedure")["area"] == "diagrams"
    assert cmp.compare("hello there") == []


def test_suggestion_falls_back_to_installed_candidates():
    a = _area("simulate a buck converter", installed={"orcad", "simulink"})
    assert a["suggested"] == "orcad" and a["advice"].startswith("Suggested: orcad")
    a = _area("simulate a buck converter", installed={"simulink", "matlab"})
    assert a["suggested"] == "simulink" and "not installed" in a["advice"] and "limits" in a["advice"]
    a = _area("design a dipole antenna", installed={"matlab"})
    assert a["suggested"] == "matlab" and a["candidates"][0]["app"] == "hfss" and a["candidates"][0]["installed"] is False
    a = _area("design a dipole antenna", installed=set())
    assert a["suggested"] is None and a["advice"].startswith("First choice would be hfss")
    assert "suggestion" in a["note"].lower()


def test_area_list():
    rows = cmp.area_list()
    assert {r["area"] for r in rows} == {a["id"] for a in cmp.COMPARISONS}
    assert all(len(r["candidates"]) >= 2 for r in rows)


def test_whole_word_matching_and_exclusions():
    assert _area("plot a bode diagram of a second order system")["area"] == "control_dynamic_systems"
    assert _area("photonic waveguide mode analysis")["area"] == "optics"
    assert _area("design a Wilkinson power divider")["area"] == "rf_passives_si"
    assert _area("solve the ODE y'' + y = 0 symbolically")["area"] == "symbolic_math"
    assert all(a["area"] != "photo_editing" for a in cmp.compare("photonics lab report"))


def test_context_only_candidates_are_never_offered_as_substitutes():
    a = _area("design an FIR filter", installed={"photoshop"})
    assert a["suggested"] is None and "no suitable candidate" in a["advice"]
    a = _area("draw a floor plan with dimensions", installed={"comsol", "drawio"})
    assert a["suggested"] == "drawio"
    a = _area("simulate a buck converter", installed={"comsol", "matlab"})
    assert a["suggested"] == "matlab"
    for area in cmp.COMPARISONS:
        assert area["candidates"][0]["substitute"], area["id"]


def test_an_application_named_in_the_task_wins_when_installed():
    assert cmp.named_apps("Simulate it in Simulink, then plot with MATLAB") == ["simulink", "matlab"]
    a = _area("simulate a buck converter in Simulink", installed={"orcad", "simulink"})
    assert a["suggested"] == "simulink" and a["advice"].startswith("The task asks for simulink")
    assert "usual first choice is orcad" in a["advice"]
    a = _area("simulate a buck converter in PSpice", installed={"simulink"})
    assert a["suggested"] == "simulink" and "names orcad, which is not installed" in a["advice"]
    a = _area("design a patch antenna with Photoshop", installed={"photoshop", "feko"})
    assert a["suggested"] == "feko" and "not ranked for this area" in a["advice"]


def test_find_area():
    assert cmp.find_area(" CFD ")["id"] == "cfd" and cmp.find_area("nope") is None


def test_server_tools_attach_the_comparison(monkeypatch):
    import asyncio
    import importlib

    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    from mcp_adapter import server as srv

    mod = importlib.reload(srv)
    monkeypatch.setattr(mod, "_installed_apps", lambda: {"orcad", "simulink", "matlab"})
    out = mod.recommend_application("simulate a buck converter with PWM control")
    assert out["comparison"][0]["area"] == "power_electronics" and out["comparison"][0]["suggested"] == "orcad"
    assert out["advice"].startswith("Suggested: orcad") and out["advice"].endswith("orcad_*.")
    alts = {a["application"]: a for a in out["alternatives"]}
    assert "simulink" in alts and alts["simulink"]["installed"] and "comsol" not in alts
    assert "suggestion" in out["note"].lower()
    monkeypatch.setattr(mod, "_installed_apps", lambda: {"simulink", "matlab"})
    out = mod.recommend_application("simulate a buck converter")
    assert "First choice would be orcad (not installed)" in out["advice"] and "simulink_*" in out["advice"]

    listing = mod.compare_applications()
    assert len(listing["areas"]) == len(cmp.COMPARISONS) and listing["installed"] == ["matlab", "simulink"]
    one = mod.compare_applications(area="antenna_design")
    assert one["suggested"] == "matlab" and one["candidates"][0]["installed"] is False
    assert mod.compare_applications(area="nope")["ok"] is False
    hit = mod.compare_applications("heat sink for a MOSFET")
    assert hit["matches"][0]["area"] == "thermal"
    assert mod.compare_applications("hello")["matches"] == []
    tools = {t.name for t in asyncio.new_event_loop().run_until_complete(mod.server.list_tools())}
    assert {"compare_applications", "recommend_application"} <= tools


def test_eagle_is_used_for_its_own_designs_but_not_as_a_layout_substitute():
    from mcp_adapter.domains import recommend

    assert recommend("export Gerbers from my EAGLE board")[0]["application"] == "eagle"
    assert recommend("run a ULP on the schematic")[0]["application"] == "eagle"
    assert recommend("route a 6 layer PCB with DDR")[0]["application"] == "altium"
    a = _area("export gerbers from my EAGLE board", installed={"eagle", "altium"})
    assert a["area"] == "pcb_layout" and a["suggested"] == "eagle" and "rank 4" in a["advice"]
    a = _area("route a 4 layer PCB", installed={"eagle"})
    assert a["suggested"] is None, "EAGLE's editor needs a sign-in, so it is never offered as a layout substitute"
    assert cmp.named_apps("open it in Eagle") == ["eagle"]


def test_every_area_says_which_application_is_best_for_what():
    for area in cmp.COMPARISONS:
        summary = area["summary"]
        assert 40 < len(summary) < 450, area["id"]
        first = area["candidates"][0]["app"]
        names = {"orcad": ("PSpice", "OrCAD"), "drawio": ("draw.io",), "hfss": ("HFSS",), "comsol": ("COMSOL",),
                 "matlab": ("MATLAB",), "simulink": ("Simulink",), "mathematica": ("Mathematica",),
                 "photoshop": ("Photoshop",), "altium": ("Altium",), "proteus": ("Proteus",), "vivado": ("Vivado",),
                 "autocad": ("AutoCAD",), "feko": ("Feko",), "eagle": ("EAGLE",)}[first]
        assert summary.startswith(names), f"{area['id']}: the summary should lead with the first choice"
    assert _area("route a PCB")["summary"].startswith("Altium")


def test_schematic_capture_and_3d_pcb_areas():
    a = _area("draw the schematic and make a BOM with manufacturer part numbers", installed={"altium", "eagle"})
    assert a["area"] == "schematic_capture" and a["suggested"] == "altium"
    assert [c["app"] for c in a["candidates"]][:4] == ["altium", "orcad", "proteus", "eagle"]
    assert _area("simulate the schematic of an amplifier")["area"] == "circuit_simulation"
    a = _area("realistic 3D render of the PCB with STEP models", installed={"autocad", "comsol"})
    assert a["area"] == "pcb_3d_mcad" and a["suggested"] is None, "AutoCAD/COMSOL are no PCB 3D substitutes"
    assert _area("check the enclosure collision of the board")["area"] == "pcb_3d_mcad"


def test_eagle_simulation_is_listed_but_basic():
    area = cmp.find_area("circuit_simulation")
    eagle = next(c for c in area["candidates"] if c["app"] == "eagle")
    assert eagle["substitute"] is False and "Monte Carlo" in eagle["limits"]
    a = _area("simulate the circuit with SPICE in EAGLE", installed={"eagle", "orcad"})
    assert a["suggested"] == "eagle" and "usual first choice is orcad" in a["advice"]
    pcb = next(c for c in cmp.find_area("pcb_layout")["candidates"] if c["app"] == "eagle")
    assert "Lightweight" in pcb["strength"] and "part numbers" in pcb["strength"]
