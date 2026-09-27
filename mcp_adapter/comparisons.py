"""Head-to-head comparison of applications for tasks that several of them can do.

Each task area ranks its candidate applications from the usual first choice downwards, sums up in one line which
application is best for which part of the job, and says for every candidate what it is strongest at, when to
choose it and what it cannot do well. The ranking is a SUGGESTION, not a
rule: when the first choice is not installed, or the user prefers another tool, any installed candidate marked as a
substitute can be used, and its ``choose_when`` / ``limits`` tell the agent what to expect and what to tell the user.
Candidates with ``substitute: False`` are listed for context only (they cover a neighbouring job, not this one).
If the task names an application itself, that choice wins whenever the application is installed.

Used by the ``compare_applications`` tool and attached to ``recommend_application`` results.
"""
from __future__ import annotations

import re
from typing import Any

SUGGESTION_NOTE = ("The ranking is a suggestion based on typical strengths, not a requirement. If the suggested "
                   "application is not installed or the user prefers another, use any installed candidate marked as a "
                   "substitute and tell the user which trade-offs (limits) apply.")


def _c(app: str, strength: str, choose_when: str, limits: str, substitute: bool = True) -> dict[str, Any]:
    return {"app": app, "strength": strength, "choose_when": choose_when, "limits": limits, "substitute": substitute}


