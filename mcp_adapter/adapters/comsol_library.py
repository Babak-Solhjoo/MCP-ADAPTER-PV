"""Index of the COMSOL installation: installed modules, their User's Guides and the Application Library examples.

Everything is read from the local installation (no network):
* ``<root>/applications/<Module>/<Topic>/<name>.mph``        – example models (about 2,000 in COMSOL 6.4);
* ``<root>/doc/help/wtpwebapps/ROOT/doc/com.comsol.help.models.<code>.<name>/`` – per-example documentation
  (``<name>.html`` with title and introduction, ``<name>.java`` that builds and solves the model step by step,
  ``models.<code>.<name>.pdf``);
* ``<root>/doc/pdf/<Module>/*.pdf``                           – module User's Guides.

The index is cached as JSON in the adapter's output folder and rebuilt when the installation changes.
"""
from __future__ import annotations

import html
import json
import os
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

HELP_REL = Path("doc") / "help" / "wtpwebapps" / "ROOT" / "doc"
INDEX_VERSION = 4
_PHYS_RE = re.compile(r'(?<!multi)physics\(\)\.create\("([^"]+)",\s*"([^"]+)"')
_MULTI_RE = re.compile(r'multiphysics\(\)\.create\("([^"]+)",\s*"([^"]+)"')
_STUDY_RE = re.compile(r'study\("[^"]+"\)\.create\("([^"]+)",\s*"([^"]+)"')
_PATH_RE = re.compile(r"Application Library path:\s*([A-Za-z0-9_,\-./ ]+?)(?:\s{2,}|$| Modeling Instructions| Appendix| From the)")


