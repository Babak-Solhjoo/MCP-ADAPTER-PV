"""MCP Adapter server.

Run with ``mcp-adapter`` (stdio) or ``python -m mcp_adapter.server --transport streamable-http --port 8000``.

Tool families:
  * catalog_*  / list_* / describe_tool / search_tools – browse the built-in knowledge base of what each
    application can do (MATLAB, Mathematica, COMSOL, Photoshop, OrCAD, Altium, Proteus, Vivado, AutoCAD,
    HFSS, Simulink, draw.io);
  * time_*     – timezone-aware clock and date arithmetic;
  * <software>_* – drive the real application headless when it is installed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, docs_search, time_tools
from . import output_folder as outfolder
from ._compat import create_server, run_server
from .adapters.altium import altium_adapter
from .adapters.autocad import autocad_adapter
from .adapters.comsol import comsol_adapter
from .adapters.comsol_library import find_example
from .adapters.comsol_library import search as search_examples
from .adapters.drawio import decode_diagram, drawio_adapter, encode_diagram
from .adapters.eagle import bom as eagle_bom_data
from .adapters.eagle import eagle_adapter
from .adapters.eagle import netlist as eagle_netlist_data
from .adapters.eagle import read_design as eagle_read
from .adapters.feko import feko_adapter
from .adapters.feko import lua_template as feko_template_text
from .adapters.hfss import hfss_adapter
from .adapters.mathematica import mathematica_adapter
from .adapters.matlab import matlab_adapter
from .adapters.orcad import capture_adapter, parse_pspice_output, pspice_adapter
from .adapters.photoshop import photoshop_adapter
from .adapters.proteus import proteus_adapter
from .adapters.simulink import simulink_adapter
from .adapters.vivado import vivado_adapter
from .catalog import KNOWN_SOFTWARE, get_catalog, resolve_software
from .comparisons import SUGGESTION_NOTE, annotate, area_list, compare, find_area, named_apps
from .config import app_enabled, enable_var, expose_unavailable, output_dir
from .domains import ROUTING_GUIDE, TOOL_PREFIX, profile, recommend, tag
from .security import SecurityError, SecurityPolicy
from .workspace import register_workspace_tools, workspace_enabled, workspace_root

INSTRUCTIONS = """MCP Adapter exposes two things for fourteen engineering/design applications:
1. A searchable catalog of each application's own tools/commands/functions (use search_tools,
   list_categories, list_tools, describe_tool). The catalog is always available, even for software that is
   not installed here. Start with list_software to see what is covered and what is installed.
2. Headless automation of INSTALLED applications (matlab_*, simulink_*, mathematica_*, comsol_*,
   photoshop_*, orcad_*, altium_*, proteus_*, vivado_*, autocad_*, hfss_*, feko_*, eagle_*, drawio_*). This server works as an
   adapter: automation tools are only registered for applications whose executable was detected at start-up,
   so if a <software>_* tool is missing from the tool list, that application is not available on this machine.
   list_software and adapter_status report which tools are exposed or hidden and why; the user can set
   <SOFTWARE>_EXE in .env and restart the server to enable more. Tools that need no executable (diagram
   generation, file parsers, script templates) are always present.
Also provides time_* tools (timezone-aware now/convert/add/diff) and, when the user enabled internet access,
live documentation search.
Where files go: when the user gives or grants a folder for the task, call set_output_folder(<that folder>) before any
other tool, so every generated script, netlist, log and result is written there directly (relative paths in tool
arguments are resolved inside it too). Without it, files go to the adapter's default outputs folder."""

WORKSPACE_INSTRUCTIONS = """
3. workspace_* tools give full access inside the task's working directory ({root}): list/read/write/move/delete
   files, search text and run shell commands there (workspace_run). Nothing outside that folder is reachable.
   Save every file you produce inside it and use paths relative to it."""

_WS_ROOT = workspace_root() if workspace_enabled() else None
server = create_server(
    "mcp-adapter",
    INSTRUCTIONS + (WORKSPACE_INSTRUCTIONS.format(root=_WS_ROOT) if _WS_ROOT else "") + "\n" + ROUTING_GUIDE,
    __version__,
)
POLICY = SecurityPolicy.from_env()
WORKSPACE_TOOLS: list[str] = register_workspace_tools(server, _WS_ROOT) if _WS_ROOT else []

ADAPTERS = {
    "matlab": matlab_adapter,
    "simulink": simulink_adapter,
    "mathematica": mathematica_adapter,
    "comsol": comsol_adapter,
    "photoshop": photoshop_adapter,
    "orcad": pspice_adapter,
    "orcad_capture": capture_adapter,
    "altium": altium_adapter,
    "proteus": proteus_adapter,
    "vivado": vivado_adapter,
    "autocad": autocad_adapter,
    "hfss": hfss_adapter,
    "feko": feko_adapter,
    "eagle": eagle_adapter,
    "drawio": drawio_adapter,
}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


# =====================================================================================================
# Adapter mode: automation tools are registered only for applications detected at start-up
# =====================================================================================================
EXPOSE_UNAVAILABLE = expose_unavailable()
_AVAILABILITY: dict[str, bool] = {}
EXPOSED_TOOLS: dict[str, list[str]] = {}
HIDDEN_TOOLS: dict[str, list[dict[str, str]]] = {}


def _registry_key(adapter) -> str:
    """Bookkeeping key per adapter (the two OrCAD adapters share the catalog id 'orcad')."""
    return "orcad_capture" if adapter is capture_adapter else adapter.id


def _available(adapter) -> bool:
    key = _registry_key(adapter)
    if key not in _AVAILABILITY:
        _AVAILABILITY[key] = adapter.is_available()
    return _AVAILABILITY[key]


def _hide(software: str, tool_name: str, reason: str) -> None:
    HIDDEN_TOOLS.setdefault(software, []).append({"tool": tool_name, "reason": reason})


def software_tool(adapter, requires_executable: bool = True):
    """Register a tool only when its application is installed (or MCP_ADAPTER_EXPOSE_UNAVAILABLE=true).

    Tools with requires_executable=False (pure-Python helpers) are always registered.
    """
    def decorator(fn):
        key = _registry_key(adapter)
        if not app_enabled(adapter.id):
            _hide(key, fn.__name__,
                  f"{adapter.name} is switched off in the configuration ({enable_var(adapter.id)}=false); "
                  "switch it on with mcp-adapter-ui or in .env and restart the server")
            return fn
        if requires_executable and not EXPOSE_UNAVAILABLE and not _available(adapter):
            _hide(key, fn.__name__,
                  f"{adapter.name} was not detected on this machine; set {adapter.env_var} in .env "
                  "(or use mcp-adapter-ui) and restart the server to enable it")
            return fn
        EXPOSED_TOOLS.setdefault(key, []).append(fn.__name__)
        # Lead every description with the application's domain so a model can pick the right tool family.
        fn.__doc__ = f"[{tag(adapter.id)}] {(fn.__doc__ or '').strip()}"
        return server.tool()(fn)
    return decorator