COMPARISONS: list[dict[str, Any]] = [
    {
        "id": "antenna_design", "title": "Antenna design (single antennas and arrays)",
        "summary": "HFSS for sign-off accuracy on detailed antennas with dielectrics and feeds; Feko for wire and metallic antennas and large arrays; COMSOL when heat or deformation detune the antenna; MATLAB for early sizing, catalogue antennas and array/beam studies.",
        "keywords": {"antenna": 3, "patch antenna": 2, "dipole": 2, "monopole": 2, "horn antenna": 2, "yagi": 2,
                     "antenna array": 2, "phased array": 2, "radiation pattern": 2, "gain": 1, "s11": 2,
                     "return loss": 2, "beamforming": 2},
        "criteria": ["accuracy for the real geometry, feed and materials", "electrical size of the model",
                     "coupling with heat or mechanics", "design stage (quick sizing vs sign-off)"],
        "candidates": [
            _c("hfss", "Reference-grade FEM accuracy for antennas with detailed feeds, dielectrics and matching networks.",
               "Final design and verification, dielectric-loaded or packaged antennas, unit-cell and finite arrays.",
               "Memory and time grow quickly with electrical size; whole-platform placement is costly."),
            _c("feko", "MoM/MLFMM is very efficient for wire, metallic and planar antennas and large arrays.",
               "Wire and metallic antennas, large arrays, and when installed performance on a platform matters next.",
               "Inhomogeneous dielectric volumes need FEM regions, which reduces the speed advantage."),
            _c("comsol", "RF Module FEM with native coupling to heat transfer, structural mechanics and other physics.",
               "Thermal detuning, deformation or other multiphysics effects, or when HFSS/Feko are unavailable.",
               "Fewer antenna-specific wizards and post-processing shortcuts; slower for large arrays."),
            _c("matlab", "Antenna Toolbox (MoM) for catalogue antennas and parametric sweeps; array factors and beamforming.",
               "Early sizing, textbook antennas, array-pattern and beam-steering studies at system level.",
               "Limited geometry and material freedom; not a sign-off field solver for custom structures."),
        ],
    },
    {
        "id": "antenna_placement_rcs",
        "title": "Antenna placement, co-site coupling and radar cross section of large platforms",
        "summary": "Feko for whole-platform placement, co-site coupling and RCS of large targets; HFSS SBR+ when the antenna already lives in AEDT; MATLAB for moderate platforms and radar-level studies; COMSOL only for electrically small platforms.",
        "keywords": {"placement": 3, "installed antenna": 3, "co-site": 3, "cosite": 3, "rcs": 3, "radar cross": 3,
                     "aircraft": 2, "ship": 2, "vehicle": 2, "car": 1, "satellite": 2, "platform": 2},
        "criteria": ["electrical size of the platform", "required accuracy (full-wave vs asymptotic)", "frequency band"],
        "candidates": [
            _c("feko", "MLFMM plus PO/LE-PO/RL-GO/UTD hybrids make ship, aircraft and vehicle scale practical.",
               "Whole-platform placement, co-site interference and monostatic/bistatic RCS of large targets.",
               "Asymptotic methods need validation against full-wave results on reduced models."),
            _c("hfss", "SBR+ shooting-bouncing-ray and FEM-IE hybrids inside AEDT; reuses HFSS antenna models directly.",
               "When the antenna was designed in HFSS or the team works in Ansys Electronics Desktop.",
               "Full FEM on electrically huge platforms is impractical; relies on SBR+ approximations."),
            _c("matlab", "Antenna Toolbox platform/installedAntenna objects and rcs() (MoM/PO) on STL platforms; "
               "Radar Toolbox for system-level radar studies.",
               "Moderate-size platforms, quick placement trade-offs and radar performance from RCS data.",
               "Smaller problem sizes and fewer solver options than Feko or SBR+."),
            _c("comsol", "Full-wave FEM/BEM with multiphysics.",
               "Electrically small platforms (a few wavelengths) when the dedicated tools are unavailable.",
               "Not suited to electrically large platforms."),
        ],
    },
    {
        "id": "rf_passives_si", "title": "RF/microwave passive components and PCB/package signal integrity",
        "summary": "HFSS for 3D interconnects, connectors, packages, filters and couplers; COMSOL when heating or deformation couple in; Feko for planar metallic structures; MATLAB for cascades, matching and Touchstone post-processing; Altium only screens SI while routing.",
        "keywords": {"coupler": 3, "waveguide": 3, "connector": 2, "via": 2, "transition": 2, "microstrip": 2,
                     "stripline": 2, "signal integrity": 3, "s-parameter": 3, "s parameter": 3, "insertion loss": 3,
                     "crosstalk": 3, "pcb trace": 3, "power divider": 3, "wilkinson": 3, "combiner": 2,
                     "resonator": 2, "matching network": 3, "impedance matching": 3, "bandpass filter": 2,
                     "microwave filter": 3, "rf filter": 3, "diplexer": 3, "circulator": 3},
        "exclude": {"waveguide": ("photonic", "photonics", "optical", "silicon photonics")},
        "criteria": ["3D detail of the structure", "need for ECAD import", "network-level vs field-level answer"],
        "candidates": [
            _c("hfss", "FEM for filters, couplers, connectors and transitions; HFSS 3D Layout extracts PCBs and packages "
               "from ECAD.",
               "Sign-off S-parameters of 3D interconnects, connectors, vias and packages.",
               "Needs careful port setup; large boards call for cut-outs."),
            _c("comsol", "RF Module FEM with thermal and structural coupling.",
               "Waveguide components, filters and couplers, especially where heating or deformation changes the "
               "response.",
               "No ECAD layout workflow comparable to HFSS 3D Layout."),
            _c("feko", "MoM for planar and metallic structures.",
               "Planar metallic circuits and radiating structures.",
               "Less suited to inhomogeneous 3D interconnects and packages."),
            _c("matlab", "RF Toolbox and RF PCB Toolbox for cascaded networks, quick planar structures and S-parameter "
               "post-processing.",
               "Budget/cascade analysis, matching networks, de-embedding, eye diagrams from Touchstone data, quick "
               "planar designs.",
               "Not a 3D field solver for arbitrary geometry."),
            _c("altium", "Pre-layout and post-layout signal-integrity checks (reflection, crosstalk) with IBIS models.",
               "Screening nets during PCB design before full-wave extraction.",
               "Not a full-wave solver; approximate transmission-line models.", substitute=False),
        ],
    },
    {
        "id": "circuit_simulation", "title": "Analog and mixed-signal circuit simulation",
        "summary": "PSpice for component-level accuracy with vendor models, Monte Carlo, worst case and stress; Altium for quick checks inside an Altium design; Proteus when a microcontroller runs real firmware; Simulink for circuits inside a larger system; EAGLE's ngspice only for basic OP/DC/AC/transient checks of an EAGLE schematic.",
        "keywords": {"circuit": 3, "spice": 3, "op-amp": 2, "op amp": 2, "opamp": 2, "amplifier": 2, "transistor": 2,
                     "mosfet": 2, "bjt": 2, "bias point": 2, "monte carlo": 2, "netlist": 2, "schematic": 1},
        "criteria": ["availability of vendor device models", "advanced analyses (Monte Carlo, worst case, stress)",
                     "firmware in the loop", "system-level vs component-level question"],
        "candidates": [
            _c("orcad", "PSpice with vendor models, Monte Carlo, worst case, sensitivity, optimizer and Smoke stress "
               "analysis.",
               "Component-level design and verification with real device models and tolerances.",
               "No microcontroller firmware execution; system-level controls are clumsier than in Simulink."),
            _c("altium", "Mixed-signal SPICE simulation from the schematic via the Simulation Dashboard.",
               "Quick checks when the design already lives in Altium.",
               "Fewer advanced analyses than PSpice."),
            _c("proteus", "SPICE-based simulation together with microcontroller firmware (VSM) and virtual instruments.",
               "Circuits driven by an MCU running real firmware.",
               "Less suited to precision analog studies and statistical analyses."),
            _c("simulink", "Simscape Electrical for system-level electrical networks combined with control logic.",
               "When the circuit is part of a larger dynamic system or control design.",
               "Device models are less detailed than vendor SPICE models."),
            _c("eagle", "Built-in ngspice 26 run from the schematic: operating point, DC sweep, AC sweep and "
               "transient, digital/mixed-mode since 9.2, results annotated on the schematic and plotted.",
               "Quick checks of a circuit that already is an EAGLE schematic.",
               "Basic compared with PSpice: few vendor models mapped, no Monte Carlo, worst-case, sensitivity or "
               "stress analyses; it runs from the editor, which needs the sign-in EAGLE lost in June 2026.",
               substitute=False),
            _c("comsol", "Electrical Circuit interface (with SPICE netlist import) coupled to field models.",
               "Lumped circuits that drive or load a field model (coils, sensors, electrodes).",
               "Not a general-purpose circuit simulator; few semiconductor device models."),
        ],
    },
    {
        "id": "power_electronics", "title": "Power electronics converters and motor drives",
        "summary": "PSpice for switching waveforms, losses and stress with real devices; Simulink for control loops, drives and code generation; Proteus for MCU control code on a simple power stage; MATLAB for analytical converter models; COMSOL for the magnetics and thermal design of the parts.",
        "keywords": {"buck": 3, "boost converter": 3, "converter": 3, "inverter": 3, "rectifier": 2, "motor drive": 3,
                     "switching": 2, "pwm": 2, "power electronics": 3, "dc-dc": 3, "flyback": 3, "igbt": 2, "gan": 2,
                     "power supply": 2},
        "criteria": ["switching-level waveforms vs average/system behaviour", "controller design and code generation",
                     "magnetic and thermal detail"],
        "candidates": [
            _c("orcad", "PSpice switching waveforms with real device models, parasitics, losses and Smoke stress checks.",
               "Detailed switching behaviour, component stress and ripple with vendor models.",
               "Long transients with slow control loops are expensive; control design tools are limited."),
            _c("simulink", "Simscape Electrical with switched and average models, Motor Control Blockset and code "
               "generation.",
               "Control-loop design, drive-level and grid-level behaviour, embedded code for the controller.",
               "Device-level waveforms are less detailed than PSpice with vendor models."),
            _c("proteus", "Converter simulated with its microcontroller firmware.",
               "Validating MCU control code against a simple power stage.",
               "Simplified power-stage accuracy."),
            _c("matlab", "Averaged or switched state-space models written as ODEs; Control System Toolbox for loop "
               "shaping and stability margins.",
               "Analytical converter models and control-law design when no circuit simulator is available.",
               "No device models or parasitics; the circuit equations must be written by hand."),
            _c("comsol", "AC/DC and Heat Transfer for inductors, transformers, busbars and power-module thermal/stress.",
               "Magnetic component design, core and winding losses, thermal and thermo-mechanical reliability.",
               "Not for full converter switching simulation.", substitute=False),
        ],
    },
    {
        "id": "schematic_capture", "title": "Schematic capture, part numbers and BOM",
        "summary": "Altium for managed parts with live supply-chain data and one data model shared with the PCB; OrCAD Capture CIS for database-driven part numbers and PSpice/Allegro flows; Proteus for microcontroller projects; EAGLE was the lightweight option whose library parts carry manufacturer part numbers (retired: now for existing files and BOM extraction).",
        "keywords": {"schematic capture": 3, "draw a schematic": 3, "draw the schematic": 3, "schematic": 2,
                     "bom": 2, "bill of materials": 3, "part number": 3, "part numbers": 3, "mpn": 3,
                     "netlist": 2, "component library": 2, "symbol library": 2},
        "exclude": {"schematic": ("simulate", "simulation", "spice")},
        "criteria": ["part data management (part numbers, suppliers, lifecycle)", "link to simulation and PCB",
                     "design size and team workflow"],
        "candidates": [
            _c("altium", "Unified schematic with managed components that carry manufacturer part numbers and live "
               "supply-chain data (Manufacturer Part Search, ActiveBOM), variants and hierarchical/multi-channel design.",
               "Professional designs where schematic, BOM and PCB must stay in one managed data model.",
               "Commercial licence; heavier than needed for a quick schematic."),
            _c("orcad", "OrCAD Capture / Capture CIS: mature schematic capture with a component information system "
               "(database parts with part numbers and properties), tightly coupled to PSpice and the Cadence PCB tools.",
               "Cadence flows, database-driven part numbers and BOMs, schematics that will be simulated in PSpice.",
               "The CIS part database has to be set up and maintained."),
            _c("proteus", "Schematic capture that also runs the microcontroller firmware in simulation, with BOM reports.",
               "Microcontroller projects where the schematic will be co-simulated.",
               "Smaller managed part database than Altium or Capture CIS."),
            _c("eagle", "Lightweight, quick schematic editor whose library devices carry manufacturer and part-number "
               "attributes per package variant; XML files make BOM and netlist extraction easy (eagle_bom, eagle_netlist "
               "work without EAGLE).",
               "Existing EAGLE schematics and open-hardware designs: reading parts, BOMs with part numbers and netlists.",
               "Retired by Autodesk on 7 June 2026: the editor needs a sign-in that no longer works, so no new capture.",
               substitute=False),
            _c("drawio", "Block diagrams and wiring sketches.",
               "Only for illustrations of a circuit.",
               "No electrical meaning: no netlist, no part data.", substitute=False),
        ],
    },
    {
        "id": "pcb_layout", "title": "PCB design, layout and manufacturing outputs",
        "summary": "Altium for professional multilayer, high-speed and rigid-flex boards with the strongest interactive routing and rule system; OrCAD/Allegro for Cadence flows; Proteus for simpler microcontroller boards; EAGLE was light and quick for 2-4-layer and open-hardware boards (retired: now for existing designs and CAM output).",
        "keywords": {"pcb": 3, "layout": 2, "gerber": 3, "footprint": 2, "routing": 2, "stackup": 2, "bom": 2,
                     "odb++": 3, "printed circuit board": 3},
        "criteria": ["board complexity (simple 2-4-layer vs dense, high-speed, rigid-flex)", "routing and rule power",
                     "where the schematic lives", "team toolchain"],
        "candidates": [
            _c("altium", "Integrated schematic-to-layout flow, rules and query language, high-speed routing, ActiveBOM, "
               "Draftsman.",
               "Professional multilayer boards, high-speed design, complete manufacturing packages.",
               "Commercial licence; best when the whole design lives in Altium."),
            _c("orcad", "OrCAD/Allegro PCB Editor with Constraint Manager, tightly coupled to Capture and PSpice.",
               "Cadence-based teams and designs simulated in PSpice.",
               "Some high-end features require Allegro-level licences."),
            _c("proteus", "Schematic and PCB with MCU co-simulation in one tool.",
               "Simpler boards, education and microcontroller projects.",
               "Limited high-speed and advanced manufacturing features."),
            _c("eagle", "Lightweight, fast schematic-plus-PCB tool that was popular for 2-4-layer and open-hardware "
               "boards: libraries whose devices carry manufacturer part numbers, a follow-me and push-and-shove "
               "router, autorouter, XML files and a scripting language; the command-line CAM Processor still "
               "produces Gerber, drill and assembly data.",
               "Existing EAGLE designs (very common in open hardware): reading them, BOMs, netlists and CAM output.",
               "Retired by Autodesk on 7 June 2026: no updates or support and the editors need a sign-in that no "
               "longer works, so no interactive layout; far fewer high-speed and constraint features than Altium.",
               substitute=False),
            _c("autocad", "DWG/DXF board outlines, enclosures and mechanical drawings.",
               "Mechanical outline exchange and enclosure drawings, not routing.",
               "Not a PCB layout tool.", substitute=False),
        ],
    },
    {
        "id": "pcb_3d_mcad", "title": "3D PCB models, realistic renders and ECAD-MCAD exchange",
        "summary": "Altium for realistic 3D, STEP models on footprints, collision checks and MCAD exchange; OrCAD/Allegro for Cadence boards; AutoCAD for the enclosure around an exported board STEP; COMSOL for thermal or structural physics on the assembly.",
        "keywords": {"3d": 2, "3d pcb": 3, "3d render": 3, "3d view": 2, "3d body": 3, "3d bodies": 3,
                     "step model": 3, "step file": 3, "mcad": 3, "enclosure": 2, "collision": 2, "realistic": 1},
        "criteria": ["accuracy of component 3D models (vendor STEP vs generated bodies)",
                     "collision and enclosure checks", "exchange format expected by the mechanical team"],
        "candidates": [
            _c("altium", "Native 3D PCB editor: realistic rendering, STEP/Parasolid models on footprints, generated "
               "3D bodies, 3D clearance and collision checks, rigid-flex folding, STEP export and MCAD CoDesigner.",
               "Realistic 3D views, enclosure fit and ECAD-MCAD exchange of a board.",
               "Realism depends on every footprint having an accurate 3D body or vendor STEP model."),
            _c("orcad", "OrCAD X / Allegro 3D canvas with STEP models mapped to footprints and STEP export.",
               "Boards designed in the Cadence PCB tools.",
               "This adapter automates PSpice and Capture, not the Cadence PCB editor."),
            _c("autocad", "3D solids for enclosures and mechanical parts around an imported board STEP.",
               "Designing or checking the enclosure, not the board itself.",
               "Not PCB-aware: no footprints, parts or copper.", substitute=False),
            _c("eagle", "3D packages from managed libraries, shown and exchanged through the Autodesk Fusion link.",
               "Existing EAGLE designs that already carry 3D packages.",
               "Needs Autodesk Fusion and online services that EAGLE lost with its retirement in June 2026.",
               substitute=False),
            _c("comsol", "Thermal and structural simulation of an imported board assembly (component temperatures, warpage).",
               "Physics on the 3D assembly.",
               "Not a rendering or ECAD tool.", substitute=False),
        ],
    },
    {
        "id": "embedded_firmware", "title": "Microcontroller firmware development and testing",
        "summary": "Proteus to run and debug real firmware against the simulated circuit; Simulink with Embedded Coder for model-based production code; MATLAB Coder for C code from algorithms; Vivado only for processors inside AMD FPGAs and SoCs.",
        "keywords": {"firmware": 3, "microcontroller": 3, "arduino": 3, "mcu": 3, "embedded": 2, "stm32": 2,
                     "pic": 2, "avr": 2, "embedded code": 3},
        "criteria": ["hand-written code vs model-based design", "need to test with the real circuit", "target hardware"],
        "candidates": [
            _c("proteus", "Runs the real firmware (HEX/ELF) on simulated MCUs together with the circuit and virtual "
               "instruments.",
               "Testing and debugging hand-written firmware against the board before hardware exists.",
               "Only supported MCU families; no automatic code generation."),
            _c("simulink", "Model-based design with Embedded Coder, hardware support packages, SIL/PIL testing.",
               "Control and signal algorithms that should be generated as production C code.",
               "Cannot execute hand-written firmware against a simulated circuit."),
            _c("matlab", "MATLAB Coder for algorithm code generation and fixed-point design.",
               "Generating C for numerical algorithms written in MATLAB.",
               "No MCU peripheral or circuit simulation."),
            _c("vivado", "MicroBlaze/MicroBlaze V soft processors and Zynq processors with Vitis embedded tools.",
               "Firmware for processors inside AMD FPGAs and SoCs.",
               "Only for AMD FPGA/SoC targets.", substitute=False),
        ],
    },
    {
        "id": "fpga_hardware", "title": "FPGA and digital hardware design",
        "summary": "Vivado is required to implement and program AMD FPGAs; Simulink or MATLAB HDL Coder generate the HDL from models or algorithms first.",
        "keywords": {"fpga": 3, "verilog": 3, "vhdl": 3, "hdl": 3, "rtl": 2, "bitstream": 3, "hls": 2,
                     "systemverilog": 3, "timing closure": 3},
        "criteria": ["hand-written RTL vs model-based generation", "target device and implementation needs"],
        "candidates": [
            _c("vivado", "Synthesis, implementation, timing closure, IP integration and bitstreams for AMD devices; "
               "Vitis HLS.",
               "Always required to implement and program AMD FPGAs; RTL and HLS design.",
               "AMD devices only."),
            _c("simulink", "HDL Coder generates VHDL/Verilog from Simulink models, with fixed-point and verification "
               "support.",
               "DSP and control algorithms developed as models, then implemented with Vivado.",
               "Still needs Vivado (or another vendor tool) for implementation and timing closure."),
            _c("matlab", "HDL Coder from MATLAB functions and Fixed-Point Designer.",
               "Algorithm-level fixed-point exploration and HDL generation from MATLAB code.",
               "Still needs Vivado (or another vendor tool) for implementation."),
        ],
    },
    {
        "id": "control_dynamic_systems", "title": "Control design and dynamic system simulation",
        "summary": "Simulink for nonlinear closed-loop simulation, logic and deployment; MATLAB for linear analysis and controller synthesis; Mathematica for closed-form, parameter-dependent stability results.",
        "keywords": {"control": 2, "controller": 2, "pid": 3, "closed loop": 3, "closed-loop": 3, "plant": 2,
                     "bode": 3, "nyquist": 3, "root locus": 3, "stability": 2, "state space": 2, "state-space": 2,
                     "transfer function": 2, "dynamic system": 3, "feedback": 2, "lqr": 3, "kalman": 2},
        "criteria": ["linear analysis vs nonlinear time simulation", "logic and mode switching", "symbolic insight"],
        "candidates": [
            _c("simulink", "Nonlinear block-diagram simulation, Stateflow logic, physical models (Simscape), code "
               "generation.",
               "Closed-loop simulation of realistic plants, supervisory logic and deployment.",
               "Pure linear analysis is quicker in MATLAB scripts."),
            _c("matlab", "Control System Toolbox: LTI models, pidtune, lqr, Bode/Nyquist/root locus, robust control.",
               "Linear analysis and controller synthesis from models or measured data.",
               "Nonlinear, event-driven systems are easier to build in Simulink."),
            _c("mathematica", "Symbolic transfer functions, closed-form stability conditions and exact analysis.",
               "Deriving analytical expressions, parameter-dependent stability regions.",
               "Fewer ready-made control design apps than MATLAB."),
            _c("comsol", "Spatially distributed plants (thermal, structural, fluid) and reduced-order models.",
               "Plants whose dynamics come from a field simulation.",
               "Controller design itself is better done in MATLAB/Simulink.", substitute=False),
            _c("orcad", "Analog control loops simulated at circuit level.",
               "Analog compensators built from op-amps and discrete parts.",
               "Not a control design environment.", substitute=False),
        ],
    },
    {
        "id": "differential_equations", "title": "Differential equations and general numerical computing",
        "summary": "MATLAB for numerical ODE/DAE work, optimisation and data; Mathematica for exact or high-precision solutions; COMSOL for PDEs on real geometry; Simulink for ODE systems built as block diagrams.",
        "keywords": {"ode": 2, "differential equation": 3, "numerical": 2, "numerically": 2, "pde": 2,
                     "linear algebra": 2, "optimization": 1, "optimisation": 1, "matrix": 1, "eigenvalue": 1},
        "criteria": ["exact vs numerical answer", "geometry-based PDE vs abstract equations", "data volume"],
        "candidates": [
            _c("matlab", "Robust numerical ODE/DAE solvers, linear algebra, optimization and large data handling.",
               "Numerical solutions, parameter studies and data-driven engineering computation.",
               "Exact symbolic solutions are limited."),
            _c("mathematica", "DSolve for exact solutions, NDSolve with high precision and event handling.",
               "When a closed form, arbitrary precision or symbolic parameter dependence is wanted.",
               "Large engineering data workflows are less convenient."),
            _c("comsol", "FEM for PDEs on real geometry, including custom PDEs and weak form.",
               "Spatial PDEs on CAD geometry and coupled fields.",
               "Overkill for pure ODE systems."),
            _c("simulink", "ODE systems assembled graphically with many solvers.",
               "Equations that map naturally to block diagrams or physical components.",
               "Less convenient for purely mathematical studies."),
        ],
    },
    {
        "id": "symbolic_math", "title": "Symbolic and exact mathematics",
        "summary": "Mathematica for any serious symbolic or exact work; MATLAB's Symbolic Math Toolbox for moderate steps inside MATLAB code.",
        "keywords": {"symbolic": 3, "symbolically": 3, "closed form": 3, "closed-form": 3, "exact solution": 3,
                     "analytically": 3, "analytical solution": 3, "simplify": 2, "series expansion": 2, "integral": 2,
                     "derivation": 2, "derive": 2, "proof": 2},
        "criteria": ["size and difficulty of the symbolic problem", "integration with numeric MATLAB code"],
        "candidates": [
            _c("mathematica", "The strongest symbolic engine: integrals, sums, special functions, simplification, proofs.",
               "Any serious symbolic or exact work.",
               "Numeric toolboxes for engineering domains are fewer than MATLAB's."),
            _c("matlab", "Symbolic Math Toolbox for moderate problems inside MATLAB workflows (matlabFunction to numeric "
               "code).",
               "Symbolic steps embedded in a MATLAB numerical workflow.",
               "Weaker on hard integrals, special functions and simplification."),
        ],
    },
    {
        "id": "data_analysis_ml", "title": "Data analysis, statistics and machine learning",
        "summary": "MATLAB for engineering data, domain toolboxes and deployment; Mathematica for exploratory and symbolic statistics.",
        "keywords": {"data analysis": 3, "statistics": 2, "regression": 2, "machine learning": 3, "deep learning": 3,
                     "classification": 2, "neural network": 2, "dataset": 1, "csv": 1, "curve fit": 3,
                     "curve fitting": 3, "fit": 1, "measured data": 2},
        "criteria": ["engineering toolboxes needed", "symbolic or knowledge-based features", "deployment"],
        "candidates": [
            _c("matlab", "Statistics and Machine Learning, Deep Learning and domain toolboxes with apps and code "
               "generation.",
               "Engineering datasets, sensor data, model training with deployment to embedded targets.",
               "Licensed toolboxes are needed for many features."),
            _c("mathematica", "Classify/Predict, neural networks, probability distributions and curated knowledge.",
               "Exploratory analysis with symbolic statistics or built-in curated data.",
               "Fewer domain-specific engineering toolboxes."),
        ],
    },
    {
        "id": "signal_image_processing", "title": "Signal and scientific image processing",
        "summary": "MATLAB for quantitative, reproducible signal and image processing; Mathematica for exploratory or symbolic analysis; Photoshop only when visual appearance is the goal.",
        "keywords": {"signal processing": 3, "fft": 2, "spectrum": 2, "filter design": 3, "fir": 3, "iir": 3,
                     "digital filter": 3, "image processing": 3, "segmentation": 2, "denoise": 2, "audio": 2,
                     "spectrogram": 2, "microscopy": 2},
        "criteria": ["quantitative, reproducible processing vs visual editing"],
        "candidates": [
            _c("matlab", "Signal, DSP, Image Processing, Computer Vision and Audio toolboxes with reproducible scripts.",
               "Quantitative analysis, filter design, measurements and batch processing of data or images.",
               "Not an interactive photo editor."),
            _c("mathematica", "Built-in signal, audio and image functions with symbolic filter analysis.",
               "Exploratory or symbolic signal analysis.",
               "Fewer specialised engineering toolboxes."),
            _c("photoshop", "Interactive retouching and visual image editing.",
               "Visual appearance matters, not measured values.",
               "Not quantitative; edits are not scientific measurements.", substitute=False),
        ],
    },
    {
        "id": "photo_editing", "title": "Photo editing and graphic assets",
        "summary": "Photoshop for anything visual; MATLAB or Mathematica only for algorithmic batch operations.",
        "keywords": {"photo": 3, "photograph": 3, "retouch": 3, "retouching": 3, "image editing": 3,
                     "remove background": 3, "background removal": 3, "mockup": 2, "banner": 2, "resize images": 2,
                     "watermark": 2, "psd": 3, "poster": 2},
        "criteria": ["visual quality and creative control", "batch automation"],
        "candidates": [
            _c("photoshop", "Professional retouching, compositing, Camera Raw, generative fill and batch actions.",
               "Anything visual: photos, graphics, mockups, marketing assets.",
               "Scripting is more involved than numeric tools for bulk algorithmic edits."),
            _c("matlab", "Algorithmic, reproducible batch image operations.",
               "Numeric transformations applied identically to many images.",
               "No creative editing tools."),
            _c("mathematica", "Programmatic image operations (RemoveBackground, ImageRestyle) and generative functions.",
               "Programmatic pipelines in a Wolfram workflow.",
               "No professional retouching workflow."),
        ],
    },
    {
        "id": "structural_fem", "title": "Structural analysis (stress, vibration, buckling, fatigue)",
        "summary": "COMSOL for structural FEM with nonlinear materials, contact and fatigue; MATLAB's PDE Toolbox for simple linear problems; Simulink Multibody for mechanism motion, not stress.",
        "keywords": {"stress": 3, "strain": 2, "deformation": 2, "vibration": 2, "modal analysis": 2,
                     "eigenfrequency": 2, "buckling": 3, "fatigue": 3, "contact": 1, "structural": 3,
                     "von mises": 3, "bracket": 1, "beam": 1},
        "criteria": ["geometry and material complexity", "rigid-body mechanism vs deformable body"],
        "candidates": [
            _c("comsol", "Full structural FEM: nonlinear materials, contact, composites, fatigue, rotordynamics, "
               "multibody.",
               "Stress, vibration, buckling and fatigue on real geometry, including coupled physics.",
               "Setting up very large assemblies takes effort."),
            _c("matlab", "PDE Toolbox for linear elasticity and modal analysis on simple geometry.",
               "Simple parts and teaching-level problems scripted in MATLAB.",
               "No nonlinear materials, contact or fatigue workflows."),
            _c("simulink", "Simscape Multibody for rigid-body mechanism dynamics.",
               "Mechanism motion, loads on joints and control of mechanisms.",
               "Does not compute stress in deformable parts.", substitute=False),
        ],
    },
    {
        "id": "thermal", "title": "Thermal analysis and electronics cooling",
        "summary": "COMSOL for temperature fields on geometry and electronics cooling; Simulink for lumped, system-level thermal behaviour; MATLAB for simple conduction problems and fits.",
        "keywords": {"thermal": 3, "temperature": 2, "temperature rise": 3, "heat": 2, "heating": 2, "cooling": 3,
                     "heat sink": 3, "heatsink": 3, "conduction": 2, "convection": 2, "radiative": 1},
        "criteria": ["spatial detail vs lumped network", "coupling with flow or electrical losses"],
        "candidates": [
            _c("comsol", "Conduction, convection, radiation and conjugate heat transfer with flow and electrical coupling.",
               "Temperature fields on real geometry, electronics cooling, Joule and induction heating.",
               "Large turbulent CFD models need significant compute."),
            _c("simulink", "Simscape Thermal lumped networks in system simulation.",
               "Transient system-level thermal behaviour with controls (e.g. battery or motor temperature).",
               "No spatial temperature field."),
            _c("matlab", "PDE Toolbox thermal and lumped models in scripts.",
               "Simple conduction problems and parameter fits.",
               "No conjugate heat transfer or radiation between surfaces."),
        ],
    },
    {
        "id": "cfd", "title": "Fluid flow and CFD",
        "summary": "COMSOL for 2D/3D flow fields and flow coupled to heat or structures; Simulink (Simscape Fluids) for system-level hydraulics.",
        "keywords": {"fluid": 2, "flow": 2, "cfd": 3, "turbulent": 3, "turbulence": 3, "laminar": 3,
                     "pressure drop": 3, "pipe": 1, "hydraulic": 2, "microfluidic": 3, "aerodynamic": 3, "drag": 2},
        "criteria": ["3D flow field vs system-level hydraulics", "coupling with heat, structure or species"],
        "candidates": [
            _c("comsol", "Laminar, turbulent, multiphase, porous, pipe and microfluidic flow with multiphysics coupling.",
               "3D/2D flow fields, pressure drop, mixing and coupled thermal or structural effects.",
               "Very large high-Reynolds industrial CFD is slower than dedicated CFD codes."),
            _c("simulink", "Simscape Fluids lumped hydraulic, thermal-liquid and gas networks.",
               "System-level hydraulics, pumps, valves and actuators with controls.",
               "No spatial flow field."),
            _c("matlab", "Scripts for simple flow models and post-processing of measured or exported data.",
               "Analysing flow data, simple analytical models.",
               "Not a CFD solver.", substitute=False),
        ],
    },
    {
        "id": "low_frequency_em",
        "title": "Low-frequency electromagnetics: motors, transformers, inductors, electrostatics",
        "summary": "COMSOL for motors, transformers, inductors, busbars and electrostatics from their geometry; Simulink for drives with known machine parameters; MATLAB for simple 2D fields and parameter fits.",
        "keywords": {"motor": 2, "transformer": 3, "inductor": 3, "inductance": 3, "coil": 2, "eddy current": 3,
                     "magnetostatic": 3, "electrostatic": 3, "capacitance": 3, "busbar": 3, "induction heating": 3,
                     "permanent magnet": 2, "magnetic field": 2, "electric field": 2, "torque": 1},
        "criteria": ["field detail vs lumped machine model", "losses and thermal coupling"],
        "candidates": [
            _c("comsol", "AC/DC Module: magnetic and electric fields, coils, rotating machinery, losses, coupled heat and "
               "stress.",
               "Designing motors, transformers, inductors, sensors and busbars from their geometry.",
               "Requires the AC/DC Module licence; 3D machines take compute."),
            _c("simulink", "Simscape Electrical and Motor Control Blockset lumped machine models.",
               "Drive and system simulation with already-known machine parameters.",
               "No field computation of the machine itself."),
            _c("matlab", "PDE Toolbox electrostatics/magnetostatics for simple 2D problems; parameter estimation.",
               "Simple field problems and fitting machine parameters from data.",
               "No rotating machinery or loss models."),
            _c("orcad", "Lumped inductors and transformers (Magnetic Parts Editor) at circuit level.",
               "Circuit behaviour with a given magnetic component.",
               "No field simulation.", substitute=False),
        ],
    },
    {
        "id": "acoustics", "title": "Acoustics and vibro-acoustics",
        "summary": "COMSOL for sound fields, transducers and vibro-acoustics on geometry; MATLAB for analytical estimates and audio signal analysis; Simulink for lumped transducer models.",
        "keywords": {"acoustic": 3, "sound": 3, "noise": 1, "loudspeaker": 3, "muffler": 3, "ultrasound": 3,
                     "ultrasonic": 3, "transmission loss": 3, "room acoustics": 3, "sound pressure": 3},
        "criteria": ["sound field on geometry vs signal-level audio"],
        "candidates": [
            _c("comsol", "Acoustics Module: pressure/thermoviscous/aeroacoustics, ray acoustics, acoustic-structure "
               "coupling.",
               "Sound fields, transducers, mufflers, room acoustics and vibro-acoustics on real geometry.",
               "Very high frequencies in large rooms need ray acoustics or asymptotic methods."),
            _c("matlab", "Analytical models (transfer matrix, image source) plus Audio Toolbox signal analysis.",
               "Analytical estimates and signal-level audio analysis, filters, loudness, impulse responses.",
               "No acoustic field simulation on real geometry."),
            _c("simulink", "Lumped acoustic or electro-mechanical transducer models in system simulation.",
               "System-level transducer and control studies.",
               "No spatial sound field."),
        ],
    },
    {
        "id": "coupled_multiphysics",
        "title": "Coupled multiphysics (Joule heating, thermal stress, FSI, piezoelectric, electrochemical-thermal)",
        "summary": "COMSOL for strongly coupled physics in one model; MATLAB to orchestrate weak or sequential couplings; HFSS only for RF losses handed to Ansys thermal tools.",
        "keywords": {"multiphysics": 3, "coupled": 2, "joule": 3, "thermal stress": 3, "fluid-structure": 3, "fsi": 3,
                     "piezoelectric": 3, "piezo": 3, "thermoelectric": 3, "electro-thermal": 3, "electrothermal": 3,
                     "mems": 2},
        "criteria": ["strength of two-way coupling", "number of physics involved"],
        "candidates": [
            _c("comsol", "Native, fully coupled multiphysics across almost all physics in one model.",
               "Any problem where two or more physics interact strongly.",
               "Strong nonlinear couplings need solver tuning."),
            _c("matlab", "Orchestration of separate tools (e.g. LiveLink for MATLAB) and custom coupling scripts.",
               "Weak, sequential couplings or parameter sweeps driven from MATLAB.",
               "Manual coupling is slower and less robust than native coupling."),
            _c("hfss", "Electromagnetic losses exported to thermal/structural solvers in the Ansys ecosystem.",
               "RF heating when the EM model already lives in AEDT.",
               "The thermal and mechanical parts need other Ansys tools outside this adapter."),
        ],
    },
    {
        "id": "chemistry_batteries", "title": "Chemical reactors, electrochemistry and batteries",
        "summary": "COMSOL for cell, corrosion, fuel-cell and reactor physics; Simulink for pack-level and BMS simulation; MATLAB for fitting models to test data; Mathematica for analytical kinetics.",
        "keywords": {"battery": 3, "batteries": 3, "lithium": 3, "li-ion": 3, "electrochemical": 3, "reactor": 3,
                     "reaction kinetics": 2, "corrosion": 3, "fuel cell": 3, "electrolyzer": 3, "electrolysis": 3,
                     "state of charge": 2, "electroplating": 3},
        "criteria": ["cell/reactor internal physics vs system behaviour", "BMS and control design"],
        "candidates": [
            _c("comsol", "Battery Design, Electrochemistry, Corrosion, Fuel Cell and Chemical Reaction Engineering "
               "modules.",
               "Cell design, degradation, thermal runaway, corrosion and reactor design from physics.",
               "Pack-level control and BMS algorithms are easier in Simulink."),
            _c("simulink", "Simscape Battery and equivalent-circuit models for packs and BMS.",
               "Pack-level system simulation, BMS algorithm development and code generation.",
               "No electrochemical field detail inside cells."),
            _c("matlab", "Parameter estimation of equivalent circuits and analysis of test data.",
               "Fitting models to measured cycling data.",
               "No physics-based cell simulation."),
            _c("mathematica", "Symbolic and numeric reaction kinetics and equilibria.",
               "Analytical kinetics and equilibrium calculations.",
               "No spatial transport or cell geometry."),
        ],
    },
    {
        "id": "optics", "title": "Optics and photonics",
        "summary": "COMSOL for wave and ray optics and thermal lensing; HFSS for small metasurfaces and nanostructures inside AEDT; MATLAB for simple propagation models.",
        "keywords": {"optical": 3, "optics": 3, "lens": 3, "photonic": 3, "photonics": 3, "laser": 2,
                     "ray tracing": 2, "grating": 3, "metasurface": 2, "optical fiber": 3, "optical fibre": 3},
        "criteria": ["wave optics vs ray optics", "structure size relative to wavelength"],
        "candidates": [
            _c("comsol", "Wave Optics (FEM, BEM, beam envelopes) and Ray Optics (lenses, stray light) with thermal "
               "coupling.",
               "Photonic devices, waveguides, gratings, lens systems and thermal lensing.",
               "Very large optical systems use ray optics; full-wave is limited to small structures."),
            _c("hfss", "Full-wave FEM also works at optical wavelengths for small periodic or nanostructures.",
               "Metasurfaces and nanoantennas when the team works in AEDT.",
               "No ray-optics or lens-design workflow."),
            _c("matlab", "Scripts for Fourier optics and data analysis of optical measurements.",
               "Simple propagation models and measurement analysis.",
               "No optical design solver."),
        ],
    },
    {
        "id": "emc_emi", "title": "EMC/EMI analysis",
        "summary": "Feko for vehicle and aircraft EMC and cable harnesses; HFSS for board, package and enclosure EMI; COMSOL for component shielding; PSpice for conducted emissions of power supplies.",
        "keywords": {"emc": 3, "emi": 3, "emission": 2, "immunity": 3, "shielding": 3, "cable harness": 3,
                     "conducted emission": 3, "radiated emission": 3, "lightning": 2, "hirf": 3},
        "criteria": ["system/platform level vs board level", "conducted vs radiated"],
        "candidates": [
            _c("feko", "Cable-harness coupling (MoM/MTL), shielding and radiated emissions/immunity on platforms.",
               "Vehicle and aircraft EMC, cable coupling, HIRF and lightning studies.",
               "Board-level detail is better handled in HFSS/Altium."),
            _c("hfss", "Radiated emissions and coupling of PCBs, packages and enclosures (HFSS and 3D Layout).",
               "Board and enclosure level EMI with ECAD-based models.",
               "Whole-platform cable harness studies are more efficient in Feko."),
            _c("comsol", "Shielding effectiveness and coupled EM-thermal studies.",
               "Component-level shielding and multiphysics effects.",
               "Fewer EMC-specific workflows."),
            _c("orcad", "Conducted emissions with LISN models in PSpice.",
               "Conducted EMI of power supplies at circuit level.",
               "No radiated field simulation."),
            _c("altium", "Signal-integrity rule checks during layout.",
               "Preventive checks while routing.",
               "Not an EMC solver.", substitute=False),
        ],
    },
    {
        "id": "technical_drawings", "title": "Technical drawings and 2D/3D CAD geometry",
        "summary": "AutoCAD for scaled, dimensioned drawings and DWG/DXF exchange; draw.io for quick plans and sketches.",
        "keywords": {"drawing": 3, "cad": 3, "dwg": 3, "dxf": 3, "floor plan": 3, "floorplan": 3, "dimension": 2,
                     "mechanical drawing": 3, "3d model": 2, "drafting": 3},
        "exclude": {"drawing": ("circuit", "schematic", "flowchart", "diagram")},
        "criteria": ["scaled, dimensioned drawing vs quick sketch vs simulation geometry"],
        "candidates": [
            _c("autocad", "Precise DWG/DXF drafting, dimensions, layouts, blocks and plotting; 3D solids.",
               "Scaled, dimensioned technical drawings and CAD exchange.",
               "Not for simulation."),
            _c("drawio", "Floor plans, layouts and sketches with its floorplan and engineering shape libraries.",
               "Readable plans and sketches where approximate scale is acceptable.",
               "No true CAD precision, dimension styles or DWG output."),
            _c("comsol", "Geometry for simulation (Design Module, CAD import and defeaturing).",
               "Preparing geometry that will be simulated.",
               "Not a drafting tool.", substitute=False),
            _c("altium", "Draftsman board fabrication and assembly drawings.",
               "Drawings of PCBs for manufacturing.",
               "Board drawings only.", substitute=False),
        ],
    },
    {
        "id": "diagrams", "title": "Diagrams for documentation (flowcharts, architecture, block diagrams)",
        "summary": "draw.io for documentation diagrams; Simulink when the diagram must simulate; Mathematica for graphs computed from data; AutoCAD when exact geometry matters.",
        "keywords": {"diagram": 3, "flowchart": 3, "flow chart": 3, "architecture": 2, "block diagram": 2, "uml": 3,
                     "org chart": 3, "sequence diagram": 3, "network diagram": 3, "mind map": 3},
        "exclude": {"diagram": ("bode", "nyquist", "eye", "phase", "smith", "polar", "free body", "free-body"),
                    "block diagram": ("simulate", "simulation", "simulink")},
        "criteria": ["documentation vs executable model", "data-driven generation"],
        "candidates": [
            _c("drawio", "Flowcharts, UML, architecture and network diagrams with CSV/Mermaid generation and exports.",
               "Documentation diagrams of any kind.",
               "Diagrams do not simulate."),
            _c("simulink", "Executable block diagrams.",
               "When the block diagram must actually run as a simulation.",
               "Not intended for general documentation graphics."),
            _c("mathematica", "Graphs and networks computed from data (Graph, TreePlot).",
               "Diagrams generated from computed structures.",
               "Less convenient for free-form documentation."),
            _c("autocad", "Precise, scaled drawings.",
               "When exact geometry and dimensions matter.",
               "Heavy for simple documentation diagrams."),
        ],
    },
]