# User-facing names of the most common COMSOL API type strings (physics interfaces, couplings, study steps).
TYPE_NAMES = {
    "SolidMechanics": "Solid Mechanics", "LaminarFlow": "Laminar Flow",
    "ElectromagneticWaves": "Electromagnetic Waves, Frequency Domain",
    "ElectromagneticWavesFrequencyDomain": "Electromagnetic Waves, Frequency Domain (optics)",
    "HeatTransfer": "Heat Transfer in Solids", "HeatTransferInFluids": "Heat Transfer in Fluids",
    "HeatTransferInSolidsAndFluids": "Heat Transfer in Solids and Fluids", "Electrostatics": "Electrostatics",
    "InductionCurrents": "Magnetic Fields", "PressureAcoustics": "Pressure Acoustics, Frequency Domain",
    "Shell": "Shell", "DilutedSpecies": "Transport of Diluted Species", "GeometricalOptics": "Geometrical Optics",
    "Semiconductor": "Semiconductor", "ConductiveMedia": "Electric Currents", "MultibodyDynamics": "Multibody Dynamics",
    "ReactionEng": "Reaction Engineering", "GlobalEquations": "Global ODEs and DAEs", "Circuit": "Electrical Circuit",
    "LithiumIonBatteryMPH": "Lithium-Ion Battery", "PorousMediaFlowDarcy": "Darcy's Law", "Chemistry": "Chemistry",
    "ColdPlasma": "Plasma", "SecondaryCurrentDistribution": "Secondary Current Distribution",
    "TertiaryCurrentDistributionNernstPlanck": "Tertiary Current Distribution, Nernst-Planck", "Fatigue": "Fatigue",
    "ElectricDischarge": "Electric Discharge", "LayeredShell": "Layered Shell", "HydrogenFuelCell": "Hydrogen Fuel Cell",
    "ElectromagneticWavesBeamEnvelopes": "Electromagnetic Waves, Beam Envelopes",
    "TurbulentFlowkeps": "Turbulent Flow, k-epsilon", "ElectricInductionCurrents": "Magnetic and Electric Fields",
    "ThermoacousticsSinglePhysics": "Thermoviscous Acoustics, Frequency Domain",
    "FreeAndPorousMediaFlow": "Free and Porous Media Flow", "MagnetostaticsNoCurrents": "Magnetic Fields, No Currents",
    "CoefficientFormPDE": "Coefficient Form PDE", "ChargedParticleTracing": "Charged Particle Tracing",
    "HermitianBeam": "Beam", "DilutedSpeciesInPorousMedia": "Transport of Diluted Species in Porous Media",
    "DomainODE": "Domain ODEs and DAEs", "SurfaceToSurfaceRadiation": "Surface-to-Surface Radiation",
    "HydrodynamicBearing": "Hydrodynamic Bearing", "StructuralMembrane": "Membrane",
    "FluidParticleTracing": "Particle Tracing for Fluid Flow", "Events": "Events",
    "FreeMolecularFlow": "Free Molecular Flow", "ColdPlasmaTimePeriodic": "Plasma, Time Periodic",
    "GeneralFormPDE": "General Form PDE", "ConcentratedSpecies": "Transport of Concentrated Species",
    "PorousMediaHeatTransfer": "Heat Transfer in Porous Media", "BeamRotor": "Beam Rotor",
    "SchrodingerEquation": "Schroedinger Equation", "TurbulentFlowSST": "Turbulent Flow, SST", "LevelSet": "Level Set",
    "TurbulentFlowkomega": "Turbulent Flow, k-omega", "Truss": "Truss", "GranularFlow": "Granular Flow",
    "LinearizedPotentialFlowFrequency": "Linearized Potential Flow, Frequency Domain",
    "TransientPressureAcoustics": "Pressure Acoustics, Transient", "LumpedBattery": "Lumped Battery",
    "ThinFilmFlowShell": "Thin-Film Flow, Shell", "CreepingFlow": "Creeping Flow",
    "CurvilinearCoordinates": "Curvilinear Coordinates", "AqueousElectrolyteTransport": "Aqueous Electrolyte Transport",
    "WaterElectrolyzer": "Water Electrolyzer", "PorousMediaFlowRichards": "Richards' Equation",
    "SolidMechanicsExplicit": "Solid Mechanics, Explicit Dynamics",
    "TransientElectromagneticWaves": "Electromagnetic Waves, Transient",
    "ElectricCurrentsShell": "Electric Currents in Shells", "RotatingMachineryMagnetic": "Rotating Machinery, Magnetic",
    "MultiphaseFlowMixtureModel": "Mixture Model", "PorousMediaFlowBrinkman": "Brinkman Equations",
    "HeatTransferInShellsLM": "Heat Transfer in Shells", "CathodicProtection": "Cathodic Protection",
    "PoroelasticWavesSinglePhysics": "Poroelastic Waves",
    "PressureAcousticsBoundaryElements": "Pressure Acoustics, Boundary Elements",
    "PrimaryCurrentDistribution": "Primary Current Distribution", "TertiaryElectroanalysis": "Electroanalysis",
    "TransportOfChargeCarriers": "Charge Transport in Solids", "MathParticle": "Mathematical Particle Tracing",
    "BoltzmannEquation": "Boltzmann Equation, Two-Term Approximation",
    "MagneticFieldsNoCurrentsBoundaryElements": "Magnetic Fields, No Currents, Boundary Elements",
    "CompressiblePotentialFlow": "Compressible Potential Flow", "ElasticWavesTimeExplicit": "Elastic Waves, Time Explicit",
    "ThermoacousticsSinglePhysicsTransient": "Thermoviscous Acoustics, Transient",
    "TransportInSolids": "Transport in Solids", "FlowInPipes": "Pipe Flow", "PhaseTransportFree": "Phase Transport",
    "PhaseField": "Phase Field", "ThinFilmFlowEdge": "Thin-Film Flow, Edge",
    "PopulationBalanceSizeBased": "Population Balance", "MultiphaseFlowInPorousMedia": "Multiphase Flow in Porous Media",
    "TurbulentFlowAlgebraicYplus": "Turbulent Flow, Algebraic yPlus",
    "ParticipatingMediaRadiation": "Radiation in Participating Media", "AusteniteDecomposition": "Austenite Decomposition",
    "NonisothermalPipeFlow": "Nonisothermal Pipe Flow", "TransmissionLine": "Transmission Line",
    "ElectrostaticsBoundaryElements": "Electrostatics, Boundary Elements",
    "LinearizedNavierStokesFrequency": "Linearized Navier-Stokes, Frequency Domain", "RayAcoustics": "Ray Acoustics",
    "PressureAcousticsTimeExplicit": "Pressure Acoustics, Time Explicit",
    # multiphysics couplings
    "NonIsothermalFlow": "Nonisothermal Flow", "ElectromagneticHeating": "Electromagnetic Heating",
    "PiezoelectricEffect": "Piezoelectric Effect", "ReactingFlowDS": "Reacting Flow",
    "ElectromechanicalForces": "Electromechanical Forces", "ThermalExpansion": "Thermal Expansion",
    "AcousticStructureBoundary": "Acoustic-Structure Boundary", "ElectrochemicalHeating": "Electrochemical Heating",
    "Magnetohydrodynamics": "Magnetohydrodynamics", "Magnetomechanics": "Magnetomechanics",
    "FluidStructureInteractionBC": "Fluid-Structure Interaction",
    "HeatTransferWithSurfaceToSurfaceRadiation": "Heat Transfer with Surface-to-Surface Radiation",
    "SolidShellConnection": "Solid-Shell Connection", "SolidBeamConnection3D": "Solid-Beam Connection",
    "ShellBeamConnection": "Shell-Beam Connection", "PoroelasticCoupling": "Poroelasticity",
    "HeatAndMoisture": "Heat and Moisture",
    # study steps
    "Stationary": "Stationary", "Transient": "Time Dependent", "Parametric": "Parametric Sweep",
    "Frequency": "Frequency Domain", "Eigenfrequency": "Eigenfrequency",
    "CurrentDistributionInitialization": "Current Distribution Initialization", "RayTracing": "Ray Tracing",
    "Frequencylinearized": "Frequency Domain, Perturbation", "WallDistanceInitialization": "Wall Distance Initialization",
    "FrequencyAdaptive": "Adaptive Frequency Sweep", "Wavelength": "Wavelength Domain",
    "SemiconductorEquilibrium": "Semiconductor Equilibrium", "BoundaryModeAnalysis": "Boundary Mode Analysis",
    "FrequencyTransient": "Frequency-Transient", "ShapeOptimization": "Shape Optimization",
    "LSQOptimization": "Parameter Estimation", "ModeAnalysis": "Mode Analysis",
    "CoilCurrentCalculation": "Coil Geometry Analysis", "FrequencyStationary": "Frequency-Stationary",
    "Optimization": "Optimization", "FrozenRotor": "Frozen Rotor", "ParameterOptimization": "Parameter Optimization",
    "TopologyOptimization": "Topology Optimization", "TimeToFreqFFT": "Time to Frequency FFT",
    "LinearBuckling": "Linear Buckling", "Frequencymodal": "Frequency Domain, Modal", "TimePeriodic": "Time Periodic",
    "ExplicitDynamics": "Explicit Dynamics", "UncertaintyQuantification": "Uncertainty Quantification",
    "ModelReduction": "Model Reduction", "BoltPretension": "Bolt Pretension", "CyclicVoltammetry": "Cyclic Voltammetry",
}


