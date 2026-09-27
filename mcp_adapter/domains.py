"""What each application is best at, and a task -> application recommender.

This is the "which tool for which job" knowledge of the MCP layer. It feeds
* the tag prepended to every automation tool description (so a model sees the domain at a glance),
* the routing guide in the server instructions and the agent prompt,
* the ``recommend_application`` tool and the ``best_for`` / ``capability_areas`` fields of ``list_software`` and
  ``software_overview`` (capability areas are the category titles of the application's catalog, so they grow
  automatically when the catalog grows).
"""
from __future__ import annotations

import re
from functools import cache
from typing import Any

# One profile per application id. Keyword weights: 3 = decisive term, 2 = strong, 1 = supporting.
APP_PROFILES: dict[str, dict[str, Any]] = {
    "hfss": {
        "tag": "Ansys HFSS: 3D electromagnetic field simulation (antennas, RF/microwave, signal integrity)",
        "best_for": [
            "antenna design and radiation patterns (gain, directivity, realised gain, S11, impedance bandwidth, axial ratio)",
            "RF/microwave passive components: filters, couplers, resonators, waveguides, transitions (FEM, eigenmode)",
            "S-parameters of connectors, cables, packages, PCB traces and vias; HFSS 3D Layout with EDB/ECAD import",
            "unit cells and infinite arrays (Floquet ports, lattice pairs), finite arrays, SBR+ installed antennas and RCS",
            "transient/TDR, EMI/EMC, SAR; full automation through PyAEDT/PyEDB and the AEDT scripting object model",
        ],
        "typical_tasks": ["patch antenna at 2.4 GHz with S11 and gain", "microstrip filter S21 sweep",
                          "waveguide mode or cavity eigenmode analysis", "PCB via transition insertion loss from an ODB++ board",
                          "Floquet unit cell of a reflectarray", "RCS of a target with SBR+"],
        "not_ideal_for": ["lumped circuit simulation (use PSpice)", "general math or scripting (use MATLAB)"],
        "prefer_over": {"matlab": "any full-wave field problem: MATLAB can only post-process, HFSS solves Maxwell's equations",
                        "comsol": "RF/antenna work with ports, S-parameters and far fields; COMSOL when EM must couple to heat or mechanics",
                        "feko": "FEM detail of components, packages, connectors and PCB signal integrity; Feko for electrically large platforms, RCS, cables"},
        "keywords": {"antenna": 3, "patch antenna": 3, "dipole": 2, "radiation pattern": 3, "s11": 3, "s21": 2,
                     "s-parameter": 3, "s parameter": 3, "sparameter": 3, "return loss": 3, "vswr": 3, "gain": 1,
                     "directivity": 3, "far field": 3, "far-field": 3, "waveguide": 3, "microstrip": 2, "coaxial": 2,
                     "resonator": 2, "cavity": 2, "rf": 2, "microwave": 3, "ghz": 2, "electromagnetic": 2,
                     "em simulation": 3, "full-wave": 3, "full wave": 3, "hfss": 3, "eigenmode": 2, "phased array": 3,
                     "radar cross": 3, "rcs": 2, "signal integrity": 3, "impedance matching": 2, "coupler": 2,
                     "circulator": 2, "horn": 2, "reflector": 1, "emi": 2, "emc": 2, "wireless power": 2,
                     "monopole": 3, "yagi": 3, "slot antenna": 3, "beamforming": 3, "array": 1, "wifi": 2,
                     "bluetooth": 2, "mmwave": 3, "millimeter wave": 3, "radome": 3, "transmission line": 2,
                     "stripline": 3, "cpw": 3, "balun": 3, "power divider": 3, "wilkinson": 3, "sar": 2,
                     "specific absorption": 3, "crosstalk": 3, "insertion loss": 3, "eye diagram": 2, "e-field": 3,
                     "h-field": 3, "maxwell": 2, "resonant frequency": 2, "bandwidth": 1, "connector": 1, "patch": 1,
                     "mhz": 1, "frequency sweep": 2, "port": 1, "pyaedt": 3, "aedt": 3, "floquet": 3, "unit cell": 2,
                     "reflectarray": 3, "sbr": 3, "3d layout": 3, "pyedb": 3, "via transition": 3, "package model": 2,
                     "axial ratio": 3, "realized gain": 3, "realised gain": 3, "touchstone": 2, "tdr": 2,
                     "dielectric resonator": 3, "filter s21": 3, "cavity filter": 3},
    },
    "feko": {
        "tag": "Altair Feko: computational electromagnetics (MoM/MLFMM/FEM/PO/UTD hybrids) for antennas, antenna placement on platforms, RCS, EMC",
        "best_for": [
            "antenna design (wire, patch, horn, reflector, arrays) and installed antenna performance on vehicles, aircraft, ships, satellites",
            "electrically large problems with MLFMM and asymptotic solvers (PO, LE-PO, RL-GO, UTD): RCS, co-site coupling, radomes",
            "EMC/EMI with cable harness coupling (MoM/MTL), shielding, lightning/HIRF, bio-electromagnetics (SAR)",
            "characteristic mode analysis, periodic structures/FSS/metasurfaces, windscreen antennas",
            "radio propagation and network planning with WinProp (ProMan/WallMan/AMan)",
            "headless automation verified on 2026.1: runfeko, cadfeko_batch variables, CADFEKO/POSTFEKO Lua scripts",
        ],
        "typical_tasks": ["monopole on a car roof: pattern and coupling at 1 GHz", "monostatic RCS of an aircraft at 3 GHz",
                          "cable harness immunity to a plane wave", "CMA of a handset chassis", "FSS unit cell reflection",
                          "indoor coverage map with WinProp"],
        "not_ideal_for": ["lumped circuits (PSpice)", "FEM-heavy package/connector/PCB detail (HFSS)", "general numerics (MATLAB)"],
        "prefer_over": {"hfss": "electrically large platforms (MLFMM/asymptotic hybrids), antenna placement, RCS, cable coupling, CMA",
                        "matlab": "any radiating or scattering field problem: MATLAB only post-processes",
                        "comsol": "antenna/RCS/EMC work; COMSOL when EM must couple to heat or mechanics"},
        "keywords": {"feko": 3, "cadfeko": 3, "postfeko": 3, "editfeko": 3, "method of moments": 3, "mom": 2, "mlfmm": 3,
                     "antenna placement": 3, "installed antenna": 3, "platform": 1, "aircraft": 2, "ship": 1, "vehicle": 1,
                     "car roof": 2, "satellite": 1, "rcs": 2, "radar cross": 3, "co-site": 3, "cosite": 3, "emc": 2, "emi": 2,
                     "cable harness": 3, "cable coupling": 3, "shielding": 2, "lightning": 2, "hirf": 3,
                     "sar": 2, "phantom": 2, "characteristic mode": 3, "cma": 2, "fss": 3, "frequency selective": 3,
                     "metasurface": 3, "metamaterial": 2, "windscreen": 3, "radome": 2, "reflector": 1, "horn": 1,
                     "antenna": 2, "radiation pattern": 2, "gain": 1, "far field": 2, "s11": 1, "physical optics": 3,
                     "uniform theory of diffraction": 3, "utd": 3, "electrically large": 3, "propagation": 2, "winprop": 3,
                     "asymptotic": 2, "monopole": 2, "dipole": 1, "wire antenna": 3, "hf antenna": 3, "yagi": 2,
                     "coverage map": 3, "path loss": 3, "ray tracing": 2, "radio coverage": 3, "network planning": 3,
                     "proman": 3, "indoor propagation": 3, "urban propagation": 3, "runfeko": 3},
    },
    "orcad": {
        "tag": "OrCAD PSpice/Capture/PCB Editor: analog & mixed-signal circuit simulation, schematics, PCB",
        "best_for": [
            "SPICE circuit simulation: bias point, DC/AC sweeps, transient, noise, Fourier, parametric, temperature",
            "op-amp, power, filter, oscillator and switching-converter circuits with real device and behavioural (ABM) models",
            "PSpice Probe measurements (rise time, bandwidth, overshoot, phase/gain margin) and Advanced Analysis "
            "(sensitivity, optimizer, Monte Carlo yield, Smoke stress/derating)",
            "digital and mixed-signal simulation with timing checks; model import and stimulus editing",
            "schematic capture with netlists, BOMs and Tcl automation (Capture); board layout and manufacturing outputs (PCB Editor)",
        ],
        "typical_tasks": ["transient of an RC low-pass with a 1 kHz square wave", "AC gain/phase and phase margin of an op-amp stage",
                          "Monte Carlo yield of a divider", "buck converter ripple with a real MOSFET model",
                          "netlist a schematic and export a BOM"],
        "not_ideal_for": ["field/antenna problems (use HFSS/Feko)", "block-diagram system models (use Simulink)"],
        "prefer_over": {"matlab": "any circuit with real components/models: MATLAB has no SPICE engine",
                        "altium": "when the goal is simulation; Altium when the goal is the board"},
        "keywords": {"spice": 3, "pspice": 3, "netlist": 3, "circuit": 2, "transient": 2, "ac sweep": 3, "dc sweep": 3,
                     "bias point": 3, "op-amp": 2, "op amp": 2, "opamp": 2, "amplifier": 2, "oscillator": 2,
                     "transistor": 2, "mosfet": 2, "bjt": 2, "diode": 2, "resistor": 1, "capacitor": 1, "inductor": 1,
                     "rc filter": 3, "rlc": 3, "low-pass filter circuit": 3, "monte carlo": 2, "worst case": 2,
                     "noise analysis": 2, "buck converter": 3, "boost converter": 3, "power supply": 2, "schematic": 2,
                     "orcad": 3, "capture": 1, "allegro": 3, "555": 2, "rectifier": 2, "voltage divider": 3,
                     "voltage": 1, "current": 1, "comparator": 2, "regulator": 2, "ldo": 3, "smps": 3, "flyback": 3,
                     "h-bridge": 3, "igbt": 3, "thyristor": 3, "zener": 3, "led driver": 3, "current mirror": 3,
                     "differential amplifier": 3, "common emitter": 3, "active filter": 3, "filter circuit": 3,
                     "bandpass": 1, "thd": 2, "distortion": 2, "slew rate": 2, "gain bandwidth": 2, "square wave": 2,
                     "waveform": 1, "ripple": 2, "load regulation": 3, "converter": 2, "inverter": 1, "cmos": 1,
                     "logic gate": 1, "pwm": 1, "transient analysis": 3, "spice model": 3, "rise time": 2,
                     "overshoot": 1, "phase margin": 2, "smoke analysis": 3, "derating": 3, "sensitivity analysis": 2,
                     "yield": 1, "stimulus": 2, "abm": 3, "behavioral model": 2, "behavioural model": 2,
                     "fourier analysis": 2, "parametric sweep": 1, "temperature sweep": 2, "capture tcl": 3},
    },
    "altium": {
        "tag": "Altium Designer: schematic capture, PCB layout/routing, stackups, fabrication outputs",
        "best_for": [
            "PCB design: placement, interactive/differential-pair routing, length tuning, layer stack and impedance profiles",
            "design rules and the query language (clearance, creepage, high-speed, manufacturing), batch/online DRC",
            "schematic capture with managed component libraries, ERC, variants, multi-channel and hierarchical designs",
            "manufacturing outputs: Gerber/ODB++/IPC-2581, drill, pick-and-place, ActiveBOM, Draftsman drawings",
            "multi-board assemblies, harness design, mixed-signal simulation, DelphiScript/API automation",
        ],
        "typical_tasks": ["export Gerbers and BOM for a board", "count components/tracks/vias of a PCB",
                          "run a script over a PCB document", "design-rule check of a layout",
                          "write a query rule for a net class clearance"],
        "not_ideal_for": ["detailed SPICE studies (use PSpice)", "field simulation (use HFSS/Feko)"],
        "prefer_over": {"orcad": "board layout and manufacturing outputs when the design lives in Altium",
                        "proteus": "professional multi-layer boards, rigid-flex, high-speed rules"},
        "keywords": {"pcb": 3, "layout": 2, "gerber": 3, "footprint": 3, "routing": 2, "trace": 1, "via": 1,
                     "stackup": 3, "layer stack": 3, "drc": 2, "bom": 2, "bill of materials": 2, "altium": 3,
                     "differential pair": 2, "length tuning": 3, "odb++": 3, "pick and place": 3, "silkscreen": 3,
                     "solder mask": 3, "schematic library": 2, "board": 1, "pcbdoc": 3, "schdoc": 3, "delphiscript": 3,
                     "component placement": 3, "placement": 2, "copper": 2, "polygon pour": 3, "keepout": 3,
                     "impedance control": 2, "controlled impedance": 3, "fabrication": 2, "assembly drawing": 3,
                     "drill": 2, "step file": 2, "3d model of the board": 3, "multilayer": 2, "multi-layer": 2,
                     "rigid-flex": 3, "design rule": 3, "erc": 2, "net class": 3, "netlist import": 2, "panelization": 3,
                     "creepage": 3, "clearance rule": 3, "query language": 2, "activebom": 3, "draftsman": 3,
                     "multi-board": 3, "wiring harness": 3, "harness design": 3, "ipc-2581": 3, "xsignal": 3,
                     "altium 365": 3, "managed component": 3, "impedance profile": 2},
    },
    "eagle": {
        "tag": ("Autodesk EAGLE (retired June 2026): schematic capture and PCB layout; reads EAGLE designs, command-line "
                "CAM (Gerber/Excellon), BOM and netlist, User Language programs and scripts"),
        "best_for": [
            "lightweight, quick schematic and PCB layout for simple to moderate (2-4-layer) boards, with library "
            "devices that carry manufacturer part numbers; the tool of choice for many hobby and open-hardware boards",
            "working with existing EAGLE designs (.sch/.brd/.lbr): parts, nets, signals, layers, design rules, variants",
            "manufacturing data from EAGLE boards with JSON CAM jobs: Gerber RS-274X/X2, Gerber job file, Excellon "
            "drill, assembly and pick-and-place (the command-line CAM Processor runs without signing in)",
            "BOMs and netlists extracted directly from the XML design files, without EAGLE",
            "open-hardware projects published as EAGLE files (Arduino, SparkFun, Adafruit, Seeed and many more)",
            "User Language programs (ULP) and command scripts (.scr) for batch edits and reports, and EAGLE's built-in "
            "ngspice simulation, where a signed-in EAGLE is still available",
        ],
        "typical_tasks": ["export Gerbers and drill files from an EAGLE .brd", "make a BOM from an EAGLE schematic",
                          "list the parts and nets of an EAGLE board", "check the layer setup and design rules of an EAGLE board",
                          "write an EAGLE script or ULP for a batch change"],
        "not_ideal_for": ["new professional designs: Autodesk stopped selling and supporting EAGLE on 7 June 2026 and its "
                          "editors need a sign-in that no longer works (use Altium, OrCAD or Fusion Electronics)",
                          "high-speed, constraint-driven layout (use Altium or Allegro)",
                          "simulation: its built-in ngspice covers operating point, DC, AC and transient only, without "
                          "Monte Carlo, worst-case or stress analyses and with few vendor models (use PSpice)",
                          "3D field simulation (use HFSS/Feko/COMSOL)"],
        "prefer_over": {"altium": "when the design already is an EAGLE file and only its data, CAM output, BOM or netlist is needed",
                        "orcad": "when the design already is an EAGLE file and only its data, CAM output, BOM or netlist is needed"},
        "keywords": {"eagle": 3, "eagle cad": 3, "autodesk eagle": 3, "eaglecon": 3, "ulp": 3, "user language program": 3,
                     "eagle library": 3, "lbr": 2, "fusion electronics": 2, "fusion 360 electronics": 2, "cam job": 2,
                     "cam processor": 2, "sparkfun": 1, "adafruit": 1, "open hardware": 1, "open-hardware": 1},
    },
    "proteus": {
        "tag": "Proteus: microcontroller/firmware co-simulation (VSM) with circuit, plus schematic and PCB",
        "best_for": [
            "simulating firmware on PIC/dsPIC/AVR/8051/ARM Cortex-M/MSP430, Arduino and Raspberry Pi Pico with the surrounding circuit",
            "large peripheral model library: LCD/OLED/TFT, sensors, motors, RTC, EEPROM, CAN, Ethernet, virtual terminals",
            "interactive virtual instruments (oscilloscope, logic analyser, I2C/SPI debuggers) and source-level firmware debugging",
            "VSM Studio compilers, Visual Designer flowchart programming and IoT Builder remote front panels",
            "schematic capture and PCB layout with design rules, autorouting, 3D view and Gerber/ODB++ outputs",
        ],
        "typical_tasks": ["run an Arduino sketch with an LCD and sensors in simulation", "inspect a .pdsprj project",
                          "debug a PIC HEX file against a virtual circuit", "simulate a CAN bus between two ECUs"],
        "not_ideal_for": ["high-accuracy analog SPICE studies (use PSpice)", "RF field problems (use HFSS)"],
        "prefer_over": {"orcad": "when a microcontroller and its firmware must run inside the simulation"},
        "keywords": {"proteus": 3, "firmware": 3, "microcontroller": 3, "arduino": 3, "pic": 2, "avr": 3, "atmega": 3,
                     "hex file": 3, "embedded": 2, "virtual instrument": 3, "vsm": 3, "pdsprj": 3, "8051": 3,
                     "stm32": 2, "lcd": 1, "sensor": 1, "isis": 2, "ares": 2, "keypad": 2, "seven segment": 3,
                     "7-segment": 3, "bootloader": 2, "esp32": 2, "esp8266": 2, "uart": 1, "i2c": 1, "spi": 1,
                     "servo": 1, "stepper": 1, "co-simulation": 3, "cosimulation": 3, "sketch": 2, "ino": 2,
                     "atmel": 2, "pic16": 3, "pic18": 3, "dspic": 3, "mplab": 3, "debug the firmware": 3,
                     "raspberry pi pico": 3, "rp2040": 3, "micropython": 3, "msp430": 3, "cortex-m": 2,
                     "visual designer": 3, "iot builder": 3, "can bus": 2, "virtual terminal": 3, "oled": 2},
    },
    "comsol": {
        "tag": ("COMSOL Multiphysics: general-purpose finite-element multiphysics - electromagnetics, structural, acoustics, "
                "fluid flow, heat transfer, chemistry/electrochemistry, plasma, semiconductors, optics, particles, optimization"),
        "best_for": [
            "almost any physics described by PDEs, and any coupling between them, on 1D/2D/3D geometry (built-in or CAD)",
            "electromagnetics: electrostatics, electric currents, magnetics and motors/transformers (AC/DC), microwaves and "
            "antennas (RF), photonics and waveguides (Wave Optics), lenses and stray light (Ray Optics), MEMS, plasma, "
            "semiconductor devices, electric discharges",
            "structural mechanics: solids, shells, beams, contact, nonlinear materials (plasticity, hyperelasticity, creep), "
            "composites, geomechanics, fatigue, rotordynamics, multibody dynamics, buckling and vibration",
            "acoustics and vibrations: pressure/thermoviscous acoustics, aeroacoustics, ultrasound, loudspeakers, mufflers, "
            "room acoustics, acoustic-structure interaction, piezoelectric transducers",
            "fluid flow: laminar, turbulent (RANS, LES), two-phase (level set, phase field), non-Newtonian and viscoelastic, "
            "mixers, microfluidics, porous media and groundwater, pipes and water hammer, rarefied/molecular flow, granular flow",
            "heat transfer: conduction, convection, conjugate heat transfer, radiation, phase change, bioheat, moisture, "
            "electronics cooling, metal processing (quenching, welding, additive manufacturing)",
            "chemistry and electrochemistry: reactors and reaction kinetics, species transport, batteries (Li-ion, lead-acid, "
            "pack thermal), fuel cells and electrolyzers, corrosion and cathodic protection, electrodeposition",
            "particle tracing (charged/fluid particles), shape/topology/parameter optimization, uncertainty quantification, "
            "custom PDEs and ODEs, apps (Application Builder) and automation (Java API, LiveLink for MATLAB/Excel/CAD)",
            "about 2,000 installed, documented example models (Application Library) to start from - searchable with "
            "comsol_search_examples and runnable with comsol_run_example",
        ],
        "typical_tasks": ["Electrical Heating in a Busbar Assembly (Joule heating, thermal)",
                          "Thermal Modeling of a Microchannel Heat Sink (conjugate heat transfer)",
                          "Tuning Fork (eigenfrequencies)", "Permanent Magnet Motor in 3D (torque, rotating machinery)",
                          "Microstrip Patch Antenna (RF Module)", "Loudspeaker Driver in a Vented Enclosure (acoustics)",
                          "Muffler with Perforates (transmission loss)", "Lamella Mixer (microfluidics)",
                          "Internal Short Circuit in a Lithium-Ion Battery", "Microwave Oven (microwave heating)",
                          "DC Corona Discharge in Air in a Point-to-Plane Configuration", "Si Solar Cell 1D",
                          "Water Hammer (pipe flow)", "Geothermal Doublet (porous media heat and flow)",
                          "Friction Stir Welding of an Aluminum Plate", "Pierce Electron Gun (charged particle tracing)",
                          "Topology Optimization of an MBB Beam", "Band-Gap Analysis of a Photonic Crystal"],
        "not_ideal_for": ["SPICE circuits with vendor device models (use PSpice)",
                          "electrically large antenna placement and RCS on whole vehicles (use Feko)",
                          "PCB/schematic layout (use Altium/OrCAD)", "pure data analysis and signal processing (use MATLAB)"],
        "prefer_over": {"matlab": "any spatial field problem on a geometry: MATLAB's PDE toolbox is far more limited",
                        "hfss": "when electromagnetics must couple to heat, mechanics or fluids, or HFSS is not installed",
                        "feko": "microwave heating, optics, and component-scale EM coupled to other physics",
                        "simulink": "distributed (spatial) physics; Simulink for lumped system dynamics"},
        "keywords": {"comsol": 3, "multiphysics": 3, "finite element": 3, "fem": 3, "heat transfer": 3, "thermal": 2,
                     "temperature field": 3, "conduction": 2, "convection": 2, "structural": 3, "stress": 2, "strain": 2,
                     "deformation": 2, "vibration": 2, "eigenfrequency": 3, "fluid": 2, "cfd": 3, "laminar": 3,
                     "turbulent": 3, "navier": 3, "acoustic": 3, "piezo": 3, "electrostatic": 3, "magnetic field": 2,
                     "joule heating": 3, "diffusion": 2, "chemical": 2, "reaction": 1, "porous": 3, "mesh": 1,
                     "pde": 2, "mph": 3, "geometry": 1, "material": 1, "beam": 1, "plate": 1, "thermal expansion": 3,
                     "heat sink": 3, "heatsink": 3, "temperature": 2, "heat": 1, "cooling": 2, "airflow": 2,
                     "forced air": 3, "natural convection": 3, "heat flux": 3, "thermal analysis": 3, "fluid flow": 3,
                     "flow": 1, "pressure drop": 3, "electrode": 2, "capacitance": 2, "magnet": 2, "coil": 1,
                     "battery": 3, "fuel cell": 3, "membrane": 2, "microfluidic": 3, "wave optics": 3, "photonic": 2,
                     "buckling": 3, "fatigue": 2, "modal": 2, "natural frequency": 3, "solid mechanics": 3,
                     "von mises": 3, "displacement": 2, "boundary condition": 2, "multiphysics coupling": 3,
                     "hyperelastic": 3, "phase change": 3, "radiation heat": 3, "mass transfer": 3,
                     "electrochemistry": 3, "lithium-ion": 3, "li-ion": 3, "corrosion": 3, "electrodeposition": 3,
                     "plasma": 3, "particle tracing": 3, "topology optimization": 3, "shape optimization": 3,
                     "two-phase": 3, "level set": 3, "fluid-structure": 3, "mems": 3, "thermoelastic": 3,
                     "weak form": 3, "application builder": 3, "livelink": 2, "ray optics": 3, "lens": 2,
                     "induction heating": 3, "eddy current": 3, "electric motor": 3, "permanent magnet": 2,
                     "transformer": 2, "inductance": 2, "magnetostatic": 3, "torque": 1, "busbar": 3,
                     "microwave heating": 3, "optical waveguide": 3, "photonic crystal": 3, "laser": 1, "optical": 1,
                     "lens design": 3, "stray light": 3, "loudspeaker": 3, "muffler": 3, "transmission loss": 3,
                     "sound": 2, "ultrasound": 3, "transducer": 2, "room acoustics": 3, "aeroacoustic": 3,
                     "rotordynamics": 3, "bearing": 1, "gear": 1, "composite": 2, "laminate": 3, "creep": 3,
                     "plasticity": 3, "contact pressure": 3, "crack": 2, "geomechanics": 3, "soil": 2, "groundwater": 3,
                     "darcy": 3, "aquifer": 3, "geothermal": 3, "mixer": 3, "stirred tank": 3, "non-newtonian": 3,
                     "polymer flow": 3, "viscoelastic": 2, "vacuum": 2, "molecular flow": 3, "water hammer": 3,
                     "pipe network": 3, "granular": 3, "heat exchanger": 3, "bioheat": 3, "tissue": 2, "moisture": 3,
                     "additive manufacturing": 3, "welding": 3, "quenching": 3, "phase transformation": 3,
                     "chemical reactor": 3, "catalyst": 3, "combustion": 2, "cstr": 3, "electrolyzer": 3,
                     "hydrogen": 2, "electroplating": 3, "cathodic protection": 3, "particle trajectory": 3,
                     "electron beam": 3, "electron gun": 3, "charged particle": 3, "ion optics": 3,
                     "uncertainty quantification": 3, "surrogate model": 2, "parameter estimation": 2,
                     "inverse problem": 2, "schrodinger": 3, "quantum well": 3, "thermoelectric": 3, "peltier": 3,
                     "piezoelectric": 3, "magnetohydrodynamics": 3, "electrophoresis": 3, "dielectrophoresis": 3,
                     "semiconductor device": 3, "doping": 3, "pn junction": 3, "p-n junction": 3, "solar cell": 3,
                     "corona": 3, "electric discharge": 3, "dielectric breakdown": 3, "sloshing": 3,
                     "application library": 3, "antenna": 1, "waveguide": 1, "s-parameter": 1},
    },
    "matlab": {
        "tag": "MATLAB: numerical computing, data analysis, signal/image processing, control design, plotting",
        "best_for": [
            "numerical algorithms, linear algebra, optimization, statistics, curve fitting and data analysis",
            "signal/image/audio processing and filter design; computer vision, lidar/point clouds, medical imaging",
            "control design (transfer functions, PID, Bode, LQR), system identification, sensor fusion and tracking",
            "machine, deep and reinforcement learning, text analytics, predictive maintenance",
            "communications at system level: 5G/LTE/WLAN/Bluetooth waveforms, BER, phased-array and radar signal processing",
            "fixed-point design, C/HDL code generation, deployment (MATLAB Compiler), plots and post-processing of other tools",
        ],
        "typical_tasks": ["design an IIR filter and plot its response", "fit a model to measured data",
                          "compute Bode plot and PID gains", "analyse a CSV and export a figure",
                          "BER curve of a QAM link", "train a small CNN classifier", "Kalman filter for IMU/GPS fusion"],
        "not_ideal_for": ["field simulation (HFSS/Feko/COMSOL)", "SPICE circuits (PSpice)", "PCB (Altium)",
                          "exact symbolic derivations (Mathematica)"],
        "prefer_over": {"mathematica": "numeric-heavy work, toolboxes (signal, control, image, comms), engineering plots"},
        "keywords": {"matlab": 3, "fft": 2, "filter design": 3, "iir": 3, "fir": 3, "butterworth": 3, "spectrum": 2,
                     "signal processing": 3, "control system": 3, "pid": 3, "bode": 3, "transfer function": 3,
                     "root locus": 3, "state space": 2, "matrix": 2, "eigenvalue": 2, "least squares": 3, "curve fit": 3,
                     "regression": 2, "optimization": 2, "statistics": 2, "plot": 2, "figure": 1, "csv": 1,
                     "data analysis": 3, "image processing": 3, "machine learning": 2, "neural network": 2,
                     "numerical": 2, "ode45": 3, "toolbox": 2, "interpolation": 2, "frequency response": 2,
                     "fourier": 2, "wavelet": 3, "kalman": 3, "lqr": 3, "histogram": 2, "solve ode": 3,
                     "differential equation": 2, "linear algebra": 3, "svd": 3, "pca": 3, "classification": 2,
                     "deep learning": 3, "audio": 2, "spectrogram": 3, "correlation": 2, "convolution": 2,
                     "resample": 2, "sampling rate": 2, "step response": 2, "impulse response": 2,
                     "stability margin": 3, "nyquist": 2, "lti": 3, "system identification": 3, "post-process": 2,
                     "post process": 2, "postprocess": 2, "excel": 2, "xlsx": 2, "mat file": 3, ".mat": 3,
                     "script": 1, "vectorize": 3, "monte carlo simulation": 2, "random": 1, "simulate numerically": 3,
                     "reinforcement learning": 3, "cnn": 2, "train a network": 3, "text analytics": 3, "sentiment": 2,
                     "5g nr": 3, "lte": 3, "wlan": 3, "ber": 3, "bit error rate": 3, "qam": 3, "ofdm": 3,
                     "modulation": 2, "waveform generation": 3, "radar signal processing": 3, "direction of arrival": 3,
                     "doa": 2, "sensor fusion": 3, "imu": 2, "tracking filter": 3, "point cloud": 2, "lidar": 2,
                     "computer vision": 3, "object detection": 3, "remaining useful life": 3, "predictive maintenance": 3,
                     "fixed-point": 3, "fixed point": 2, "hdl code": 2, "standalone application": 2, "dicom": 3,
                     "genomics": 3, "uav": 2, "path planning": 2, "robot": 2, "inverse kinematics": 3, "timetable": 2},
    },
    "simulink": {
        "tag": "Simulink: block-diagram modelling and simulation of dynamic systems, controls, Stateflow",
        "best_for": [
            "time-domain simulation of dynamic systems from block diagrams (plants, controllers, sensors)",
            "control loops and supervisory logic with Stateflow state machines, truth tables and temporal logic",
            "physical modelling with Simscape (electrical, mechanical, thermal, fluids) and Simscape Multibody",
            "verification: Simulink Test harnesses, coverage (MC/DC), Design Verifier, Requirements Toolbox, Model Advisor",
            "code generation (Simulink/Embedded Coder, AUTOSAR, HDL Coder, PLC Coder) and hardware deployment (Arduino, STM32, C2000)",
        ],
        "typical_tasks": ["simulate model vdp for 20 s and plot states", "tune a PID loop on a plant model",
                          "list blocks and change a gain in a model", "add a Stateflow supervisor to a controller",
                          "run a parameter sweep with parsim", "generate C code from a subsystem"],
        "not_ideal_for": ["circuit-level electronics with device models (PSpice)", "field problems (COMSOL/HFSS)"],
        "prefer_over": {"matlab": "when the system is naturally a block diagram or already exists as a .slx model"},
        "keywords": {"simulink": 3, "block diagram": 3, ".slx": 3, "slx": 3, "stateflow": 3, "simscape": 3,
                     "dynamic system": 3, "closed loop": 2, "controller": 2, "plant model": 3, "model reference": 2,
                     "code generation": 2, "embedded coder": 3, "hil": 2, "simulation model": 2, "feedback loop": 2,
                     "s-function": 3, "scope block": 3, "simulate the model": 3, "dynamic model": 2, "mechatronic": 2,
                     "hybrid system": 2, "sample time": 3, "model predictive": 3, "mpc": 2, "pid tuning": 2,
                     "block parameters": 3, "subsystem": 3, "signal builder": 3, "lookup table": 2, "powertrain": 2,
                     "state machine": 2, "multibody": 3, "autosar": 3, "hdl coder": 3, "plc": 2, "test harness": 3,
                     "model coverage": 3, "mc/dc": 3, "mcdc": 3, "design verifier": 3, "requirements traceability": 3,
                     "parsim": 3, "fast restart": 3, "rapid accelerator": 3, "motor control": 2, "field-oriented": 3,
                     "foc": 2, "physical model": 2, "power electronics simulation": 3},
    },
    "mathematica": {
        "tag": "Wolfram Mathematica: symbolic mathematics, exact/analytic solutions, special functions, knowledge",
        "best_for": [
            "symbolic algebra and calculus: closed-form integrals, series, limits, transforms, exact ODE/PDE solutions",
            "proofs and simplification, special functions, number theory, combinatorics, discrete math and graph theory",
            "high-precision and interval arithmetic, uncertainty propagation (Around), probability distributions",
            "curated knowledge and entities (units, chemistry, geography, astronomy, finance), geometry and regions",
            "machine learning and neural networks, image/audio processing, cloud APIs and deployment, publication-quality graphics",
        ],
        "typical_tasks": ["closed form of an integral", "solve an ODE symbolically", "series expansion",
                          "simplify a transfer function symbolically", "shortest path and centrality of a graph",
                          "unit-aware physical calculation", "fit a probability distribution to data"],
        "not_ideal_for": ["large numeric toolbox-based workflows (MATLAB)", "physical simulations (COMSOL/HFSS)"],
        "prefer_over": {"matlab": "exact/symbolic results, special functions, arbitrary precision, curated data"},
        "keywords": {"mathematica": 3, "wolfram": 3, "symbolic": 3, "closed form": 3, "closed-form": 3, "analytic": 2,
                     "exact": 2, "integral": 2, "integrate": 2, "derivative": 1, "series expansion": 3, "taylor": 2,
                     "limit": 1, "simplify": 3, "prove": 2, "special function": 3, "bessel": 2, "gamma function": 2,
                     "number theory": 3, "prime": 2, "combinatorics": 2, "laplace transform": 2, "dsolve": 3,
                     "symbolically": 3, "exact solution": 3, "algebra": 2, "polynomial": 2, "factor": 1, "expand": 1,
                     "theorem": 2, "infinite sum": 3, "sum of the series": 3, "differential equation": 2,
                     "recurrence": 3, "generating function": 3, "arbitrary precision": 3, "high precision": 3,
                     "digits of": 3, "wolfram alpha": 3, "residue": 2, "contour integral": 3, "identity": 1,
                     "elliptic": 2, "hypergeometric": 3, "zeta": 3, "in closed form": 3, "solve exactly": 3,
                     "graph theory": 3, "shortest path": 2, "centrality": 3, "uncertainty propagation": 3,
                     "error propagation": 2, "unit conversion": 2, "units": 1, "chemical element": 3,
                     "entity": 1, "asymptotic": 2, "mellin": 3, "z-transform": 3, "ztransform": 3, "group theory": 3,
                     "permutation group": 3, "notebook": 1, "cloud deploy": 3, "api function": 2},
    },
    "vivado": {
        "tag": "AMD Vivado: FPGA design flow (RTL synthesis, implementation, timing, bitstream, IP integrator, HLS)",
        "best_for": [
            "Verilog/SystemVerilog/VHDL synthesis and implementation for AMD FPGAs/SoCs, timing closure and utilization/power reports",
            "XDC timing and physical constraints, pblocks, netlist queries, ECO, incremental and DFX (partial reconfiguration) flows",
            "block designs with IP (Zynq/MPSoC/Versal CIPS, MicroBlaze, AXI, DMA, transceivers), IP packaging and board files",
            "Vitis HLS C/C++-to-RTL with pragmas and co-simulation; xsim and third-party simulation",
            "hardware manager (programming, flash, ILA/VIO, IBERT), Vitis embedded (XSCT, bootgen boot images, XRT)",
        ],
        "typical_tasks": ["synthesize a Verilog project and report timing", "generate and program a bitstream",
                          "add an AXI GPIO to a block design", "write XDC constraints for a clock and I/O pins",
                          "build a BOOT.BIN with bootgen", "synthesize a C function with Vitis HLS"],
        "not_ideal_for": ["analog circuits (PSpice)", "generic numeric work (MATLAB)"],
        "prefer_over": {},
        "keywords": {"vivado": 3, "fpga": 3, "verilog": 3, "vhdl": 3, "bitstream": 3, "synthesis": 2, "rtl": 3,
                     "timing closure": 3, "xdc": 3, "constraint": 1, "zynq": 3, "microblaze": 3, "axi": 3, "ip integrator": 3,
                     "block design": 2, "hls": 3, "xilinx": 3, "artix": 3, "kintex": 3, "ultrascale": 3, "ila": 2,
                     "hdl": 3, "tcl": 1, "lut": 2, "flip-flop": 2, "clock domain": 3, "pll": 1, "mmcm": 3, "bram": 3,
                     "dsp48": 3, "systemverilog": 3, "testbench": 3, "xsim": 3, "jtag": 2, "program the board": 2,
                     "zcu": 3, "basys": 3, "nexys": 3, "arty": 3, "pynq": 3, "vitis": 3, "utilization": 2,
                     "slack": 1, "place and route": 3, "implementation run": 3, "logic analyzer": 1, "uart": 1,
                     "spi": 1, "i2c": 1, "axi stream": 3, "dma": 2, "soft processor": 3, "partial reconfiguration": 3,
                     "dfx": 3, "bootgen": 3, "boot.bin": 3, "xsct": 3, "petalinux": 3, "xpm": 3, "ibert": 3,
                     "transceiver": 2, "gty": 3, "gth": 3, "versal": 3, "mpsoc": 3, "rfsoc": 3, "pblock": 3,
                     "false path": 3, "multicycle": 3, "create_clock": 3, "cdc": 2},
    },
    "autocad": {
        "tag": "Autodesk AutoCAD: 2D/3D CAD drafting, DWG/DXF drawings, dimensions, layouts, plotting",
        "best_for": [
            "technical drawings: floor plans, mechanical parts, schematics as DWG/DXF with layers, dimensions and annotation scaling",
            "blocks, attributes and dynamic blocks; parametric constraints; xrefs, PDF/DGN underlays and point clouds",
            "layouts, viewports, plotting/publishing and sheet sets; drawing comparison and standards checks",
            "3D solids, surfaces and meshes, visual styles, rendering and model documentation views",
            "automation through scripts, AutoLISP/Visual LISP, .NET/ActiveX APIs and the headless core console",
        ],
        "typical_tasks": ["export a DWG to DXF/PDF", "list layers and blocks of a drawing",
                          "draw a plate with holes to scale", "update attribute values in a title block",
                          "batch-plot all layouts of a folder"],
        "not_ideal_for": ["PCB layout (Altium)", "simulation of any kind"],
        "prefer_over": {},
        "keywords": {"autocad": 3, "dwg": 3, "dxf": 3, "drawing": 2, "draft": 2, "floor plan": 3, "cad": 2,
                     "dimension": 2, "layer": 1, "block": 1, "autolisp": 3, "lisp": 2, "plot to pdf": 3, "title block": 3,
                     "mechanical drawing": 3, "2d drawing": 3, "site plan": 3, "elevation": 2, "section view": 2,
                     "hatch": 3, "polyline": 3, "isometric": 2, "scale drawing": 3, "annotation": 2, "blueprint": 3,
                     "plan view": 3, "architectural": 2, "civil": 2, "survey": 1, "xref": 3, "paper space": 3,
                     "model space": 3, "viewport": 2, "3d solid": 2, "extrude": 1, "to scale": 2, "dynamic block": 3,
                     "block attribute": 3, "sheet set": 3, "batch plot": 3, "publish layouts": 3,
                     "parametric constraint": 2, "pdf underlay": 3, "mleader": 3, "multileader": 3, "dimstyle": 3,
                     "accoreconsole": 3, "visual lisp": 3, "overkill": 3, "purge": 2},
    },
    "photoshop": {
        "tag": "Adobe Photoshop: raster image editing, retouching, layers, filters, batch export",
        "best_for": [
            "photo retouching, colour correction and raw development (Camera Raw)",
            "layer-based compositing: masks, blend modes, smart objects, type, layer styles",
            "filters, Neural Filters and generative AI (Generative Fill/Expand, Remove tool)",
            "batch resize/convert/watermark, actions, droplets, exporting layers and artboards",
            "automation with ExtendScript, Action Manager/batchPlay, UXP plugins and the Photoshop cloud API",
        ],
        "typical_tasks": ["resize and sharpen a folder of photos", "export each layer of a PSD as PNG",
                          "run a recorded action on files", "remove the background of product photos",
                          "replace a smart object in a mockup"],
        "not_ideal_for": ["scientific image analysis (MATLAB)", "vector diagrams (draw.io)"],
        "prefer_over": {},
        "keywords": {"photoshop": 3, "psd": 3, "photo": 2, "retouch": 3, "image": 1, "png": 1, "jpeg": 1, "jpg": 1,
                     "layer": 1, "filter": 1, "resize": 2, "crop": 2, "colour correction": 3, "color correction": 3,
                     "watermark": 3, "batch": 1, "jsx": 3, "action": 1, "mask": 1, "composite": 2, "sharpen": 2,
                     "brightness": 2, "contrast": 2, "saturation": 2, "remove background": 3, "background removal": 3,
                     "thumbnail": 2, "poster": 2, "mockup": 2, "smart object": 3, "tiff": 2, "webp": 2,
                     "export layers": 3, "adjustment layer": 3, "blur": 2, "levels": 1, "curves": 1, "photo editing": 3,
                     "convert images": 2, "red eye": 3, "collage": 3, "banner": 1, "dpi": 1, "cmyk": 3,
                     "generative fill": 3, "generative expand": 3, "camera raw": 3, "raw photo": 2, "neural filter": 3,
                     "uxp": 3, "batchplay": 3, "extendscript": 3, "blend mode": 2, "droplet": 3, "artboard": 2},
    },
    "drawio": {
        "tag": "draw.io: diagrams (flowcharts, block diagrams, architecture, UML, network) as editable files and PNG/SVG/PDF",
        "best_for": [
            "flowcharts, block diagrams, architecture and network diagrams for documentation",
            "UML, ER, BPMN, C4, ArchiMate and cloud architecture diagrams with vendor shape libraries (AWS, Azure, GCP, Cisco, Kubernetes)",
            "programmatic diagram generation: XML, CSV import with layout, Mermaid/PlantUML, custom shapes and libraries",
            "export to PNG/SVG/PDF/VSDX via the desktop CLI; embedding via iframe/postMessage and the HTML viewer",
        ],
        "typical_tasks": ["flowchart of a process", "system architecture diagram", "convert a .drawio to PNG",
                          "org chart from a CSV", "AWS architecture diagram"],
        "not_ideal_for": ["electrical schematics for simulation (OrCAD/Altium)", "engineering drawings (AutoCAD)"],
        "prefer_over": {},
        "keywords": {"draw.io": 3, "drawio": 3, "diagram": 3, "flowchart": 3, "flow chart": 3, "block diagram": 2,
                     "architecture diagram": 3, "uml": 3, "org chart": 3, "network diagram": 3, "mind map": 3,
                     "sequence diagram": 3, "swimlane": 3, "er diagram": 3, "entity relationship": 3,
                     "class diagram": 3, "state machine diagram": 3, "wireframe": 2, "process map": 3, "topology": 2,
                     "bpmn": 3, "workflow": 2, "pipeline diagram": 3, "system diagram": 3, "boxes and arrows": 3,
                     "nodes and edges": 3, "gantt": 1, "infographic": 1, "deployment diagram": 3, "data flow": 3,
                     "dfd": 3, "visualize the architecture": 3, "documentation diagram": 3, "aws architecture": 3,
                     "azure architecture": 3, "kubernetes diagram": 3, "c4 model": 3, "archimate": 3, "mermaid": 2,
                     "plantuml": 2, "csv to diagram": 3, "vsdx": 2, "visio": 2},
    },
}