# Names a user may write when asking for a specific application.
APP_NAMES: dict[str, str] = {
    "matlab": "matlab", "simulink": "simulink", "simscape": "simulink", "mathematica": "mathematica",
    "wolfram": "mathematica", "comsol": "comsol", "photoshop": "photoshop", "orcad": "orcad", "pspice": "orcad",
    "altium": "altium", "proteus": "proteus", "vivado": "vivado", "vitis": "vivado", "autocad": "autocad",
    "hfss": "hfss", "draw.io": "drawio", "drawio": "drawio", "feko": "feko", "eagle": "eagle",
}

_WORD = re.compile(r"[a-z0-9.+#/-]+")


def _text(task: str) -> str:
    return " " + " ".join(w.strip(".") for w in _WORD.findall(task.lower())) + " "


def _hit(kw: str, text: str) -> bool:
    """Whole-word (or whole-phrase) match, allowing a plural ending."""
    return any(f" {kw}{suffix} " in text for suffix in ("", "s", "es"))


def named_apps(task: str) -> list[str]:
    """Applications the task names explicitly (e.g. "in MATLAB", "with PSpice"), in order of appearance."""
    text = _text(task)
    found = sorted((text.find(f" {name} "), app) for name, app in APP_NAMES.items() if f" {name} " in text)
    out: list[str] = []
    for _, app in found:
        if app not in out:
            out.append(app)
    return out


