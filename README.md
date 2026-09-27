# MCP-ADAPTER

A single [Model Context Protocol](https://modelcontextprotocol.io) server that gives AI agents two things for
fourteen engineering and design applications:

1. **A comprehensive tool catalog** – what each application can do, how every tool/command/function is used,
   with syntax, parameters, a runnable example and a documentation link. Fully searchable through MCP tools.
2. **Headless automation adapters** – run the real application when it is installed (batch/script mode),
   capture its output and return structured JSON.

Plus a timezone-aware **time tool** and an optional **live documentation search** (Tavily).

> [!WARNING]
> **Network exposure: read this before installing.**
> With the default `stdio` transport this server opens **no network port**. With `--transport streamable-http`
> or `sse` it **listens on a TCP port**, and Windows Firewall will ask whether to allow *inbound* connections to
> Python. **If you use it locally, you MUST deny that prompt.** Loopback traffic is never filtered by the firewall,
> so everything keeps working and nothing becomes reachable from other machines.
> The choice is also enforced in code, not only by the firewall: in the default **`local` mode** the server refuses
> to bind anything but `127.0.0.1` and rejects requests whose `Host` header is not localhost, and tools that call
> external web APIs are not even registered. Pick the mode once with `mcp-adapter-setup` and change it any time in
> `.env`. Details: [Security and network policy](#security-and-network-policy).

| Application | Catalog id | Automation mechanism used by the adapter |
|---|---|---|
| MATLAB | `matlab` | `matlab -batch`, JSON round-trip of workspace variables |
| Simulink | `simulink` | MATLAB API (`sim`, `Simulink.SimulationInput`, `find_system`, `set_param`) |
| Wolfram Mathematica | `mathematica` | `wolframscript -code / -file`, `Export[]` |
| COMSOL Multiphysics | `comsol` | `comsolbatch` (studies, parameter lists, model methods), `comsol compile`, MPh Python |
| Adobe Photoshop | `photoshop` | ExtendScript/JSX via COM (`Photoshop.Application`), osascript or direct launch |
| Cadence OrCAD (Capture, PSpice, PCB Editor) | `orcad` | `pspice.exe` batch runs + `.out` parsing, Capture Tcl scripts |
| Altium Designer | `altium` | `X2.EXE -RScriptFile/-RProcName` DelphiScript projects |
| Proteus Design Suite | `proteus` | GUI launch, custom CLI pass-through, `.pdsprj` inspection |
| AMD Vivado | `vivado` | `vivado -mode batch -source` Tcl, Hardware Manager, `vitis_hls` |
| Autodesk AutoCAD | `autocad` | `accoreconsole.exe` scripts + AutoLISP, DXFOUT, -PLOT, COM `SendCommand` |
| Ansys HFSS (Electronics Desktop) | `hfss` | `ansysedt -ng -RunScriptAndExit` IronPython, `-BatchSolve`, PyAEDT |
| Autodesk EAGLE (retired June 2026) | `eagle` | `eaglecon -X -dCAMJOB` CAM jobs, XML design reading (BOM, netlist), `-C` editor commands |
| draw.io / diagrams.net | `drawio` | pure-Python mxGraph XML generation + desktop CLI export |

## Catalog size

<!-- CATALOG_STATS -->
| Software | Vendor | Reference version | Categories | Catalog entries |
|---|---|---|---:|---:|
| MATLAB | MathWorks | R2025a | 62 | 2099 |
| Wolfram Mathematica | Wolfram Research | 14.2 | 77 | 2458 |
| COMSOL Multiphysics | COMSOL AB | 6.4 (verified against a local installation: help, completion data, plugins and Application Library) | 128 | 3836 |
| Adobe Photoshop | Adobe | 2025 (v26) | 23 | 734 |
| Cadence OrCAD / OrCAD X (Capture, PSpice, PCB Editor) | Cadence Design Systems | OrCAD X 23.1 / 24.1 | 17 | 614 |
| Altium Designer | Altium | 24/25 | 21 | 735 |
| Proteus Design Suite (ISIS schematic capture, VSM simulation, ARES PCB layout) | Labcenter Electronics | 8.17 / 9 | 11 | 397 |
| AMD Vivado Design Suite | AMD (Xilinx) | 2024.2 / 2025.1 | 19 | 699 |
| Autodesk AutoCAD | Autodesk | 2025 / 2026 | 30 | 1232 |
| Ansys HFSS (Ansys Electronics Desktop) | Ansys | 2025 R1 | 15 | 502 |
| Simulink | MathWorks | R2025a | 18 | 573 |
| draw.io (diagrams.net) | JGraph Ltd | 26.x desktop | 12 | 470 |
| Altair Feko | Altair Engineering | 2025.1 (verified against a 2026.1 installation and the online reference) | 13 | 465 |
| Autodesk EAGLE | Autodesk (originally CadSoft Computer) | 9.6.0 (verified against a local 9.6.0 installation, its built-in help and manuals; the last release was 9.6.2) | 19 | 853 |
| **Total** | | | | **15667** |
<!-- /CATALOG_STATS -->

Catalogs live in [`mcp_adapter/catalogs/<software>/`](mcp_adapter/catalogs) as plain JSON, one category per file,
following [CATALOG_SCHEMA.md](CATALOG_SCHEMA.md). They were compiled from the vendors' official documentation
(MathWorks, Wolfram, COMSOL, Adobe, Cadence, Altium, Labcenter, AMD, Autodesk, Ansys, Altair, JGraph) and every entry
records `name`, `kind`, `description`, `usage`, `parameters`, `example`, `notes` and `docs`.

## Installation

```bash
git clone https://github.com/Babak-Solhjoo/MCP-ADAPTER-PV.git
cd MCP-ADAPTER-PV
python -m pip install -e ".[dev]"
mcp-adapter-setup        # answer two questions: local-only or network? internet tools on or off?
```

`mcp-adapter-setup` writes the answers to `.env` (creating it from `.env.example` if needed) and prints what
they mean for the firewall. Skipping it is safe: the defaults are **local-only** and **internet tools off**.

Requirements: Python 3.10+, the `mcp` SDK (1.x and 2.x are both supported). Nothing else is needed for the
catalog and time tools. For automation, the corresponding application must be installed on the same machine.

## Security and network policy

The firewall is the last line of defence, not the only one. Two switches in `.env` decide what the server may
do on the network, and the server enforces them itself at start-up and on every request:

| Setting | Values | Effect |
|---|---|---|
| `MCP_ADAPTER_NETWORK_MODE` | `local` (default) / `network` | `local`: HTTP/SSE transports may only bind `127.0.0.1`/`localhost`; any other `--host` aborts start-up (exit code 2). DNS-rebinding protection rejects requests whose `Host`/`Origin` header is not localhost. `network`: other interfaces are allowed when explicitly requested with `--host`. |
| `MCP_ADAPTER_ALLOWED_HOSTS` | comma list | `network` mode only: `Host` header allow-list (e.g. `192.168.1.20:8000`); enables rebinding protection for those hosts. |
| `MCP_ADAPTER_AUTH_TOKEN` | random string (24+ characters) | Bearer token for the HTTP transports. **Required for every HTTP transport**, local mode included (other programs and other accounts on the same computer share `127.0.0.1`): without it the server refuses to start an HTTP transport, and every request must send `Authorization: Bearer <token>` (compared in constant time; wrong or missing tokens get `401`). HTTP is also refused if the installed `mcp` package lacks Host/Origin protection. `mcp-adapter-setup` and the workspace UI's endpoint generate the token; it stays in `.env` and is never shown back in the UI. stdio needs no token. |
| `MCP_ADAPTER_ALLOW_INTERNET` | `true` / `false` (default) | When `false`, `search_docs_online` (Tavily) and `mathematica_wolfram_alpha` are **not registered at all**, so no code path can reach the internet. Everything else is local subprocess automation. |

What each transport does:

* **`stdio` (default)** – the MCP client starts `mcp-adapter` as a child process and talks over pipes. No socket
  is opened, no firewall prompt can appear for it.
* **`streamable-http` / `sse`** – a TCP listener. In `local` mode it is bound to `127.0.0.1` only. Windows
  Firewall may still show its "allow access" dialog the first time Python listens; **deny it for local use**
  (loopback traffic is exempt from firewall rules, so the server keeps working).

Check and change the policy at any time:

```bash
mcp-adapter --print-policy       # effective policy as JSON
mcp-adapter-setup --show         # current .env answers
mcp-adapter-setup                # interactive: re-answer the questions
mcp-adapter-setup --non-interactive --mode local --allow-internet no
```

Or edit the keys in `.env` directly and restart the server. Agents can call the `security_policy` tool to see
which mode is active and why some tools are missing.

Note that the Claude desktop app and other MCP clients have their own local helper listeners and may trigger
their own firewall prompts; those are unrelated to this server and can equally be denied for local use.

### What an MCP client can do with this server

Treat every connected client (and the model behind it) as someone sitting at your keyboard:

* The automation tools run the installed applications with **your user rights**, and several of them execute code
  by design (`matlab_run_code`, `mathematica_evaluate`, `comsol_run_python`, `vivado_run_tcl`, `autocad_run_lisp`,
  `hfss_run_script`, `altium_run_delphiscript`, `photoshop_run_jsx`, ...). Connect only clients you trust, and
  switch off the applications you do not need (`MCP_ADAPTER_ENABLE_<APP>=false` or the Applications page).
* `workspace_run` (only registered when you switch on full folder access for a task) runs shell commands with the
  task folder as working directory; it is not a sandbox.
* The output-folder checks (`set_output_folder`) prevent mistakes such as writing into system folders or network
  shares; they do not confine the code-running tools above.
* API keys and the auth token live only in `.env`, which is git-ignored and readable only by your account; the UI
  accepts them write-only. The MCP server keeps them out of its environment, so the applications and shells it
  starts never see them; tool paths are not expanded (`%VAR%`), so a path cannot echo a key back; and the agent
  runner removes any key value from transcripts, chat history and reports.
* Tool paths may not point to network locations (UNC paths, mapped network drives): opening one would make
  Windows connect to another machine and possibly send your credentials.
* `OPENAI_API_KEY` is only sent to `api.openai.com`. A custom OpenAI-compatible endpoint (`OPENAI_BASE_URL`) gets
  `OPENAI_COMPAT_API_KEY` instead and must use HTTPS unless it runs on this computer.
* The agent is told to treat file contents, tool results and web pages as data, never as instructions, and a
  model can only call the tools it was given.
* Programs are never started from the current folder, and Program Files is searched before drive roots, so a
  planted `matlab.bat` cannot hijack a tool; on shared computers set the `<APP>_EXE` paths explicitly.
* The workspace UI binds `127.0.0.1` only and needs a per-session token on every API call, so other machines and
  other web pages cannot use it.

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## Where results are written

When you give a task a folder, the adapter writes its files into that folder, not into its own `outputs` folder.

* **Claude Desktop and other long-running clients:** the server starts once, before any task exists. The server
  instructions therefore tell the agent to call `set_output_folder("D:\\Projects\\PCB")` first whenever the user gives
  or grants a folder. From then on every generated script, netlist, log and result goes there, and relative paths
  in tool arguments are resolved inside it. Each application uses its own subfolder, for example
  `D:\Projects\PCB\orcad`, unless `per_application_subfolders` is false. An empty folder goes back to the default.
* **Tasks started from the workspace UI or `mcp-adapter-agent --work-dir`:** the server is started for that task
  with the folder as its output folder, so nothing needs to be called.
* **No folder given:** files go to `MCP_ADAPTER_OUTPUT_DIR` (default `outputs/` in the repository).

The folder is checked in code before it is used:

* it must be an absolute path to an existing, writable folder on a local disk; the adapter never creates it;
* drive roots, the home folder itself, hidden folders, Windows/program/ProgramData/AppData folders and the
  adapter's own source folder are refused;
* network locations (UNC paths and mapped network drives) are refused, so results stay on this machine;
* `MCP_ADAPTER_OUTPUT_ROOTS` in `.env` (folders separated by `;`) restricts the choice to those folders;
* a server started for one task working directory accepts only folders inside it.

These checks prevent mistakes; they are not a sandbox, because the scripting tools (MATLAB, Mathematica, COMSOL
Java, Tcl, DelphiScript) run code with your user rights anyway. The setting belongs to the server process, so in
Claude Desktop it applies to every chat until it is changed, reset or the app restarts. `adapter_status` and
`security_policy` show the current folder and its source.

## Workspace UI (chats, settings, applications)

```bash
python -m mcp_adapter.ui.server        # or: mcp-adapter-ui
```

This starts a tiny local web server and opens the page in your browser with a private link of the form
`http://127.0.0.1:8765/#t=<session key>` (`--no-browser` only prints that link, `--port 9000` changes the port).
The key after `#t=` is new for every start and is needed to use the page, so another program or another user on
the same computer cannot drive the UI; the part after `#` never leaves the browser. It runs until Ctrl+C; all
settings live in `.env` and chats are stored as JSON under `outputs/chats/`, so nothing is lost when it stops.
`.env` and the chat folder are made readable only by your account.

The page has three tabs:

**Chat** - a Claude-desktop-like workspace, no other client needed:

* a sidebar with your chats; **+ New chat** creates another one. Chats run **in parallel**, each with its own
  agent run and its own MCP server process, and each keeps its conversation history (later messages see the
  earlier turns).
* the conversation in the middle: your messages, the agent's answers (with "view steps" to open the run's
  report), and, while it works, every tool call and result live. Approve / Deny buttons appear when the chat
  has "Approve calls" switched on.
* the message box at the bottom with small option chips: **Model** (one dropdown with the models of every
  provider connected in Settings), **Effort**, the **working folder** (type a path or **Browse...** for the
  native folder dialog), **Folder access**, **Approve calls**, **Reasoning** and **Max turns**. Chip changes are
  saved to the chat immediately and become the defaults for new chats.
* **Folder access** gives the agent the `workspace_*` tools for that folder: list, read, write, move, delete
  and search, whose paths cannot leave the folder, plus `workspace_run`, which runs shell commands with the folder
  as working directory. `workspace_run` is a full shell, not a sandbox, so it **always asks for approval**, even
  when "Approve calls" is off. Keep folder access off for untrusted tasks. A drive root or your home folder is
  never given full access. All files the tools generate, and the run reports (`agent-reports/`), land in that
  folder.

**Settings**:

* **Model providers** - Anthropic and OpenAI keys (write-only), a light per provider: green = key stored and
  the provider answered, orange = key stored but not verified/unreachable, red = no key; **Test** re-checks and
  refreshes the live model list. Models of every provider with a key appear in the chat's model dropdown.
* **MCP server for other LLM apps** - use these applications from Claude Desktop, Claude Code, Cursor, VS Code
  and similar: ready-to-copy config for stdio clients (they start the server themselves) and a **Start
  endpoint** button that runs the server as a local HTTP MCP endpoint (`http://127.0.0.1:8766/mcp`, loopback only,
  optional autostart with the UI) for clients that connect to a URL. The endpoint requires the bearer token
  `MCP_ADAPTER_AUTH_TOKEN`, which the UI creates in `.env` on first start; clients send
  `Authorization: Bearer <token>` (the config snippets show where). An optional default working folder plus
  access switch applies to those external apps. ChatGPT's connectors need a server on the public internet, so a
  localhost endpoint is not visible to them without a tunnel, which is outside the local-only policy here.
* **Security & network policy**, adapter mode, timeout and output folder, as described above.

**Applications** - the grid with an on/off switch, real icon (read from the installed executable on
Windows), detection status and executable path fields per application; **Re-detect** and **Save** live in the
header.

The UI is hardened like the server: it binds **127.0.0.1 only**; every API call carries the per-session key,
which reaches the browser only through the launch link and is never embedded in the page; `Host` must be
localhost and `Origin` must be the UI's own address; cross-origin preflights are refused; a strict content
security policy with a per-response nonce blocks injected scripts; and secrets are never echoed back. Approvals
are bound to the exact pending tool call. Restart the UI server after pulling Python changes; page changes show
up on reload.

## Adapter behaviour: only installed software is exposed

Most users have a few of the fourteen applications, not all of them. The server therefore works as an adapter:

* At start-up it looks for each application's executable (`PATH`, the usual vendor folders, or the
  `<SOFTWARE>_EXE` variable in `.env`).