TOOL_PREFIX = {app: (["orcad_"] if app == "orcad" else [f"{app}_"]) for app in APP_PROFILES}

ROUTING_GUIDE = """
Choosing the right application (call recommend_application(task) when unsure; it also tells you what is installed
and compares the candidates):
- Antennas, RF/microwave components, S-parameters, radiation patterns, signal integrity, unit cells -> HFSS (hfss_*).
- Antenna placement on vehicles/aircraft/ships, electrically large RCS, EMC with cable harnesses, characteristic
  modes, FSS/metasurfaces, windscreen antennas, radio coverage (WinProp) -> Feko (feko_*).
  Plain antenna design: HFSS or Feko, whichever is installed.
- Coupled or distributed physics on a geometry - heat, structural, fluid/CFD, electrostatics, magnetics and motors,
  acoustics, electrochemistry and batteries, plasma, semiconductors, optics, MEMS, particle tracing, topology
  optimization - -> COMSOL (comsol_*). COMSOL covers almost every physics: search its ~2,000 installed examples with
  comsol_search_examples, start from the closest one (comsol_run_example / its Java script) and read results with
  comsol_inspect_model / comsol_evaluate. It can also solve antennas and RF components when HFSS/Feko is missing.
- Circuit simulation with real components (transient/AC/DC/noise/Monte Carlo/Smoke, op-amps, converters) -> PSpice (orcad_pspice_*).
- PCB layout, footprints, design rules, Gerbers/ODB++, BOM, Draftsman -> Altium (altium_*); OrCAD/Proteus when the design lives there.
- Existing EAGLE designs (.sch/.brd/.lbr): parts, nets, BOM, netlist, CAM jobs (Gerber/Excellon) -> EAGLE (eagle_*).
  Autodesk retired EAGLE on 7 June 2026: its editors need a sign-in, so eagle_* reads designs and runs the CAM
  Processor; new layout work goes to Altium or OrCAD.
- Firmware running with its circuit (Arduino/PIC/AVR/ARM/Pico), virtual instruments -> Proteus (proteus_*).
- FPGA: Verilog/VHDL synthesis, XDC timing, bitstreams, IP block designs, HLS, boot images -> Vivado (vivado_*).
- Block-diagram dynamic systems, control loops, Stateflow, Simscape physical models, .slx code generation -> Simulink (simulink_*).
- Exact/symbolic math, closed forms, special functions, graph theory, curated data, proofs -> Mathematica (mathematica_*).
- Numeric computing, signal/image/audio processing, filter and control design, ML/DL, comms waveforms and BER,
  sensor fusion, data analysis, plots -> MATLAB (matlab_*).
- DWG/DXF technical drawings -> AutoCAD (autocad_*). Raster photos -> Photoshop (photoshop_*). Diagrams/flowcharts -> draw.io (drawio_*).
Rules: these are suggestions, not requirements. Prefer the specialised application over MATLAB whenever one fits
and is installed; use MATLAB for numerics, post-processing and plotting around it. When several applications can do
the task, compare_applications(task) ranks them with each one's strength, when to choose it and its limits. If the
suggested application is not installed, or the user asks for another one, use an installed candidate that can
substitute and tell the user what it gives up. Read software_overview (capability_areas) and describe_tool/search_tools
for the application's own commands first."""