# =====================================================================================================
# Catalog tools
# =====================================================================================================
@server.tool()
def list_software() -> dict[str, Any]:
    """List the supported applications with catalog size and whether the executable was found locally."""
    cat = get_catalog()
    stats = {s["id"]: s for s in cat.stats()}
    rows = []
    for sid in KNOWN_SOFTWARE:
        s = stats.get(sid, {"id": sid, "name": sid, "categories": 0, "tools": 0})
        adapter = ADAPTERS.get(sid)
        exposed = EXPOSED_TOOLS.get(sid, []) + (EXPOSED_TOOLS.get("orcad_capture", []) if sid == "orcad" else [])
        hidden = HIDDEN_TOOLS.get(sid, []) + (HIDDEN_TOOLS.get("orcad_capture", []) if sid == "orcad" else [])
        rows.append({
            **s,
            "installed": _available(adapter) if adapter else None,
            "enabled": app_enabled(sid),
            "best_for": profile(sid).get("best_for", []),
            "automation_tools_available": exposed,
            "automation_tools_hidden": [h["tool"] for h in hidden],
        })
    return {
        "software": rows,
        "total_catalog_entries": sum(r["tools"] for r in rows),
        "note": "Automation tools are registered only for installed applications (adapter mode). Hidden tools "
                "become available after setting <SOFTWARE>_EXE in .env and restarting; the catalog works for all.",
    }


@server.tool()
def list_categories(software: str) -> dict[str, Any]:
    """List the catalog categories (with tool counts) for one application, e.g. 'matlab' or 'vivado'."""
    try:
        sc = get_catalog().get_software(software)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    return {"software": sc.id, "name": sc.name, "categories": sc.categories}