* **Automation tools are registered only for applications that were found.** An agent that lists the tools sees
  `matlab_*` only if MATLAB exists on that machine; it never sees tools it cannot use and never has to discover
  a missing program by trial and error.
* **Tools that need no executable are always present**: `drawio_create_diagram`, `drawio_flowchart`,
  `drawio_read`, `drawio_codec`, `orcad_parse_pspice_output`, `proteus_project_info`, `altium_script_template`,
  `feko_parse_out`, `feko_lua_template`, `eagle_read_design`, `eagle_bom`, `eagle_netlist`.
* **The catalog is always available for all fourteen applications**, because knowing how a tool works is useful
  even when the software runs on another machine.
* `list_software` shows, per application, whether it is installed and which automation tools are available or
  hidden; `adapter_status` adds the reason for every hidden tool (which variable to set). The server
  instructions tell agents to consult these first.
* Installed something later? Set `<SOFTWARE>_EXE` if needed and restart the server; detection happens at start-up.
* `MCP_ADAPTER_EXPOSE_UNAVAILABLE=true` in `.env` registers every tool regardless (useful for demos and tests);
  calls for missing software then return `ok: false` with the same hint instead of failing.

Optional extras:

* `pip install pywin32` – synchronous Photoshop/AutoCAD COM automation on Windows
* `pip install mph` – COMSOL Python scripting (`comsol_run_python`, `comsol_model_summary`)
* `pip install pyaedt` – HFSS through PyAEDT (`hfss_run_pyaedt`)
* `pip install truststore` – use the OS certificate store for Tavily calls behind corporate proxies