_WORD = re.compile(r"[a-z0-9.+#/-]+")


NAMED_BONUS = 5


def recommend(task: str, installed: set[str] | None = None, limit: int = 4) -> list[dict[str, Any]]:
    """Rank applications for a task by weighted keyword matches; ties broken by installation status. An application
    the task names itself ("in Simulink", "my EAGLE board") gets a bonus so the user's choice comes first."""
    from .comparisons import named_apps

    text = " " + " ".join(_WORD.findall(task.lower())) + " "
    named = set(named_apps(task))
    rows = []
    for app, prof in APP_PROFILES.items():
        score = NAMED_BONUS if app in named else 0
        hits: list[str] = []
        for kw, weight in prof["keywords"].items():
            if f" {kw} " in text or (len(kw) > 4 and kw in text):
                score += weight
                hits.append(kw)
        if score <= 0:
            continue
        is_installed = None if installed is None else app in installed
        rows.append({
            "application": app,
            "score": score + (1 if is_installed else 0),
            "installed": is_installed,
            "matched": sorted(hits, key=lambda k: -prof["keywords"][k])[:8],
            "tag": prof["tag"],
            "best_for": prof["best_for"],
            "tools_prefix": TOOL_PREFIX[app],
            "prefer_over": prof.get("prefer_over", {}),
        })
    rows.sort(key=lambda r: (-r["score"], r["application"]))
    for r in rows:
        if r["installed"] is False:
            r["note"] = (f"{r['application']} is not installed on this machine; say so to the user, then consider the "
                         "next candidate or explain what MATLAB can and cannot do instead.")
    return rows[:limit]


@cache
def capability_areas(app: str) -> tuple[tuple[str, int], ...]:
    """(category title, entry count) for every chunk of the application's catalog."""
    try:
        from .catalog import get_catalog

        sc = get_catalog().get_software(app)
    except Exception:  # noqa: BLE001 - unknown app or unreadable catalog
        return ()
    counts: dict[str, int] = {}
    for t in sc.tools:
        counts[t.category] = counts.get(t.category, 0) + 1
    return tuple(counts.items())


def profile(app: str, with_areas: bool = False) -> dict[str, Any]:
    p = APP_PROFILES.get(app, {})
    out = {k: p[k] for k in ("tag", "best_for", "typical_tasks", "not_ideal_for", "prefer_over") if k in p}
    if with_areas:
        areas = capability_areas(app)
        out["capability_areas"] = [{"area": a, "catalog_entries": n} for a, n in areas]
        out["catalog_entries_total"] = sum(n for _, n in areas)
    return out


def tag(app: str) -> str:
    return APP_PROFILES.get(app, {}).get("tag", app)