@server.tool()
def list_tools(software: str, category: str | None = None, kind: str | None = None,
               limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List catalog entries for an application, optionally filtered by category substring and kind.

    Returns compact rows (name, kind, category, description). Use describe_tool for usage and examples.
    """
    try:
        items = get_catalog().tools(software, category=category, kind=kind)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    page = items[max(0, offset): max(0, offset) + max(1, min(limit, 500))]
    return {"software": resolve_software(software), "total": len(items), "offset": offset,
            "returned": len(page), "tools": [t.to_dict(full=False) for t in page]}


@server.tool()
def describe_tool(software: str, name: str, as_markdown: bool = True) -> str | dict[str, Any]:
    """Full description of one catalog entry: what it does, usage/syntax, parameters, example, docs link."""
    try:
        entry = get_catalog().get(software, name)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    if entry is None:
        hits = get_catalog().search(name, software=software, limit=8)
        suggestions = [h[1].name for h in hits]
        return _err(f"No entry named {name!r} in {software}. Similar: {suggestions}")
    return entry.to_markdown() if as_markdown else entry.to_dict()


@server.tool()
def search_tools(query: str, software: str | None = None, limit: int = 20, kind: str | None = None,
                 category: str | None = None) -> dict[str, Any]:
    """Search the catalogs by keyword(s) across all applications or one; ranked by name/category/description matches."""
    try:
        hits = get_catalog().search(query, software=software, limit=max(1, min(limit, 200)), kind=kind, category=category)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    return {"query": query, "count": len(hits),
            "results": [{"score": score, **t.to_dict(full=False), "usage": t.usage} for score, t in hits]}


@server.tool()
def software_overview(software: str) -> dict[str, Any]:
    """Everything an agent needs to decide how to use an application: what it is best for, typical tasks, what it
    is not suited to, its capability areas (catalog categories with entry counts, browse them with list_tools),
    the automation entry points (CLI flags, scripting APIs, file formats) and whether it is installed here."""
    try:
        sc = get_catalog().get_software(software)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    adapter = ADAPTERS.get(sc.id)
    return {**sc.meta, **profile(sc.id, with_areas=True), "catalog_categories": len(sc.categories),
            "catalog_tools": len(sc.tools), "adapter": adapter.status() if adapter else None}


def _installed_apps() -> set[str]:
    return {sid for sid, a in ADAPTERS.items() if sid in KNOWN_SOFTWARE and _available(a) and app_enabled(sid)}


@server.tool()
def recommend_application(task: str) -> dict[str, Any]:
    """Suggest which application (and tool family) fits an engineering task best, e.g. antennas -> HFSS, SPICE circuits -> PSpice,
    coupled physics -> COMSOL, PCB -> Altium, FPGA -> Vivado, symbolic math -> Mathematica, numerics/plots -> MATLAB.

    Call this before choosing tools for a task. Returns ranked candidates with the matched terms, what each is best for,
    whether it is installed here and its tool prefix, plus a head-to-head comparison of the applications that can do
    this kind of task (strength, when to choose each, limits). The result is a suggestion, not a rule: if the suggested
    application is not installed or the user prefers another, use an installed alternative and tell the user its limits.
    """
    installed = _installed_apps()
    rows = recommend(task, installed)
    areas = compare(task, installed)
    if not rows and not areas:
        return {"task": task, "candidates": [], "comparison": [], "note": SUGGESTION_NOTE,
                "advice": "No domain keywords recognised; use search_tools on the catalog or MATLAB for general "
                          "numerics/plots. Say which assumptions you make."}
    ranked = {r["application"] for r in rows}
    alternatives: list[dict[str, Any]] = []
    for area in areas:
        for c in area["candidates"]:
            if c["substitute"] and c["app"] not in ranked and all(a["application"] != c["app"] for a in alternatives):
                alternatives.append({"application": c["app"], "installed": c["installed"], "area": area["area"],
                                     "choose_when": c["choose_when"], "limits": c["limits"],
                                     "tools_prefix": TOOL_PREFIX.get(c["app"], [f"{c['app']}_"])})
    if areas:
        best = areas[0]
        advice = best["advice"]
        if best["suggested"]:
            advice += f" Tools: {TOOL_PREFIX.get(best['suggested'], [best['suggested'] + '_'])[0]}*."
    else:
        top = rows[0]
        alt = next((r for r in rows if r["installed"]), None)
        if top["installed"]:
            advice = f"Suggested: {top['application']} ({top['tools_prefix'][0]}* tools)."
        elif alt:
            advice = (f"{top['application']} fits best but is not installed here; {alt['application']} is installed and "
                      f"covers this domain, so it can be used instead ({alt['tools_prefix'][0]}* tools). Mention the "
                      "substitution and what it gives up.")
        else:
            advice = (f"{top['application']} fits best but is not installed here and no other candidate is; tell the "
                      "user, then use MATLAB only for what it can genuinely do.")
    return {"task": task, "candidates": rows, "comparison": areas, "alternatives": alternatives,
            "advice": advice, "note": SUGGESTION_NOTE}


@server.tool()
def compare_applications(task: str = "", area: str = "") -> dict[str, Any]:
    """Head-to-head comparison of the applications that can do the same kind of task (antennas, antenna placement/RCS,
    RF passives/SI, circuits, power electronics, schematic capture/BOM, PCB, 3D PCB/MCAD, firmware, FPGA, control,
    ODEs/numerics, symbolic maths, data/ML, signal/image processing, photo editing, structural, thermal, CFD,
    low-frequency EM, acoustics, multiphysics, batteries/chemistry, optics, EMC, drawings, diagrams). Each area has a
    one-line summary of which application is best for what; each candidate has its strength, when to choose it and its
    limits, ranked from the usual first choice, with installation status on this machine.

    Advisory only: the suggested pick is the application the task names (if installed), else the first installed
    candidate that can substitute. Any installed candidate may be used; tell the user which trade-offs apply.
    Call with no arguments to list the areas, with area=<id> for one area, or with task=<description> to match areas.
    """
    installed = _installed_apps()
    if area.strip():
        found = find_area(area)
        if not found:
            return _err(f"Unknown area {area!r}; call compare_applications() without arguments to list the areas.")
        return annotate(found, installed, named_apps(task))
    if not task.strip():
        return {"areas": area_list(), "installed": sorted(installed), "note": SUGGESTION_NOTE}
    matches = compare(task, installed, limit=3)
    if not matches:
        return {"task": task, "matches": [], "areas": area_list(), "note": SUGGESTION_NOTE,
                "advice": "No comparison area matched; pick an area from the list or call recommend_application(task)."}
    return {"task": task, "matches": matches, "note": SUGGESTION_NOTE}


def search_docs_online(query: str, software: str | None = None, max_results: int = 5,
                       depth: str = "basic", vendor_sites_only: bool = True) -> dict[str, Any]:
    """Live web search of official documentation (Tavily). Requires TAVILY_API and MCP_ADAPTER_ALLOW_INTERNET=true in .env."""
    sid = None
    if software:
        try:
            sid = resolve_software(software)
        except ValueError as exc:
            return _err(str(exc))
    return docs_search.search_docs(query, sid, max_results, depth, vendor_sites_only)


if POLICY.allow_internet:  # internet-facing tools are only registered when the policy allows them
    server.tool()(search_docs_online)
else:
    _hide("online", "search_docs_online", "disabled by MCP_ADAPTER_ALLOW_INTERNET=false")


@server.tool()
def adapter_status() -> dict[str, Any]:
    """Which applications were detected, which automation tools are exposed or hidden (and why), and the security policy."""
    adapters = []
    for a in ADAPTERS.values():
        st = a.status()
        key = _registry_key(a)
        st["exposed_tools"] = EXPOSED_TOOLS.get(key, [])
        st["hidden_tools"] = HIDDEN_TOOLS.get(key, [])
        adapters.append(st)
    return {
        "adapter_mode": "all tools exposed (MCP_ADAPTER_EXPOSE_UNAVAILABLE=true)" if EXPOSE_UNAVAILABLE
                        else "only tools of detected applications are exposed",
        "workspace": {"root": str(_WS_ROOT) if _WS_ROOT else None, "enabled": bool(_WS_ROOT), "tools": WORKSPACE_TOOLS,
                      "how_to_enable": "set MCP_ADAPTER_WORK_DIR and MCP_ADAPTER_WORK_DIR_ACCESS=true (the chat UI does "
                                       "this per chat with the folder-access switch)"},
        "adapters": adapters,
        "hidden_online_tools": HIDDEN_TOOLS.get("online", []),
        "docs_search_enabled": docs_search.is_enabled(),
        "security": POLICY.describe(),
        "output_folder": outfolder.describe(),
    }


@server.tool()
def security_policy() -> dict[str, Any]:
    """Effective network/internet policy (local-only vs network mode, which tools are disabled, how to change it) and
    the rules for the output folder."""
    return {**POLICY.describe(), "output_folder": outfolder.describe()}


@server.tool()
def set_output_folder(folder: str = "", per_application_subfolders: bool = True) -> dict[str, Any]:
    """Write all generated scripts and results into *folder*, normally the folder the user gave or granted for the task,
    instead of the adapter's default outputs folder. Call it first in a task that has a folder. Relative paths in
    later tool arguments are resolved inside it. Each application writes into its own subfolder (e.g.
    D:\\Projects\\PCB\\orcad) unless per_application_subfolders is false. An empty folder goes back to the default.

    The folder must already exist on a local disk. Drive roots, the home folder itself, hidden, system, program,
    application-data and network folders are refused; MCP_ADAPTER_OUTPUT_ROOTS in .env can restrict it further.
    The setting applies to this server process (all chats of this client) until changed or the server restarts.
    """
    try:
        return outfolder.set_output_folder(folder, per_application_subfolders)
    except outfolder.OutputFolderError as exc:
        return {**_err(str(exc)), "output_folder": str(output_dir())}


# =====================================================================================================
# Time tools
# =====================================================================================================
@server.tool()
def time_now(timezone: str = "UTC", format: str | None = None) -> dict[str, Any]:
    """Current date/time in a timezone (IANA name like 'Europe/Berlin', 'local', 'EST', or '+03:30').

    Returns ISO 8601, unix epoch, UTC offset, DST flag, weekday, ISO week and an optional strftime-formatted string.
    """
    try:
        return time_tools.now(timezone, format)
    except ValueError as exc:
        return _err(str(exc))


@server.tool()
def time_convert(datetime: str, from_timezone: str = "UTC", to_timezone: str = "UTC",
                 format: str | None = None) -> dict[str, Any]:
    """Convert a date/time between timezones. Accepts ISO 8601, unix timestamps, 'now', 'today'."""
    try:
        return time_tools.convert(datetime, from_timezone, to_timezone, format)
    except ValueError as exc:
        return _err(str(exc))


@server.tool()
def time_add(datetime: str = "now", timezone: str = "UTC", weeks: float = 0, days: float = 0, hours: float = 0,
             minutes: float = 0, seconds: float = 0, format: str | None = None) -> dict[str, Any]:
    """Add (or subtract with negatives) a duration to a date/time."""
    try:
        return time_tools.add(datetime, timezone, weeks, days, hours, minutes, seconds, format)
    except ValueError as exc:
        return _err(str(exc))


@server.tool()
def time_difference(start: str, end: str = "now", timezone: str = "UTC") -> dict[str, Any]:
    """Elapsed time between two date/times in seconds, minutes, hours, days, weeks and a human string."""
    try:
        return time_tools.difference(start, end, timezone)
    except ValueError as exc:
        return _err(str(exc))


@server.tool()
def time_format(datetime: str = "now", format: str = "%Y-%m-%d %H:%M:%S", timezone: str = "UTC") -> dict[str, Any]:
    """Format a date/time with a strftime pattern (e.g. '%A %d %B %Y, %I:%M %p')."""
    try:
        return time_tools.format_datetime(datetime, format, timezone)
    except ValueError as exc:
        return _err(str(exc))


@server.tool()
def time_list_timezones(filter: str | None = None, limit: int = 100) -> dict[str, Any]:
    """List IANA timezone names, optionally filtered by substring (e.g. 'Asia', 'London')."""
    return time_tools.list_zones(filter, limit)


@server.tool()
def time_world_clock(datetime: str = "now", zones: list[str] | None = None, from_timezone: str = "UTC") -> dict[str, Any]:
    """Show one instant across several timezones (defaults to ten major zones)."""
    try:
        return time_tools.compare_zones(datetime, zones, from_timezone)
    except ValueError as exc:
        return _err(str(exc))


@server.tool()
def time_unix(datetime: str = "now", timezone: str = "UTC") -> dict[str, Any]:
    """Convert a date/time to unix epoch seconds/milliseconds (or 'now')."""
    try:
        return time_tools.unix_time(datetime, timezone)
    except ValueError as exc:
        return _err(str(exc))


# =====================================================================================================
# MATLAB
# =====================================================================================================
@software_tool(matlab_adapter)
def matlab_run_code(code: str, capture: list[str] | None = None, working_dir: str | None = None,
                    timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run MATLAB code headless (matlab -batch). Name workspace variables in `capture` to get them back as JSON."""
    return matlab_adapter.run_code(code, capture, working_dir, timeout_seconds).to_dict()


@software_tool(matlab_adapter)
def matlab_run_file(path: str, args: list[str] | None = None, capture: list[str] | None = None,
                    timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run a .m script, or call a function file with the given argument expressions."""
    return matlab_adapter.run_file(path, args, capture, timeout_seconds).to_dict()


@software_tool(matlab_adapter)
def matlab_call_function(name: str, args: list[Any] | None = None, nargout: int = 1,
                         timeout_seconds: int | None = None) -> dict[str, Any]:
    """Call any MATLAB function with JSON arguments (numbers, strings, arrays, objects->struct) and return outputs as JSON."""
    return matlab_adapter.call_function(name, args, nargout, timeout_seconds).to_dict()


@software_tool(matlab_adapter)
def matlab_eval(expression: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Evaluate a single MATLAB expression (e.g. 'inv([2 0;0 4])' or 'roots([1 -3 2])') and return its value."""
    return matlab_adapter.eval_expression(expression, timeout_seconds).to_dict()


@software_tool(matlab_adapter)
def matlab_plot_to_file(plot_code: str, output_file: str, dpi: int = 150) -> dict[str, Any]:
    """Run plotting code headless and export the figure to PNG/PDF/SVG/EPS via exportgraphics."""
    return matlab_adapter.save_figure_code(plot_code, output_file, dpi).to_dict()


@software_tool(matlab_adapter)
def matlab_version() -> dict[str, Any]:
    """Report the MATLAB version and installation root of the detected installation."""
    return matlab_adapter.version().to_dict()


@software_tool(matlab_adapter)
def matlab_installed_toolboxes() -> dict[str, Any]:
    """List installed MathWorks toolboxes (from `ver`)."""
    return matlab_adapter.installed_toolboxes().to_dict()


# =====================================================================================================
# Simulink
# =====================================================================================================
@software_tool(simulink_adapter)
def simulink_simulate(model: str, stop_time: str | None = None, variables: dict[str, Any] | None = None,
                      block_parameters: list[dict[str, str]] | None = None, save_mat: bool = True,
                      max_samples: int = 200, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Simulate a Simulink model (name on the path or .slx path) headless and summarise logged signals.

    variables: workspace variables used by the model; block_parameters: [{block, parameter, value}].
    """
    return simulink_adapter.simulate(model, stop_time, variables, block_parameters, save_mat, max_samples,
                                     timeout_seconds).to_dict()


@software_tool(simulink_adapter)
def simulink_model_info(model: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Solver settings, block count, ports and subsystems of a model."""
    return simulink_adapter.model_info(model, timeout_seconds).to_dict()


@software_tool(simulink_adapter)
def simulink_list_blocks(model: str, block_type: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """List all block paths (optionally of one BlockType such as 'Gain' or 'Scope')."""
    return simulink_adapter.list_blocks(model, block_type, timeout_seconds).to_dict()


@software_tool(simulink_adapter)
def simulink_get_block_parameters(block_path: str, parameters: list[str] | None = None,
                                  timeout_seconds: int | None = None) -> dict[str, Any]:
    """Read block parameters ('model/Subsystem/Gain'); without a list returns all dialog parameters."""
    return simulink_adapter.get_block_parameters(block_path, parameters, timeout_seconds).to_dict()


@software_tool(simulink_adapter)
def simulink_set_block_parameters(block_path: str, parameters: dict[str, str], save: bool = True,
                                  timeout_seconds: int | None = None) -> dict[str, Any]:
    """Set block parameters with set_param and optionally save the model."""
    return simulink_adapter.set_block_parameters(block_path, parameters, save, timeout_seconds).to_dict()


@software_tool(simulink_adapter)
def simulink_build_model(model_name: str, blocks: list[dict[str, Any]], lines: list[dict[str, str]],
                         directory: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Create a new model from block specs [{library, name, position?, parameters?}] and lines [{from:'A/1', to:'B/1'}]."""
    return simulink_adapter.build_model(model_name, blocks, lines, directory, timeout_seconds).to_dict()


@software_tool(simulink_adapter)
def simulink_export_diagram(model: str, output_file: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Render the block diagram to PNG/PDF/SVG."""
    return simulink_adapter.export_diagram(model, output_file, timeout_seconds).to_dict()


# =====================================================================================================
# Mathematica
# =====================================================================================================
@software_tool(mathematica_adapter)
def mathematica_evaluate(code: str, output_format: str = "text", timeout_seconds: int | None = None) -> dict[str, Any]:
    """Evaluate Wolfram Language code with wolframscript. output_format: text | inputform | json | tex | fullform."""
    return mathematica_adapter.evaluate(code, output_format, timeout_seconds).to_dict()


@software_tool(mathematica_adapter)
def mathematica_run_file(path: str, args: list[str] | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run a .wls/.wl/.m script file; args are available in $ScriptCommandLine."""
    return mathematica_adapter.run_file(path, args, timeout_seconds).to_dict()


@software_tool(mathematica_adapter)
def mathematica_run_script(script: str, args: list[str] | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run a multi-line Wolfram Language script (saved to a temporary .wls file)."""
    return mathematica_adapter.run_script_text(script, args, timeout_seconds).to_dict()


@software_tool(mathematica_adapter)
def mathematica_export(expression: str, output_file: str, export_format: str | None = None,
                       options: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Export the value of an expression (Plot[...], Image, Dataset, ...) to a file with Export[]."""
    return mathematica_adapter.export(expression, output_file, export_format, options, timeout_seconds).to_dict()


def mathematica_wolfram_alpha(query: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Ask Wolfram|Alpha through the Wolfram Language (needs internet, a signed-in Wolfram account and MCP_ADAPTER_ALLOW_INTERNET=true)."""
    return mathematica_adapter.wolfram_alpha(query, timeout_seconds).to_dict()


if POLICY.allow_internet:
    software_tool(mathematica_adapter)(mathematica_wolfram_alpha)
else:
    _hide("online", "mathematica_wolfram_alpha", "disabled by MCP_ADAPTER_ALLOW_INTERNET=false")


@software_tool(mathematica_adapter)
def mathematica_version() -> dict[str, Any]:
    """Report the Wolfram Language version and installation directory."""
    return mathematica_adapter.version().to_dict()


# =====================================================================================================
# COMSOL
# =====================================================================================================
@software_tool(comsol_adapter)
def comsol_run_batch(input_file: str, output_file: str | None = None, study: str | None = None,
                     parameters: dict[str, str] | None = None, cores: int | None = None,
                     extra_args: list[str] | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Solve an .mph model headless with comsolbatch (optionally one study, parameter list, core count)."""
    return comsol_adapter.run_batch(input_file, output_file, study, parameters, cores, None, extra_args,
                                    timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_run_method(input_file: str, method_name: str, output_file: str | None = None,
                      timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run an Application Builder model method by name (-methodcall)."""
    return comsol_adapter.run_method(input_file, method_name, output_file, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_compile_java(java_file: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Compile a COMSOL Java API program (comsol compile); run the .class with comsol_run_batch."""
    return comsol_adapter.compile_java(java_file, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_run_python(code: str, model_file: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run Python against the COMSOL API via the MPh library; `client` and `model` are pre-created."""
    return comsol_adapter.run_mph_python(code, model_file, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_model_summary(model_file: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Parameters, physics, studies, datasets and plots of an .mph file via the optional MPh library
    (prefer comsol_inspect_model, which needs no extra Python package)."""
    return comsol_adapter.model_summary(model_file, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_list_modules() -> dict[str, Any]:
    """Installed COMSOL modules (from the local installation) with their number of Application Library examples
    and User's Guide PDFs - tells an agent which physics this machine can simulate."""
    index = comsol_adapter.library_index()
    if index is None:
        return _err("COMSOL installation folder not found; set COMSOL_EXE or COMSOL_ROOT")
    return {"ok": True, "root": index["root"], "modules": index["modules"], "examples_total": len(index["examples"])}


@software_tool(comsol_adapter)
def comsol_search_examples(query: str, module: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Search the ~2,000 Application Library example simulations installed with COMSOL by topic, physics or study
    (e.g. "busbar joule heating", "lithium-ion battery aging", "microstrip patch antenna", "topology optimization").
    Each hit lists physics interfaces and study types; open one with comsol_example_info and solve it with
    comsol_run_example. Starting from the closest example is the fastest way to set up a new simulation."""
    index = comsol_adapter.library_index()
    if index is None:
        return _err("COMSOL installation folder not found; set COMSOL_EXE or COMSOL_ROOT")
    return {"ok": True, "query": query, "results": search_examples(index, query, module, max(1, min(limit, 50)))}


@software_tool(comsol_adapter)
def comsol_example_info(name: str, include_java: bool = False, max_java_chars: int = 30000) -> dict[str, Any]:
    """Details of one Application Library example (name, library path or exact title): title, introduction,
    physics and couplings, study steps, the .mph path, the documentation PDF/HTML and the documented Java script
    that builds and solves the model step by step (include_java=true returns its source, trimmed to
    max_java_chars) - adapt that Java to create new models, then run it with comsol_build_from_java."""
    index = comsol_adapter.library_index()
    ex = find_example(index or {}, name) if index else None
    if ex is None:
        return _err(f"No Application Library example named {name!r}; use comsol_search_examples")
    out = {"ok": True, **ex}
    if include_java and ex.get("java"):
        src = Path(ex["java"]).read_text(encoding="utf-8", errors="replace")
        out["java_source"] = src[:max_java_chars]
        out["java_truncated"] = len(src) > max_java_chars
    return out


@software_tool(comsol_adapter)
def comsol_run_example(name: str, output_dir: str | None = None, method: str = "mph", study: str | None = None,
                       cores: int | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Copy an Application Library example into output_dir (default outputs/comsol/examples) and solve it headless:
    method "mph" re-solves the library model, "java" rebuilds it from its documented Java script. Returns the
    solved .mph path; read results with comsol_inspect_model / comsol_evaluate. Large examples can take long."""
    return comsol_adapter.run_example(name, output_dir, method, study, cores, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_build_from_java(java_file: str, output_file: str | None = None,
                           timeout_seconds: int | None = None) -> dict[str, Any]:
    """Compile and run a COMSOL model Java file (the class name must match the file name) to build, solve and
    save a model; the saved .mph is in artifacts.output_file. Compilation errors are returned in stdout."""
    return comsol_adapter.build_from_java(java_file, output_file, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_inspect_model(model_file: str, evaluate_derived_values: bool = True,
                         timeout_seconds: int | None = None) -> dict[str, Any]:
    """Read an .mph without extra Python packages: title, parameters, components with physics interfaces,
    multiphysics couplings and materials, studies with step types, datasets, plot groups and the model's
    derived values (Global Evaluation, integrals...) with their computed numbers."""
    return comsol_adapter.inspect_model(model_file, evaluate_derived_values, timeout_seconds).to_dict()


@software_tool(comsol_adapter)
def comsol_evaluate(model_file: str, expressions: list[str], units: list[str] | None = None, kind: str = "global",
                    dataset: str | None = None, entities: list[int] | None = None,
                    timeout_seconds: int | None = None) -> dict[str, Any]:
    """Evaluate expressions on a solved .mph: kind "global" (e.g. es.C11, spf.Uave, a parameter expression) or
    volume_integral/surface_integral/line_integral/volume_average/surface_average/volume_maximum/volume_minimum/
    surface_maximum/surface_minimum over all meshed entities or the given entity numbers (in 2D use surface_*
    for domains). Units are optional per expression (e.g. "pF", "degC")."""
    return comsol_adapter.evaluate(model_file, expressions, units, kind, dataset, entities, timeout_seconds).to_dict()


# =====================================================================================================
# Photoshop
# =====================================================================================================
@software_tool(photoshop_adapter)
def photoshop_run_jsx(script: str, is_file: bool = False, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run ExtendScript in Photoshop. Call mcpResult({...}) inside the script to return JSON data."""
    return photoshop_adapter.run_jsx(script, is_file, True, timeout_seconds).to_dict()


@software_tool(photoshop_adapter)
def photoshop_run_action(action: str, action_set: str, document: str | None = None, save_as: str | None = None,
                         timeout_seconds: int | None = None) -> dict[str, Any]:
    """Play a recorded Action from the Actions panel, optionally opening a file first and saving the result."""
    return photoshop_adapter.run_action(action, action_set, document, save_as, timeout_seconds).to_dict()


@software_tool(photoshop_adapter)
def photoshop_document_info(document: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Size, resolution, color mode and layer tree of a document (opens the path, or uses the active document)."""
    return photoshop_adapter.document_info(document, timeout_seconds).to_dict()


@software_tool(photoshop_adapter)
def photoshop_batch_process(input_files: list[str], output_dir: str, operations: list[dict[str, Any]],
                            output_format: str = "png", quality: int = 90, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Apply operations to many images and save them, e.g. [{"op":"resize_width","width":1200},{"op":"unsharp_mask","amount":80,"radius":1.2,"threshold":2}].

    Ops: resize, resize_width, canvas, crop, rotate, flip_horizontal, flip_vertical, grayscale, rgb, cmyk, flatten,
    merge_visible, auto_contrast, auto_levels, brightness_contrast, levels, gaussian_blur, unsharp_mask, sharpen,
    invert, desaturate, resolution, trim, add_text.
    """
    return photoshop_adapter.batch_process(input_files, output_dir, operations, output_format, quality, timeout_seconds).to_dict()


@software_tool(photoshop_adapter)
def photoshop_export_layers(document: str, output_dir: str, output_format: str = "png",
                            timeout_seconds: int | None = None) -> dict[str, Any]:
    """Export each top-level layer of a PSD as its own PNG/JPEG."""
    return photoshop_adapter.export_layers(document, output_dir, output_format, timeout_seconds).to_dict()


# =====================================================================================================
# OrCAD
# =====================================================================================================
@software_tool(pspice_adapter)
def orcad_pspice_simulate(circuit_file: str, extra_args: list[str] | None = None,
                          timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run a PSpice .cir/.net circuit in batch mode and parse errors, warnings and bias-point voltages."""
    return pspice_adapter.simulate(circuit_file, extra_args, timeout_seconds).to_dict()


@software_tool(pspice_adapter)
def orcad_pspice_simulate_netlist(netlist: str, name: str = "circuit", timeout_seconds: int | None = None) -> dict[str, Any]:
    """Write PSpice netlist text (components, .MODEL, .TRAN/.AC/.DC, .PROBE, .END) to a file and simulate it."""
    return pspice_adapter.simulate_netlist(netlist, name, timeout_seconds).to_dict()


@software_tool(pspice_adapter, requires_executable=False)
def orcad_parse_pspice_output(out_file: str) -> dict[str, Any]:
    """Parse an existing PSpice .out file (errors, warnings, node voltages, total power)."""
    return parse_pspice_output(out_file)


@software_tool(capture_adapter)
def orcad_capture_open(design_file: str) -> dict[str, Any]:
    """Open a .dsn/.opj design in OrCAD Capture (GUI, detached)."""
    return capture_adapter.open_design(design_file).to_dict()


@software_tool(capture_adapter)
def orcad_capture_run_tcl(script: str, is_file: bool = False, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Prepare/run a Capture Tcl script (executed via ORCAD_CAPTURE_TCL_CMD when configured, else returns source instructions)."""
    return capture_adapter.run_tcl(script, is_file, timeout_seconds).to_dict()


# =====================================================================================================
# Altium Designer
# =====================================================================================================
@software_tool(altium_adapter)
def altium_open(path: str) -> dict[str, Any]:
    """Open a project or document in Altium Designer (GUI, detached)."""
    return altium_adapter.open(path).to_dict()


@software_tool(altium_adapter)
def altium_run_script_project(script_project: str, procedure: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run 'Unit>Procedure' from an existing .PrjScr via X2.EXE -RScriptFile/-RProcName."""
    return altium_adapter.run_script_project(script_project, procedure, timeout_seconds).to_dict()


@software_tool(altium_adapter)
def altium_run_delphiscript(code: str, procedure: str = "Main", unit_name: str = "MCPScript",
                            timeout_seconds: int | None = None) -> dict[str, Any]:
    """Wrap DelphiScript code (must define `procedure Main;`) in a script project and run it in Altium."""
    return altium_adapter.run_delphiscript(code, procedure, unit_name, timeout_seconds).to_dict()


@software_tool(altium_adapter, requires_executable=False)
def altium_script_template(template: str, project_path: str | None = None, output_file: str | None = None) -> dict[str, Any]:
    """Return ready-made DelphiScript: template 'export_bom' (project_path, output_file .csv) or 'pcb_stats' (output_file .txt)."""
    if template == "export_bom":
        if not project_path or not output_file:
            return _err("export_bom needs project_path and output_file")
        return {"template": template, "code": altium_adapter.script_export_bom(project_path, output_file)}
    if template == "pcb_stats":
        if not output_file:
            return _err("pcb_stats needs output_file")
        return {"template": template, "code": altium_adapter.script_pcb_stats(output_file)}
    return _err("Unknown template; use 'export_bom' or 'pcb_stats'")


# =====================================================================================================
# Proteus
# =====================================================================================================
@software_tool(proteus_adapter)
def proteus_open_project(project_file: str, extra_args: list[str] | None = None) -> dict[str, Any]:
    """Open a .pdsprj in Proteus (GUI, detached)."""
    return proteus_adapter.open_project(project_file, extra_args).to_dict()


@software_tool(proteus_adapter)
def proteus_run_cli(args: list[str], wait: bool = True, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run PDS.EXE with custom arguments (for release-specific command-line switches)."""
    return proteus_adapter.run_cli(args, wait, timeout_seconds).to_dict()


@software_tool(proteus_adapter, requires_executable=False)
def proteus_project_info(project_file: str) -> dict[str, Any]:
    """Inspect a .pdsprj container without opening Proteus: embedded files, firmware references, MCU hints."""
    return proteus_adapter.project_info(project_file).to_dict()


# =====================================================================================================
# Vivado
# =====================================================================================================
@software_tool(vivado_adapter)
def vivado_run_tcl(script: str, is_file: bool = False, tclargs: list[str] | None = None, working_dir: str | None = None,
                   timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run Tcl in Vivado batch mode (vivado -mode batch -source). Generated scripts can call mcp_emit <json> to return data."""
    return vivado_adapter.run_tcl(script, is_file, tclargs, working_dir, timeout_seconds, parse_json=True).to_dict()


@software_tool(vivado_adapter)
def vivado_project_info(project_file: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Part, top module, sources, constraints, IPs and run status of an .xpr project."""
    return vivado_adapter.project_info(project_file, timeout_seconds).to_dict()


@software_tool(vivado_adapter)
def vivado_build_project(project_file: str, run_name: str = "impl_1", to_step: str = "write_bitstream", jobs: int = 4,
                         reset_runs: bool = False, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Synthesize, implement and (by default) write the bitstream for a project; reports WNS/TNS and bitstreams."""
    return vivado_adapter.build_project(project_file, run_name, to_step, jobs, reset_runs, timeout_seconds).to_dict()


@software_tool(vivado_adapter)
def vivado_reports(project_file: str, run_name: str = "impl_1", reports: list[str] | None = None,
                   timeout_seconds: int | None = None) -> dict[str, Any]:
    """Generate reports for an implemented run: timing, utilization, power, drc, methodology, io, clocks, route_status."""
    return vivado_adapter.reports(project_file, run_name, reports, timeout_seconds).to_dict()


@software_tool(vivado_adapter)
def vivado_program_device(bitstream: str, probes_file: str | None = None, device_index: int = 0,
                          hw_server: str = "localhost:3121", timeout_seconds: int | None = None) -> dict[str, Any]:
    """Program a JTAG-connected FPGA with a .bit file through Hardware Manager."""
    return vivado_adapter.program_device(bitstream, probes_file, device_index, hw_server, timeout_seconds).to_dict()


@software_tool(vivado_adapter)
def vivado_create_project(name: str, directory: str, part: str, sources: list[str], constraints: list[str] | None = None,
                          top: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Create a new RTL project from HDL sources and XDC constraints for a target part (e.g. xc7a35tcpg236-1)."""
    return vivado_adapter.create_project(name, directory, part, sources, constraints, top, timeout_seconds).to_dict()


@software_tool(vivado_adapter)
def vivado_run_hls(script: str, is_file: bool = False, working_dir: str | None = None,
                   timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run a Vitis HLS Tcl script (vitis_hls -f)."""
    return vivado_adapter.run_hls(script, is_file, working_dir, timeout_seconds).to_dict()


# =====================================================================================================
# AutoCAD
# =====================================================================================================
@software_tool(autocad_adapter)
def autocad_run_script(script: str, drawing: str | None = None, is_file: bool = False,
                       timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run an AutoCAD .scr script headless with the Core Console, optionally on a drawing."""
    return autocad_adapter.run_script(script, drawing, is_file, "en-US", timeout_seconds).to_dict()


@software_tool(autocad_adapter)
def autocad_run_lisp(lisp_code: str, drawing: str | None = None, call: str | None = None, save: bool = False,
                     timeout_seconds: int | None = None) -> dict[str, Any]:
    """Load AutoLISP code headless and optionally call an expression; (mcp-json-write alist *mcp-result-file*) returns data."""
    return autocad_adapter.run_lisp(lisp_code, drawing, call, save, timeout_seconds).to_dict()


@software_tool(autocad_adapter)
def autocad_drawing_info(drawing: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Layers, blocks, layouts, styles, entity counts and extents of a DWG."""
    return autocad_adapter.drawing_info(drawing, timeout_seconds).to_dict()


@software_tool(autocad_adapter)
def autocad_export_dxf(drawing: str, output_file: str, precision: int = 16, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Convert a DWG to DXF (DXFOUT)."""
    return autocad_adapter.export_dxf(drawing, output_file, precision, timeout_seconds).to_dict()


@software_tool(autocad_adapter)
def autocad_plot_to_pdf(drawing: str, output_file: str, layout: str = "Model",
                        paper: str = "ISO A3 (420.00 x 297.00 MM)", timeout_seconds: int | None = None) -> dict[str, Any]:
    """Plot a layout to PDF with the DWG To PDF driver (extents, fit to paper)."""
    return autocad_adapter.plot_to_pdf(drawing, output_file, layout, paper, "Extents", timeout_seconds).to_dict()


@software_tool(autocad_adapter)
def autocad_batch(script: str, drawings: list[str], is_file: bool = False, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run one script against many drawings."""
    return autocad_adapter.batch(script, drawings, is_file, timeout_seconds)


@software_tool(autocad_adapter)
def autocad_send_command(command: str, wait_seconds: float = 1.0) -> dict[str, Any]:
    """Send a command line to a running AutoCAD session over COM (Windows)."""
    return autocad_adapter.com_send_command(command, wait_seconds).to_dict()


# =====================================================================================================
# HFSS
# =====================================================================================================
@software_tool(hfss_adapter)
def hfss_run_script(script: str, project: str | None = None, is_file: bool = False, non_graphical: bool = True,
                    script_args: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run an AEDT IronPython script (oDesktop/oProject/oDesign API) and exit; call mcp_result(obj) to return JSON."""
    return hfss_adapter.run_script(script, project, is_file, non_graphical, script_args, False, timeout_seconds).to_dict()


@software_tool(hfss_adapter)
def hfss_batch_solve(project: str, design: str | None = None, setup: str | None = None, cores: int | None = None,
                     distributed: bool = False, machinelist: str | None = None,
                     timeout_seconds: int | None = None) -> dict[str, Any]:
    """Solve a project (or design:setup) with ansysedt -BatchSolve."""
    return hfss_adapter.batch_solve(project, design, setup, cores, distributed, machinelist, None, timeout_seconds).to_dict()


@software_tool(hfss_adapter)
def hfss_project_info(project: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Designs, variables, setups/sweeps, boundaries, excitations and model objects of an .aedt project."""
    return hfss_adapter.project_info(project, timeout_seconds).to_dict()


@software_tool(hfss_adapter)
def hfss_set_variables_and_solve(project: str, design: str, variables: dict[str, str], setup: str | None = None,
                                 save: bool = True, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Change design variables (e.g. {"patch_L": "12mm"}) and analyze a setup or all setups."""
    return hfss_adapter.set_variables_and_solve(project, design, variables, setup, save, timeout_seconds).to_dict()


@software_tool(hfss_adapter)
def hfss_export_touchstone(project: str, design: str, setup: str, sweep: str, output_file: str,
                           timeout_seconds: int | None = None) -> dict[str, Any]:
    """Export S-parameters of setup:sweep to a Touchstone .sNp file."""
    return hfss_adapter.export_touchstone(project, design, setup, sweep, output_file, timeout_seconds).to_dict()


@software_tool(hfss_adapter)
def hfss_export_report_csv(project: str, design: str, report_name: str, output_file: str,
                           timeout_seconds: int | None = None) -> dict[str, Any]:
    """Export an existing Results report to CSV."""
    return hfss_adapter.export_report_csv(project, design, report_name, output_file, timeout_seconds).to_dict()


@software_tool(hfss_adapter)
def hfss_run_pyaedt(code: str, project: str | None = None, design: str | None = None, version: str | None = None,
                    non_graphical: bool = True, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run PyAEDT code with a ready `hfss` object (ansys.aedt.core.Hfss); requires pip install pyaedt."""
    return hfss_adapter.run_pyaedt(code, project, design, version, non_graphical, timeout_seconds).to_dict()


# ---- Altair Feko ---------------------------------------------------------------------------------
@software_tool(feko_adapter)
def feko_version(timeout_seconds: int | None = None) -> dict[str, Any]:
    """Installed Feko version, parsed from the banner a bare `runfeko` prints (the program has no --version switch)."""
    return feko_adapter.version(timeout_seconds).to_dict()


@software_tool(feko_adapter)
def feko_solve(model: str, processes: int | None = None, use_gpu: bool = False, priority: int | None = None,
               extra_args: list[str] | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Solve a Feko model (.cfx, .pre, .fek or .nfp) headless with runfeko: antennas, antenna placement on
    vehicles/aircraft/ships (MoM/MLFMM/FEM/PO/UTD hybrids), RCS, EMC and cable coupling, SAR, periodic structures.
    runfeko runs cadfeko_batch/prefeko itself when the mesh or .fek is out of date. Returns the parsed .out summary
    (frequencies, source impedance/current/power, warnings, errors) and the .out/.bof/export paths; `processes`
    sets -np, `use_gpu` adds --use-gpu, `priority` 0 (idle) to 4 (high)."""
    return feko_adapter.solve(model, processes, use_gpu, priority, extra_args, timeout_seconds).to_dict()


@software_tool(feko_adapter)
def feko_batch_process(model: str, variables: dict[str, str] | None = None, force_mesh: bool = False,
                       timeout_seconds: int | None = None) -> dict[str, Any]:
    """Change model variables of a .cfx and/or re-mesh it without the GUI via `cadfeko_batch model.cfx -# VAR=VALUE
    --force-mesh`; the quickest way to run parameter studies (then call feko_solve). Values may be expressions."""
    return feko_adapter.batch_process(model, variables, force_mesh, timeout_seconds).to_dict()


@software_tool(feko_adapter)
def feko_run_cadfeko_script(script: str, is_file: bool = False, model: str | None = None, configure: str | None = None,
                            timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run CADFEKO Lua (cf API) headless: `cadfeko [model.cfx] --non-interactive --run-script file.lua`. Build or
    modify geometry, media, ports, sources, loads, frequency and requests, mesh, save the .cfx and optionally launch
    the solver. `configure` is a Lua string executed before the script (e.g. "freq=2.4e9 out=[[D:/x]]"). Feko sets
    the working directory to the script's folder, so use absolute paths. Start from feko_lua_template."""
    return feko_adapter.run_cadfeko_script(script, is_file, model, configure, timeout_seconds).to_dict()


@software_tool(feko_adapter)
def feko_run_postfeko_script(script: str, is_file: bool = False, model: str | None = None,
                             configure: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run POSTFEKO Lua (pf API) headless: `postfeko [model.fek|session.pfs] --non-interactive --run-script file.lua`.
    Read far/near fields, S-parameters, source impedances, currents, SAR or characteristic modes from a solved
    .fek/.bof and export CSV, .ffe, Touchstone or images; `configure` pre-sets Lua variables for the script."""
    return feko_adapter.run_postfeko_script(script, is_file, model, configure, timeout_seconds).to_dict()


@software_tool(feko_adapter, requires_executable=False)
def feko_parse_out(out_file: str) -> dict[str, Any]:
    """Parse a Feko .out report (no Feko needed): solution frequencies, per-source impedance/current/power,
    section headings, mesh/memory/time summary lines, warnings and errors."""
    return feko_adapter.parse_out(out_file).to_dict()


@software_tool(feko_adapter, requires_executable=False)
def feko_lua_template(kind: str, model: str = "D:/feko/model.cfx", output: str = "D:/feko/result.csv",
                      frequency_hz: float = 300e6) -> dict[str, Any]:
    """Ready-made Lua scripts to adapt: 'dipole' (CADFEKO build + solve), 'export_source_data' (POSTFEKO impedance
    CSV), 'export_farfield' (POSTFEKO .ffe export), 'rcs_sweep' (CADFEKO plane-wave monostatic RCS)."""
    try:
        return {"ok": True, "kind": kind, "script": feko_template_text(kind, model, output, frequency_hz)}
    except KeyError as exc:
        return {"ok": False, "error": str(exc)}


# =====================================================================================================
# Autodesk EAGLE
# =====================================================================================================
@software_tool(eagle_adapter)
def eagle_version() -> dict[str, Any]:
    """EAGLE version from the console banner (eaglecon -?) and the executable in use."""
    return eagle_adapter.version().to_dict()


@software_tool(eagle_adapter)
def eagle_cam_jobs() -> dict[str, Any]:
    """CAM job files shipped with EAGLE (usable by name in eagle_cam_job) and the legacy output devices of eagle.def."""
    return {"cam_jobs": eagle_adapter.cam_jobs(), "legacy_devices": eagle_adapter.devices()}


@software_tool(eagle_adapter)
def eagle_cam_job(board: str, cam_job: str = "", output_dir: str = "", variant: str = "",
                  timeout_seconds: int | None = None) -> dict[str, Any]:
    """Generate manufacturing data from an EAGLE board (.brd) with a JSON CAM job (eaglecon -X -dCAMJOB): Gerber files,
    the Gerber job file, Excellon drill and assembly/pick-and-place data under <output_dir>/CAMOutputs.

    cam_job is a .cam path or the name of a shipped job (eagle_cam_jobs); empty picks the shipped example_N_layer job
    that matches the board's copper layers. variant selects an assembly variant. Runs without an Autodesk sign-in
    (the command-line CAM Processor still works after EAGLE's retirement on 7 June 2026) and also reads binary
    pre-6.0 boards."""
    return eagle_adapter.cam_job(board, cam_job or None, output_dir or None, variant or None,
                                 timeout_seconds).to_dict()


@software_tool(eagle_adapter)
def eagle_cam_output(board: str, device: str, layers: list[str], output_file: str, flags: list[str] | None = None,
                     timeout_seconds: int | None = None) -> dict[str, Any]:
    """Legacy single-file CAM output: eaglecon -X -d<device> -o<file> board.brd <layers>, e.g. device GERBER_RS274X with
    layers ["1", "17", "18"] (Top, Pads, Vias) or EXCELLON with ["44", "45"]. Device names: eagle_cam_jobs."""
    return eagle_adapter.cam_output(board, device, layers, output_file, flags, timeout_seconds).to_dict()


@software_tool(eagle_adapter)
def eagle_run_commands(design_file: str, commands: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Open a .sch/.brd/.lbr in EAGLE's editor and execute editor commands (eagle -C), e.g. "RUN bom.ulp" or
    "SCRIPT fix.scr; WRITE"; QUIT is appended. Since EAGLE's retirement (7 June 2026) the editor needs an Autodesk
    sign-in: when it stops at the 'Sign in' window the tool closes it and reports that (it never signs in). Prefer
    eagle_cam_job, eagle_read_design, eagle_bom and eagle_netlist, which do not need the editor."""
    return eagle_adapter.run_commands(design_file, commands, timeout_seconds).to_dict()


@software_tool(eagle_adapter, requires_executable=False)
def eagle_read_design(design_file: str, max_items: int = 500) -> dict[str, Any]:
    """Read an EAGLE XML design without EAGLE: schematic (sheets, parts with package and attributes, nets with pins,
    variants, modules), board (copper layers, size, elements with position/rotation, signals with pads/wires/vias,
    unrouted signals, key design rules) or library (packages, symbols, device sets)."""
    try:
        return eagle_read(design_file, max_items)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))


@software_tool(eagle_adapter, requires_executable=False)
def eagle_bom(design_file: str, output_csv: str = "") -> dict[str, Any]:
    """Bill of materials from an EAGLE schematic or board (XML, no EAGLE needed), grouped by value, package, manufacturer
    and MPN attributes, with designators; parts without a package (frames, supply symbols) are left out. Optionally
    writes a CSV (relative paths go into the current output folder)."""
    try:
        return eagle_bom_data(design_file, output_csv or None)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))


@software_tool(eagle_adapter, requires_executable=False)
def eagle_netlist(design_file: str, output_file: str = "") -> dict[str, Any]:
    """Netlist from an EAGLE schematic (part.pin per net) or board (element.pad per signal), XML only; optionally
    written as a tab-separated text file."""
    try:
        return eagle_netlist_data(design_file, output_file or None)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))


# =====================================================================================================
# draw.io
# =====================================================================================================
@software_tool(drawio_adapter, requires_executable=False)
def drawio_create_diagram(nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None,
                          output_file: str | None = None, direction: str = "TB", title: str = "Page-1",
                          export_format: str | None = None) -> dict[str, Any]:
    """Build a .drawio diagram from nodes [{id,label,shape?,style?,x?,y?,width?,height?}] and edges [{source,target,label?,style?}].

    Shapes: rectangle, rounded, ellipse, circle, rhombus/decision, process, terminator, start, end, document, cylinder/database,
    cloud, note, hexagon, parallelogram/data, triangle, actor, text, card, step, container/swimlane, cube, component, umlclass.
    Layout is automatic (layered, direction TB or LR) unless x/y are given. Set export_format=png|svg|pdf to also render.
    """
    return drawio_adapter.create_diagram(nodes, edges, output_file, direction, title, export_format).to_dict()


@software_tool(drawio_adapter, requires_executable=False)
def drawio_flowchart(steps: list[str], output_file: str | None = None, title: str = "Flowchart", direction: str = "TB",
                     export_format: str | None = None) -> dict[str, Any]:
    """Quick linear flowchart from a list of step labels (labels ending with '?' become decision diamonds)."""
    return drawio_adapter.flowchart(steps, output_file, title, direction, export_format).to_dict()


@software_tool(drawio_adapter)
def drawio_export(input_file: str, output_file: str, export_format: str = "png", page_index: int | None = None,
                  all_pages: bool = False, transparent: bool = False, scale: float | None = None, border: int | None = None,
                  width: int | None = None, height: int | None = None, crop: bool = False, embed_diagram: bool = False,
                  timeout_seconds: int | None = None) -> dict[str, Any]:
    """Export a .drawio file to png/jpg/svg/pdf/vsdx/xml with the draw.io desktop CLI."""
    return drawio_adapter.export(input_file, output_file, export_format, page_index, all_pages, transparent, scale, border,
                                 width, height, crop, embed_diagram, timeout_seconds).to_dict()


@software_tool(drawio_adapter, requires_executable=False)
def drawio_read(path: str) -> dict[str, Any]:
    """Read a .drawio/.xml file: pages, vertex/edge counts, labels and the decoded mxGraphModel XML."""
    return drawio_adapter.read(path).to_dict()


@software_tool(drawio_adapter, requires_executable=False)
def drawio_codec(text: str, mode: str = "decode") -> dict[str, Any]:
    """Decode a compressed <diagram> payload to XML (mode=decode) or compress XML to draw.io format (mode=encode)."""
    try:
        if mode == "encode":
            return {"ok": True, "encoded": encode_diagram(text)}
        return {"ok": True, "xml": decode_diagram(text)}
    except Exception as exc:  # zlib/base64 errors
        return _err(f"{mode} failed: {exc}")


# =====================================================================================================
# Resources
# =====================================================================================================
@server.resource("catalog://software")
def resource_software_list() -> str:
    """JSON list of supported applications and catalog sizes."""
    return json.dumps(list_software(), indent=2)


@server.resource("catalog://{software}/categories")
def resource_categories(software: str) -> str:
    """Categories of one application's catalog."""
    return json.dumps(list_categories(software), indent=2)


@server.resource("catalog://{software}/{name}")
def resource_tool(software: str, name: str) -> str:
    """Markdown description of a single catalog entry."""
    out = describe_tool(software, name)
    return out if isinstance(out, str) else json.dumps(out, indent=2)


# =====================================================================================================
# Entry point
# =====================================================================================================
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mcp-adapter",
        description="MCP server for engineering/design software. Network exposure is governed by "
                    "MCP_ADAPTER_NETWORK_MODE in .env (default: local = loopback only); run mcp-adapter-setup to choose.",
    )
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio",
                        help="stdio (default, opens no port) or an HTTP transport that listens on --host/--port")
    parser.add_argument("--host", default=None,
                        help="bind address for HTTP transports (default 127.0.0.1; non-loopback needs network mode)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--print-policy", action="store_true", help="print the effective security policy and exit")
    parser.add_argument("--version", action="version", version=f"mcp-adapter {__version__}")
    args = parser.parse_args(argv)

    if args.print_policy:
        print(json.dumps(POLICY.describe(), indent=2))
        return
    if args.transport == "stdio":
        print(POLICY.startup_banner("stdio", None, None), file=sys.stderr)
        run_server(server, "stdio")
        return
    try:
        host = POLICY.effective_host(args.host)
        POLICY.check_http_start()
    except SecurityError as exc:
        print(f"[mcp-adapter] refused to start: {exc}", file=sys.stderr)
        sys.exit(2)
    print(POLICY.startup_banner(args.transport, host, args.port), file=sys.stderr)
    run_server(server, args.transport, host, args.port, POLICY.transport_security(host, args.port),
               auth_token=POLICY.auth_token)


if __name__ == "__main__":
    main()