def _score(area: dict, text: str) -> tuple[int, list[str]]:
    score, hits = 0, []
    excluded = area.get("exclude", {})
    for kw, w in area["keywords"].items():
        if _hit(kw, text) and not any(_hit(x, text) for x in excluded.get(kw, ())):
            score += w
            hits.append(kw)
    return score, hits


def compare(task: str, installed: set[str] | None = None, limit: int = 2) -> list[dict[str, Any]]:
    """Task areas that match *task*, best first, each with ranked candidates annotated with installation status and
    a suggested pick: an installed application the task names, else the first installed candidate that can
    substitute, else nothing (the advice then says which application would be the first choice)."""
    text = _text(task)
    requested = named_apps(task)
    scored = []
    for order, area in enumerate(COMPARISONS):
        score, hits = _score(area, text)
        if score > 0:
            scored.append((-score, order, hits, area))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [annotate(area, installed, requested, score=-neg, matched=hits) for neg, _, hits, area in scored[:limit]]


def annotate(area: dict, installed: set[str] | None = None, requested: list[str] | tuple = (),
             **extra: Any) -> dict[str, Any]:
    """One area with its ranked candidates, the suggested pick and advice that states any trade-off."""
    cands = []
    for rank, c in enumerate(area["candidates"], 1):
        row = {"rank": rank, **c}
        if installed is not None:
            row["installed"] = c["app"] in installed
        cands.append(row)
    first = cands[0]

    def usable(c: dict) -> bool:
        return installed is None or bool(c.get("installed"))

    notes = []
    for app in requested:
        cand = next((c for c in cands if c["app"] == app), None)
        if installed is not None and app not in installed:
            notes.append(f"The task names {app}, which is not installed here.")
        elif cand is None:
            notes.append(f"The task names {app}, which is not ranked for this area; follow the user's choice if it "
                         "can do the job and say what it cannot.")
    req = next((c for c in cands if c["app"] in requested and usable(c)), None)
    pick = req or next((c for c in cands if usable(c) and (c is first or c["substitute"])), None)

    out = {"area": area["id"], "title": area["title"], "summary": area["summary"], "criteria": area["criteria"],
           "candidates": cands, **extra}
    if requested:
        out["requested"] = list(requested)
    if pick is None:
        out["suggested"] = None
        out["advice"] = (f"First choice would be {first['app']}, but no suitable candidate is installed here; tell the "
                         "user and describe what a suitable tool would provide.")
    elif pick is req:
        out["suggested"] = pick["app"]
        out["advice"] = (f"The task asks for {pick['app']} and it can do this (rank {pick['rank']} here): use it."
                         + ("" if pick is first else
                            f" Keep in mind: {pick['limits']} The usual first choice is {first['app']}."))
    elif pick is first:
        out["suggested"] = pick["app"]
        out["advice"] = f"Suggested: {pick['app']}. {pick['choose_when']}"
    else:
        out["suggested"] = pick["app"]
        out["advice"] = (f"First choice would be {first['app']} (not installed). Suggested installed alternative: "
                         f"{pick['app']}. {pick['choose_when']} Tell the user its limits: {pick['limits']}")
    if notes:
        out["advice"] = " ".join(notes) + " " + out["advice"]
    out["note"] = SUGGESTION_NOTE
    return out


def find_area(area_id: str) -> dict | None:
    key = area_id.strip().lower()
    return next((a for a in COMPARISONS if a["id"] == key), None)


def area_list() -> list[dict[str, Any]]:
    return [{"area": a["id"], "title": a["title"], "summary": a["summary"],
             "candidates": [c["app"] for c in a["candidates"]]} for a in COMPARISONS]