def type_name(api_type: str) -> str:
    """'ConductiveMedia' -> 'Electric Currents'; unknown types are returned unchanged."""
    return TYPE_NAMES.get(api_type, api_type)


@dataclass
class Example:
    name: str                      # file stem, e.g. "busbar_terminal"
    module: str                    # "ACDC_Module"
    topic: str                     # "Introductory_Electric_Currents"
    library_path: str              # "ACDC_Module/Introductory_Electric_Currents/busbar_terminal"
    mph: str
    title: str = ""
    intro: str = ""
    java: str = ""
    pdf: str = ""
    html: str = ""
    physics: list[str] = field(default_factory=list)       # "es: Electrostatics"
    multiphysics: list[str] = field(default_factory=list)
    studies: list[str] = field(default_factory=list)       # study step types
    size_mb: float = 0.0
    preview: bool = False          # library preview file: full model must be downloaded in COMSOL


def find_root(executable: str | None) -> Path | None:
    """COMSOL 'Multiphysics' folder (the one containing applications/ and doc/)."""
    env = os.environ.get("COMSOL_ROOT")
    if env and (Path(env) / "applications").is_dir():
        return Path(env)
    if not executable:
        return None
    for parent in Path(executable).resolve().parents:
        if (parent / "applications").is_dir() and (parent / "doc").is_dir():
            return parent
    return None


def _text(raw: str) -> str:
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _parse_help(folder: Path) -> dict:
    info: dict = {}
    pages = sorted(folder.glob("*.html"))
    if pages:
        raw = pages[0].read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<title>(.*?)</title>", raw, re.S)
        if m:
            info["title"] = re.sub(r"^COMSOL\s+[\d.]+\s*-\s*", "", html.unescape(m.group(1)).strip())
        text = _text(raw[:120_000])
        pm = _PATH_RE.search(text)
        if pm:
            info["library_path"] = pm.group(1).strip()
        start = text.find("Introduction ")
        if start >= 0:
            intro = text[start + len("Introduction "):]
            for stop in (" Model Definition", " Figure 1", " Results and Discussion", " Application Library path"):
                k = intro.find(stop)
                if k > 0:
                    intro = intro[:k]
            info["intro"] = intro[:700].strip()
        info["html"] = str(pages[0])
    javas = sorted(folder.glob("*.java"))
    if javas:
        src = javas[0].read_text(encoding="utf-8", errors="replace")
        info["java"] = str(javas[0])
        info["physics"] = sorted({f"{t}: {k}" for t, k in _PHYS_RE.findall(src)})
        info["multiphysics"] = sorted({f"{t}: {k}" for t, k in _MULTI_RE.findall(src)})
        info["studies"] = sorted({k for _, k in _STUDY_RE.findall(src)})
    pdfs = sorted(folder.glob("*.pdf"))
    if pdfs:
        info["pdf"] = str(pdfs[0])
    return info