## Which application for which task

Agents reach for the general tool (MATLAB) unless the server tells them what each application is built for.
The MCP layer therefore carries this knowledge in five places:

* every automation tool description starts with a domain tag, e.g.
  `[Ansys HFSS: 3D electromagnetic field simulation (antennas, RF/microwave, signal integrity)] ...`;
* the server instructions contain the routing guide below, and the bundled agent's system prompt repeats it;
* `list_software` and `software_overview` return `best_for`, `typical_tasks`, `not_ideal_for` and `prefer_over`
  per application; `software_overview` also lists the application's capability areas (its catalog categories with
  entry counts), so an agent sees everything the program can do before choosing tools;
* `recommend_application(task)` ranks the applications for a task description, says which of them are installed
  on this machine and which tool prefix to use;
* `compare_applications(task)` compares the applications that can do the same kind of task, head to head
  (see [Comparing applications for the same task](#comparing-applications-for-the-same-task)).

| Task | Use | Tools |
|---|---|---|
| Antennas, RF/microwave parts, S-parameters, radiation patterns, signal integrity, unit cells and arrays, PCB/package extraction | HFSS | `hfss_*` |
| Antenna placement on vehicles/aircraft/ships, electrically large RCS, EMC with cable harnesses, characteristic modes, FSS, windscreen antennas, radio coverage (WinProp) | Feko | `feko_*` |
| Coupled physics on a geometry: heat, structural, fluid/CFD, electrostatics/magnetics, acoustics, electrochemistry and batteries, plasma, MEMS, topology optimization | COMSOL | `comsol_*` |
| Circuit simulation with real components (transient, AC/DC sweeps, noise, Monte Carlo, Smoke), Probe measurements | PSpice | `orcad_pspice_*` |
| PCB layout, footprints, design rules and queries, Gerbers/ODB++, ActiveBOM, Draftsman, multi-board and harness | Altium | `altium_*` |
| Existing EAGLE designs: parts, nets, BOM, netlist, Gerber/Excellon/assembly output with CAM jobs (EAGLE is retired; no new layout work) | EAGLE | `eagle_*` |
| Firmware running together with its circuit (Arduino/PIC/AVR/ARM/Pico), virtual instruments, IoT Builder | Proteus | `proteus_*` |
| FPGA: Verilog/VHDL synthesis, XDC timing, bitstreams, IP block designs, HLS, DFX, boot images | Vivado | `vivado_*` |
| Block-diagram dynamic systems, control loops, Stateflow, Simscape physical models, verification, code generation | Simulink | `simulink_*` |
| Exact/symbolic mathematics, closed forms, special functions, graph theory, curated data, uncertainty | Mathematica | `mathematica_*` |
| Numerics, signal/image/audio processing, filter and control design, ML/DL/RL, comms waveforms and BER, sensor fusion, data analysis, plots | MATLAB | `matlab_*` |
| DWG/DXF technical drawings, blocks and attributes, sheet sets, 3D solids, AutoLISP/.NET automation | AutoCAD | `autocad_*` |
| Raster photos, retouching, Camera Raw, generative fill, batch export, UXP/ExtendScript automation | Photoshop | `photoshop_*` |
| Flowcharts, block, UML/ER/BPMN and cloud architecture diagrams, CSV-driven diagrams | draw.io | `drawio_*` |

Rule given to agents: prefer the specialised application whenever one fits and is installed; MATLAB is for
numerics and post-processing around it. These are suggestions, not requirements: when the first choice is not
installed, or the user asks for a different application, the agent uses an installed alternative and says what it
gives up, instead of substituting silently. The profiles and keyword weights live in `mcp_adapter/domains.py`.

### Comparing applications for the same task

Many tasks can be done by more than one application, and some do them better. `mcp_adapter/comparisons.py`
holds a head-to-head comparison for 27 such task areas. Each area sums up in one line which
application is best for which part of the job, and for every candidate it states its strength, when
to choose it and its limits, ranked from the usual first choice.

The comparison is **advisory**:

* the suggested pick is the first **installed** candidate, so a missing application never blocks the work;
* when that is not the first choice, the advice names the first choice and the limits of the installed
  alternative, so the agent can tell the user what the substitution gives up;
* an installed application that the task names itself ("in Simulink", "with PSpice") wins over the ranking;
* candidates marked `*` below cover a neighbouring job only; they are listed for context and never offered as a
  substitute (Photoshop is not suggested for filter design, for example);
* if no suitable candidate is installed, the advice says so and names the first choice.

`recommend_application(task)` attaches the matching areas as `comparison` and lists installed `alternatives`.
`compare_applications()` lists all areas, `compare_applications(area="cfd")` shows one area, and
`compare_applications(task="...")` matches a description.

| Task area | Candidates, usual first choice first | Which is best for what |
|---|---|---|
| Antenna design (single antennas and arrays) (`antenna_design`) | HFSS → Feko → COMSOL → MATLAB | HFSS for sign-off accuracy on detailed antennas with dielectrics and feeds; Feko for wire and metallic antennas and large arrays; COMSOL when heat or deformation detune the antenna; MATLAB for early sizing, catalogue antennas and array/beam studies. |
| Antenna placement, co-site coupling and radar cross section of large platforms (`antenna_placement_rcs`) | Feko → HFSS → MATLAB → COMSOL | Feko for whole-platform placement, co-site coupling and RCS of large targets; HFSS SBR+ when the antenna already lives in AEDT; MATLAB for moderate platforms and radar-level studies; COMSOL only for electrically small platforms. |
| RF/microwave passive components and PCB/package signal integrity (`rf_passives_si`) | HFSS → COMSOL → Feko → MATLAB → Altium* | HFSS for 3D interconnects, connectors, packages, filters and couplers; COMSOL when heating or deformation couple in; Feko for planar metallic structures; MATLAB for cascades, matching and Touchstone post-processing; Altium only screens SI while routing. |
| Analog and mixed-signal circuit simulation (`circuit_simulation`) | OrCAD/PSpice → Altium → Proteus → Simulink → EAGLE* → COMSOL | PSpice for component-level accuracy with vendor models, Monte Carlo, worst case and stress; Altium for quick checks inside an Altium design; Proteus when a microcontroller runs real firmware; Simulink for circuits inside a larger system; EAGLE's ngspice only for basic OP/DC/AC/transient checks of an EAGLE schematic. |
| Power electronics converters and motor drives (`power_electronics`) | OrCAD/PSpice → Simulink → Proteus → MATLAB → COMSOL* | PSpice for switching waveforms, losses and stress with real devices; Simulink for control loops, drives and code generation; Proteus for MCU control code on a simple power stage; MATLAB for analytical converter models; COMSOL for the magnetics and thermal design of the parts. |
| Schematic capture, part numbers and BOM (`schematic_capture`) | Altium → OrCAD/PSpice → Proteus → EAGLE* → draw.io* | Altium for managed parts with live supply-chain data and one data model shared with the PCB; OrCAD Capture CIS for database-driven part numbers and PSpice/Allegro flows; Proteus for microcontroller projects; EAGLE was the lightweight option whose library parts carry manufacturer part numbers (retired: now for existing files and BOM extraction). |
| PCB design, layout and manufacturing outputs (`pcb_layout`) | Altium → OrCAD/PSpice → Proteus → EAGLE* → AutoCAD* | Altium for professional multilayer, high-speed and rigid-flex boards with the strongest interactive routing and rule system; OrCAD/Allegro for Cadence flows; Proteus for simpler microcontroller boards; EAGLE was light and quick for 2-4-layer and open-hardware boards (retired: now for existing designs and CAM output). |
| 3D PCB models, realistic renders and ECAD-MCAD exchange (`pcb_3d_mcad`) | Altium → OrCAD/PSpice → AutoCAD* → EAGLE* → COMSOL* | Altium for realistic 3D, STEP models on footprints, collision checks and MCAD exchange; OrCAD/Allegro for Cadence boards; AutoCAD for the enclosure around an exported board STEP; COMSOL for thermal or structural physics on the assembly. |
| Microcontroller firmware development and testing (`embedded_firmware`) | Proteus → Simulink → MATLAB → Vivado* | Proteus to run and debug real firmware against the simulated circuit; Simulink with Embedded Coder for model-based production code; MATLAB Coder for C code from algorithms; Vivado only for processors inside AMD FPGAs and SoCs. |
| FPGA and digital hardware design (`fpga_hardware`) | Vivado → Simulink → MATLAB | Vivado is required to implement and program AMD FPGAs; Simulink or MATLAB HDL Coder generate the HDL from models or algorithms first. |
| Control design and dynamic system simulation (`control_dynamic_systems`) | Simulink → MATLAB → Mathematica → COMSOL* → OrCAD/PSpice* | Simulink for nonlinear closed-loop simulation, logic and deployment; MATLAB for linear analysis and controller synthesis; Mathematica for closed-form, parameter-dependent stability results. |
| Differential equations and general numerical computing (`differential_equations`) | MATLAB → Mathematica → COMSOL → Simulink | MATLAB for numerical ODE/DAE work, optimisation and data; Mathematica for exact or high-precision solutions; COMSOL for PDEs on real geometry; Simulink for ODE systems built as block diagrams. |
| Symbolic and exact mathematics (`symbolic_math`) | Mathematica → MATLAB | Mathematica for any serious symbolic or exact work; MATLAB's Symbolic Math Toolbox for moderate steps inside MATLAB code. |
| Data analysis, statistics and machine learning (`data_analysis_ml`) | MATLAB → Mathematica | MATLAB for engineering data, domain toolboxes and deployment; Mathematica for exploratory and symbolic statistics. |
| Signal and scientific image processing (`signal_image_processing`) | MATLAB → Mathematica → Photoshop* | MATLAB for quantitative, reproducible signal and image processing; Mathematica for exploratory or symbolic analysis; Photoshop only when visual appearance is the goal. |
| Photo editing and graphic assets (`photo_editing`) | Photoshop → MATLAB → Mathematica | Photoshop for anything visual; MATLAB or Mathematica only for algorithmic batch operations. |
| Structural analysis (stress, vibration, buckling, fatigue) (`structural_fem`) | COMSOL → MATLAB → Simulink* | COMSOL for structural FEM with nonlinear materials, contact and fatigue; MATLAB's PDE Toolbox for simple linear problems; Simulink Multibody for mechanism motion, not stress. |
| Thermal analysis and electronics cooling (`thermal`) | COMSOL → Simulink → MATLAB | COMSOL for temperature fields on geometry and electronics cooling; Simulink for lumped, system-level thermal behaviour; MATLAB for simple conduction problems and fits. |
| Fluid flow and CFD (`cfd`) | COMSOL → Simulink → MATLAB* | COMSOL for 2D/3D flow fields and flow coupled to heat or structures; Simulink (Simscape Fluids) for system-level hydraulics. |
| Low-frequency electromagnetics: motors, transformers, inductors, electrostatics (`low_frequency_em`) | COMSOL → Simulink → MATLAB → OrCAD/PSpice* | COMSOL for motors, transformers, inductors, busbars and electrostatics from their geometry; Simulink for drives with known machine parameters; MATLAB for simple 2D fields and parameter fits. |
| Acoustics and vibro-acoustics (`acoustics`) | COMSOL → MATLAB → Simulink | COMSOL for sound fields, transducers and vibro-acoustics on geometry; MATLAB for analytical estimates and audio signal analysis; Simulink for lumped transducer models. |
| Coupled multiphysics (Joule heating, thermal stress, FSI, piezoelectric, electrochemical-thermal) (`coupled_multiphysics`) | COMSOL → MATLAB → HFSS | COMSOL for strongly coupled physics in one model; MATLAB to orchestrate weak or sequential couplings; HFSS only for RF losses handed to Ansys thermal tools. |
| Chemical reactors, electrochemistry and batteries (`chemistry_batteries`) | COMSOL → Simulink → MATLAB → Mathematica | COMSOL for cell, corrosion, fuel-cell and reactor physics; Simulink for pack-level and BMS simulation; MATLAB for fitting models to test data; Mathematica for analytical kinetics. |
| Optics and photonics (`optics`) | COMSOL → HFSS → MATLAB | COMSOL for wave and ray optics and thermal lensing; HFSS for small metasurfaces and nanostructures inside AEDT; MATLAB for simple propagation models. |
| EMC/EMI analysis (`emc_emi`) | Feko → HFSS → COMSOL → OrCAD/PSpice → Altium* | Feko for vehicle and aircraft EMC and cable harnesses; HFSS for board, package and enclosure EMI; COMSOL for component shielding; PSpice for conducted emissions of power supplies. |
| Technical drawings and 2D/3D CAD geometry (`technical_drawings`) | AutoCAD → draw.io → COMSOL* → Altium* | AutoCAD for scaled, dimensioned drawings and DWG/DXF exchange; draw.io for quick plans and sketches. |
| Diagrams for documentation (flowcharts, architecture, block diagrams) (`diagrams`) | draw.io → Simulink → Mathematica → AutoCAD | draw.io for documentation diagrams; Simulink when the diagram must simulate; Mathematica for graphs computed from data; AutoCAD when exact geometry matters. |

Example with OrCAD not installed but Simulink and MATLAB installed, for "simulate a buck converter":

```text
First choice would be orcad (not installed). Suggested installed alternative: simulink. Control-loop design,
drive-level and grid-level behaviour, embedded code for the controller. Tell the user its limits: Device-level
waveforms are less detailed than PSpice with vendor models. Tools: simulink_*.
```

## Configuration

Copy [`.env.example`](.env.example) to `.env`. Everything is optional:

| Variable | Purpose |
|---|---|
| `MCP_ADAPTER_NETWORK_MODE`, `MCP_ADAPTER_ALLOW_INTERNET`, `MCP_ADAPTER_ALLOWED_HOSTS` | security policy, see [Security and network policy](#security-and-network-policy); set by `mcp-adapter-setup` |
| `MCP_ADAPTER_EXPOSE_UNAVAILABLE` | `false` (default): register automation tools only for detected applications; `true`: register all, see [Adapter behaviour](#adapter-behaviour-only-installed-software-is-exposed) |
| `TAVILY_API` | key for `search_docs_online` (live web search of vendor documentation); only used when `MCP_ADAPTER_ALLOW_INTERNET=true` |
| `MATLAB_EXE`, `WOLFRAMSCRIPT_EXE`, `COMSOL_EXE`, `PHOTOSHOP_EXE`, `ORCAD_CAPTURE_EXE`, `PSPICE_EXE`, `ALTIUM_EXE`, `PROTEUS_EXE`, `VIVADO_EXE`, `VITIS_HLS_EXE`, `AUTOCAD_CORE_CONSOLE_EXE`, `ANSYS_EDT_EXE`, `FEKO_EXE`, `EAGLE_EXE`, `DRAWIO_EXE` | explicit executable paths; when unset the adapter looks on `PATH` and in the usual vendor install folders (newest version wins) |
| `MCP_ADAPTER_TIMEOUT` | default timeout in seconds for launching an application (600) |
| `MCP_ADAPTER_OUTPUT_DIR` | where generated scripts, logs and results are written (`./outputs`) |
| `PSPICE_ARGS`, `ORCAD_CAPTURE_TCL_CMD`, `ALTIUM_SCRIPT_ARGS`, `PHOTOSHOP_APP_NAME` | release-specific command-line overrides (see the adapter docstrings) |

Call the `adapter_status` tool to see what was detected.

## Running the server

There are two things you can run. Neither is running by itself after installation.

**1. The MCP server** is normally started *by your MCP client* (Claude Desktop, Claude Code, your own agent)
as a child process that talks over stdio, so you usually never start it by hand: you register the command in the
client (next section) and the client launches it when it connects. To run it manually, e.g. for an HTTP client:

```bash
python -m mcp_adapter.server                                            # stdio (default): opens no port
python -m mcp_adapter.server --transport streamable-http --port 8000    # listens on 127.0.0.1 only in local mode
python -m mcp_adapter.server --transport sse --port 8000
python -m mcp_adapter.server --transport streamable-http --host 0.0.0.0 # refused unless MCP_ADAPTER_NETWORK_MODE=network
python -m mcp_adapter.server --print-policy                             # show the effective security policy
```

The server prints its effective exposure to stderr at start-up, e.g. `listening on 127.0.0.1:8000 - LOOPBACK ONLY`.

**2. The configuration UI** is a small local web page you start when you want to change settings and stop
with Ctrl+C when done (see [Configuration UI](#configuration-ui)):

```bash
python -m mcp_adapter.ui.server
```

On Windows use `py` instead of `python` if the plain command is not on your PATH. The short commands
`mcp-adapter`, `mcp-adapter-setup` and `mcp-adapter-ui` are installed too, but they only work when Python's
`Scripts` folder is on your PATH; the `python -m ...` forms always work.

### Claude Desktop

Add to `claude_desktop_config.json` (see [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)):

```json
{
  "mcpServers": {
    "mcp-adapter": {
      "command": "python",
      "args": ["-m", "mcp_adapter.server"],
      "cwd": "C:/path/to/MCP-ADAPTER-PV"
    }
  }
}
```

### Claude Code

```bash
claude mcp add mcp-adapter -- python -m mcp_adapter.server
```

(On Windows without `python` on PATH: `claude mcp add mcp-adapter -- py -m mcp_adapter.server`.)

### Any MCP client (Python)

[`examples/client_demo.py`](examples/client_demo.py) launches the server over stdio, lists the tools and calls a few.

## Tools

### Catalog

| Tool | What it does |
|---|---|
| `list_software` | supported applications, catalog sizes, whether the executable was found |
| `list_categories(software)` | catalog categories with counts |
| `list_tools(software, category?, kind?, limit, offset)` | compact rows of catalog entries |
| `describe_tool(software, name)` | full entry as Markdown: description, usage, parameters, example, notes, docs |
| `search_tools(query, software?, kind?, category?)` | ranked keyword search across one or all catalogs |
| `software_overview(software)` | product summary + automation entry points (CLI flags, APIs, file formats) |
| `recommend_application(task)` | suggests the application for a task (what each is best for, installed here or not, tool prefix) with the matching comparison and installed alternatives |
| `compare_applications(task?, area?)` | head-to-head comparison of applications that can do the same kind of task: which is best for what, strength, when to choose, limits; advisory, falls back to installed candidates |
| `search_docs_online(query, software?)` | live documentation search restricted to vendor sites (Tavily); **only registered when `MCP_ADAPTER_ALLOW_INTERNET=true`** |
| `adapter_status` | detected executables, env overrides, the active security policy and the current output folder |
| `set_output_folder(folder, per_application_subfolders?)` | writes all generated files and results into the task's folder (checked: existing, local, not a system/hidden/network folder); empty = back to the default |
| `security_policy` | network mode, bind restriction, which internet tools are disabled and how to change it |

Resources: `catalog://software`, `catalog://{software}/categories`, `catalog://{software}/{name}`.

### Time

`time_now`, `time_convert`, `time_add`, `time_difference`, `time_format`, `time_list_timezones`,
`time_world_clock`, `time_unix`. Inputs accept ISO 8601, unix seconds/milliseconds, `now/today/tomorrow`
and common formats; zones accept IANA names, `local`, abbreviations (`EST`, `CET`, `IST`) and offsets (`+03:30`).

### Automation (per application)

| Family | Tools |
|---|---|
| MATLAB | `matlab_run_code`, `matlab_run_file`, `matlab_call_function`, `matlab_eval`, `matlab_plot_to_file`, `matlab_version`, `matlab_installed_toolboxes` |
| Simulink | `simulink_simulate`, `simulink_model_info`, `simulink_list_blocks`, `simulink_get_block_parameters`, `simulink_set_block_parameters`, `simulink_build_model`, `simulink_export_diagram` |
| Mathematica | `mathematica_evaluate`, `mathematica_run_file`, `mathematica_run_script`, `mathematica_export`, `mathematica_wolfram_alpha`, `mathematica_version` |
| COMSOL | `comsol_list_modules`, `comsol_search_examples`, `comsol_example_info`, `comsol_run_example`, `comsol_build_from_java`, `comsol_inspect_model`, `comsol_evaluate`, `comsol_run_batch`, `comsol_run_method`, `comsol_compile_java`, `comsol_run_python`, `comsol_model_summary` |
| Photoshop | `photoshop_run_jsx`, `photoshop_run_action`, `photoshop_document_info`, `photoshop_batch_process`, `photoshop_export_layers` |
| OrCAD | `orcad_pspice_simulate`, `orcad_pspice_simulate_netlist`, `orcad_parse_pspice_output`, `orcad_capture_open`, `orcad_capture_run_tcl` |
| Altium | `altium_open`, `altium_run_script_project`, `altium_run_delphiscript`, `altium_script_template` |
| Proteus | `proteus_open_project`, `proteus_run_cli`, `proteus_project_info` |
| Vivado | `vivado_run_tcl`, `vivado_project_info`, `vivado_build_project`, `vivado_reports`, `vivado_program_device`, `vivado_create_project`, `vivado_run_hls` |
| AutoCAD | `autocad_run_script`, `autocad_run_lisp`, `autocad_drawing_info`, `autocad_export_dxf`, `autocad_plot_to_pdf`, `autocad_batch`, `autocad_send_command` |
| HFSS | `hfss_run_script`, `hfss_batch_solve`, `hfss_project_info`, `hfss_set_variables_and_solve`, `hfss_export_touchstone`, `hfss_export_report_csv`, `hfss_run_pyaedt` |
| Feko | `feko_version`, `feko_solve`, `feko_batch_process`, `feko_run_cadfeko_script`, `feko_run_postfeko_script`, `feko_parse_out`, `feko_lua_template` |
| EAGLE | `eagle_version`, `eagle_cam_jobs`, `eagle_cam_job`, `eagle_cam_output`, `eagle_run_commands`, `eagle_read_design`, `eagle_bom`, `eagle_netlist` |
| draw.io | `drawio_create_diagram`, `drawio_flowchart`, `drawio_export`, `drawio_read`, `drawio_codec` |

`mathematica_wolfram_alpha` is the only automation tool that reaches the internet (through your Mathematica
installation); like `search_docs_online` it exists only when `MCP_ADAPTER_ALLOW_INTERNET=true`.

Every automation tool returns the same shape:

```json
{"ok": true, "software": "matlab", "command": "...", "returncode": 0, "duration_s": 6.9,
 "stdout": "...", "stderr": "", "artifacts": {"script": "outputs/matlab/mcp_run_....m"}, "data": {"x": [1, 2, 3]}}
```

`data` carries structured results (JSON captured from MATLAB/Vivado/Photoshop/HFSS scripts, parsed PSpice
output, diagram statistics, ...). When the executable is missing, `ok` is `false` and `error` names the
environment variable to set.

### Examples

```text
search_tools(query="butterworth filter design", software="matlab")
describe_tool(software="mathematica", name="NDSolve")
matlab_eval(expression="roots([1 -3 2])")                  -> data.mcp_value__ == [2, 1]
simulink_simulate(model="vdp", stop_time="20")             -> logged signals summary + .mat file
vivado_build_project(project_file="C:/fpga/top.xpr")        -> WNS/TNS and bitstream path
drawio_flowchart(steps=["Load", "Valid?", "Save"], export_format="png")
hfss_export_touchstone(project="ant.aedt", design="patch", setup="Setup1", sweep="Sweep", output_file="ant.s1p")
time_convert(datetime="2026-09-19 14:30", from_timezone="Europe/Berlin", to_timezone="Asia/Tehran")
```

## Notes on vendor entry points

* **OrCAD Capture** executes Tcl from its Command Window; a start-up switch differs between releases, so the
  adapter writes the script and returns the `source` command unless `ORCAD_CAPTURE_TCL_CMD` is configured.
* **Altium Designer** is driven with `X2.EXE -RScriptFile:<PrjScr> -RProcName:<Unit>Proc>`; override the switch
  template with `ALTIUM_SCRIPT_ARGS` if your version expects different names.
* **Proteus** has no documented headless simulation switch; `proteus_run_cli` passes arbitrary switches through.
* **Feko** (verified with 2026.1): `runfeko MODEL [-np N] [--use-gpu] [--priority x]` solves; `cadfeko_batch MODEL.cfx
  -# VAR=VALUE --force-mesh` changes variables and re-meshes; Lua scripts run with `cadfeko` / `postfeko [MODEL]
  --non-interactive --run-script FILE [--configure-script "..."]`. `runfeko` has no `--version` switch (the adapter
  parses its banner). The Lua templates follow the scripting reference; confirm method names with the CADFEKO recorder.
* **EAGLE** (verified with 9.6.0 in September 2026): Autodesk retired EAGLE on 7 June 2026 and shut down its licensing
  servers. The command-line CAM Processor still runs without signing in: `eaglecon -X -N -dCAMJOB -j<job.cam>
  -o<dir> board.brd` writes Gerber files, the Gerber job file, Excellon drill and assembly data (the adapter picks the
  shipped `example_N_layer.cam` job from the board's layer setup when none is given), and a legacy device plus a
  layer list (`-dEXCELLON ... 44 45`) writes a single file. The editor route (`eagle -C "RUN x.ulp; QUIT"`) now stops
  at the Autodesk 'Sign in' window; the adapter detects that, closes the process and says so, and never signs in.
  `.sch`/`.brd`/`.lbr` files are XML (`doc/eagle.dtd`), so `eagle_read_design`, `eagle_bom` and `eagle_netlist`
  work without EAGLE; binary files from before EAGLE 6.0 are only read by the CAM Processor.
* **COMSOL** (verified with 6.4): `comsolbatch -inputfile model.mph|Model.class -outputfile out.mph -batchlog log`
  solves a model or runs a compiled Java model program; a model built by a class is saved as `out_<ModelTag>.mph`,
  which the adapter reports. `comsolcompile` exits with code 0 even when compilation fails, so the adapter checks its
  message and the `.class` file. `comsol_inspect_model` and `comsol_evaluate` compile small Java programs, so reading
  results needs no Python bridge (MPh stays optional). The example tools index the local Application Library
  (`applications/`) and its documentation (`doc/help/.../com.comsol.help.models.*`), including the Java script that
  builds each example; `scripts/build_comsol_examples.py` regenerates the example catalog from an installation.
* **Photoshop** must be running (or startable) on the same desktop session; COM (pywin32) gives synchronous
  execution, otherwise the adapter launches the JSX and polls for the result file.

## Development

```bash
pytest -q                      # unit tests (live MATLAB test runs only when MATLAB is installed)
ruff check mcp_adapter tests scripts
python scripts/catalog_stats.py
python scripts/tavily_search.py "Vivado report_timing_summary options"
```

Project layout:

```
mcp_adapter/
  server.py          MCP tool/resource registration and CLI entry point
  catalog.py         catalog loader, alias resolution, ranked search
  time_tools.py      timezone-aware time utilities
  docs_search.py     Tavily-backed live documentation search
  config.py          .env loading, executable discovery, timeouts
  adapters/          one module per application (base.py holds the subprocess runner)
  catalogs/<id>/     meta.json + NN_<category>.json data files
tests/               pytest suite (catalog schema validation, time tools, adapters, server)
examples/            client demo and Claude Desktop configuration
scripts/             Tavily helper and catalog statistics
```

To add or extend a catalog, drop a new `NN_<slug>.json` file into the software folder (≤ 40 tools per file,
fields per [CATALOG_SCHEMA.md](CATALOG_SCHEMA.md)) and run `pytest tests/test_catalog.py`.

## License

MIT