def _is_preview(mph: Path) -> bool:
    """Application Library 'preview' files hold only an image and metadata; COMSOL downloads the full model on demand."""
    try:
        with zipfile.ZipFile(mph) as z:
            return "preview" in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def build_index(root: Path) -> dict:
    apps = root / "applications"
    help_dir = root / HELP_REL
    help_by_path: dict[str, dict] = {}
    help_by_name: dict[str, list[dict]] = {}
    if help_dir.is_dir():
        for folder in help_dir.glob("com.comsol.help.models.*"):
            info = _parse_help(folder)
            name = folder.name.split(".")[-1]
            info["_name"] = name
            if info.get("library_path"):
                help_by_path[info["library_path"].lower()] = info
            help_by_name.setdefault(name.lower(), []).append(info)
    examples: list[dict] = []
    for mph in sorted(apps.rglob("*.mph")):
        rel = mph.relative_to(apps)
        parts = rel.parts
        module = parts[0]
        topic = "/".join(parts[1:-1])
        lib_path = "/".join([*parts[:-1], mph.stem])
        info = help_by_path.get(lib_path.lower())
        if info is None:
            cands = help_by_name.get(mph.stem.lower(), [])
            info = cands[0] if len(cands) == 1 else {}
        ex = Example(name=mph.stem, module=module, topic=topic, library_path=lib_path, mph=str(mph),
                     title=info.get("title", mph.stem.replace("_", " ")), intro=info.get("intro", ""),
                     java=info.get("java", ""), pdf=info.get("pdf", ""), html=info.get("html", ""),
                     physics=info.get("physics", []), multiphysics=info.get("multiphysics", []),
                     studies=info.get("studies", []), size_mb=round(mph.stat().st_size / 1e6, 1),
                     preview=_is_preview(mph))
        examples.append(asdict(ex))
    modules = []
    for mod_dir in sorted([p for p in apps.iterdir() if p.is_dir()]):
        guides = sorted(str(p) for p in (root / "doc" / "pdf" / mod_dir.name).glob("*.pdf"))
        modules.append({"module": mod_dir.name, "examples": sum(1 for e in examples if e["module"] == mod_dir.name),
                        "user_guides": guides[:6]})
    return {"version": INDEX_VERSION, "root": str(root), "modules": modules, "examples": examples}


def _signature(root: Path) -> str:
    apps = root / "applications"
    return f"{root}|{apps.stat().st_mtime if apps.exists() else 0}|{INDEX_VERSION}"


def load_index(root: Path, cache_file: Path) -> dict:
    sig = _signature(root)
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("signature") == sig:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    data = build_index(root)
    data["signature"] = sig
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


_TOKEN = re.compile(r"[a-z0-9]+")


def search(index: dict, query: str, module: str | None = None, limit: int = 15) -> list[dict]:
    words = [w for w in _TOKEN.findall(query.lower()) if len(w) > 1]
    mod = (module or "").lower().replace(" ", "_")
    rows = []
    for ex in index.get("examples", []):
        if mod and mod not in ex["module"].lower():
            continue
        title = ex["title"].lower()
        phys = " ".join(ex["physics"] + ex["multiphysics"] + ex["studies"]).lower()
        where = (ex["library_path"] + " " + ex["name"]).lower().replace("_", " ")
        intro = ex.get("intro", "").lower()
        score = 0
        for w in words:
            score += 4 * (w in title) + 2 * (w in phys) + 2 * (w in where) + 1 * (w in intro)
        if words and all(w in title for w in words):
            score += 5
        if score > 0 or not words:
            rows.append((score, ex))
    rows.sort(key=lambda r: (-r[0], r[1]["library_path"]))
    return [{"score": s, **{k: ex[k] for k in ("name", "title", "module", "topic", "library_path", "physics",
                                                "multiphysics", "studies", "size_mb")},
             "has_java": bool(ex["java"]), "preview": ex.get("preview", False)} for s, ex in rows[:limit]]


def find_example(index: dict, name: str) -> dict | None:
    key = name.strip().lower().replace("\\", "/").removesuffix(".mph")
    exact = [e for e in index.get("examples", []) if key in (e["name"].lower(), e["library_path"].lower())]
    if exact:
        return exact[0]
    by_title = [e for e in index.get("examples", []) if e["title"].lower() == key]
    return by_title[0] if by_title else None
