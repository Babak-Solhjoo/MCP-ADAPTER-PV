"""AMD Vivado adapter: runs Tcl headless with ``vivado -mode batch -source``.

Set ``VIVADO_EXE`` to ``vivado.bat`` (Windows) or ``vivado`` (Linux) when it is not on PATH.
``VITIS_HLS_EXE`` optionally points at ``vitis_hls`` for HLS scripts.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import find_executable
from .base import JSON_END, JSON_START, BaseAdapter, RunResult, read_text_if_exists, resolve_path


def tcl_string(text: str) -> str:
    """Quote *text* as a Tcl braced literal when safe, else a double-quoted string."""
    if "}" not in text and "{" not in text:
        return "{" + text + "}"
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("[", "\\[").replace("$", "\\$") + '"'


def tcl_path(path: str | os.PathLike) -> str:
    return tcl_string(str(path).replace("\\", "/"))


TCL_JSON_HELPERS = r"""
proc mcp_json_escape {s} {
    set s [string map {\\ \\\\ \" \\\" \n \\n \r \\r \t \\t} $s]
    return "\"$s\""
}
proc mcp_json_list {lst} {
    set items {}
    foreach i $lst { lappend items [mcp_json_escape $i] }
    return "\[[join $items ,]\]"
}
proc mcp_json_dict {d} {
    set items {}
    dict for {k v} $d { lappend items "[mcp_json_escape $k]:$v" }
    return "\{[join $items ,]\}"
}
proc mcp_emit {json} {
    puts "\n%START%$json%END%"
}
""".replace("%START%", JSON_START).replace("%END%", JSON_END)


class VivadoAdapter(BaseAdapter):
    id = "vivado"
    name = "AMD Vivado"
    env_var = "VIVADO_EXE"
    exe_names = ["vivado", "vivado.bat"]
    exe_patterns = [
        "Xilinx/Vivado/20*/bin/vivado.bat",
        "AMD/Vivado/20*/bin/vivado.bat",
        "Xilinx/20*/Vivado/bin/vivado.bat",
        "/tools/Xilinx/Vivado/20*/bin/vivado",
        "/opt/Xilinx/Vivado/20*/bin/vivado",
    ]
    install_hint = "Install Vivado and set VIVADO_EXE to bin/vivado.bat (Windows) or bin/vivado."

    # ---- core ----------------------------------------------------------------------------------
    def run_tcl(self, script: str, is_file: bool = False, tclargs: list[str] | None = None,
                cwd: str | None = None, timeout: int | None = None, parse_json: bool = False) -> RunResult:
        """Run Tcl *script* (code or a file path when is_file) in batch mode and return the log."""
        if not self.is_available():
            return self.unavailable()
        if is_file:
            path = resolve_path(script)
            if not path.exists():
                return RunResult(ok=False, software=self.id, command="", error=f"File not found: {path}")
        else:
            path = self.write_temp_script(TCL_JSON_HELPERS + "\n" + script + "\n", ".tcl", "mcp_vivado")
        log = self.scripts_dir() / (path.stem + ".log")
        jou = self.scripts_dir() / (path.stem + ".jou")
        args = [self.executable() or "vivado", "-mode", "batch", "-source", str(path),
                "-log", str(log), "-journal", str(jou), "-notrace"]
        if tclargs:
            args += ["-tclargs", *tclargs]
        res = self.run_command(args, cwd=cwd or path.parent, timeout=timeout,
                               artifacts={"script": str(path), "log": str(log), "journal": str(jou)},
                               parse_json=parse_json)
        if not res.ok and not res.stdout:
            res.stdout = read_text_if_exists(log)
        return res

    # ---- flows ---------------------------------------------------------------------------------
    def project_info(self, project_file: str, timeout: int | None = None) -> RunResult:
        """Report part, top module, source files, IPs and run status of an .xpr project."""
        xpr = resolve_path(project_file)
        script = f"""
open_project {tcl_path(xpr)}
set d [dict create]
dict set d name [mcp_json_escape [get_property NAME [current_project]]]
dict set d directory [mcp_json_escape [get_property DIRECTORY [current_project]]]
dict set d part [mcp_json_escape [get_property PART [current_project]]]
dict set d board_part [mcp_json_escape [get_property BOARD_PART [current_project]]]
dict set d target_language [mcp_json_escape [get_property TARGET_LANGUAGE [current_project]]]
dict set d top [mcp_json_escape [get_property TOP [current_fileset]]]
dict set d sources [mcp_json_list [get_files -of_objects [get_filesets sources_1]]]
dict set d constraints [mcp_json_list [get_files -of_objects [get_filesets constrs_1]]]
dict set d ips [mcp_json_list [get_ips]]
set runs {{}}
foreach r [get_runs] {{
    set rd [dict create]
    dict set rd name [mcp_json_escape $r]
    dict set rd status [mcp_json_escape [get_property STATUS $r]]
    dict set rd progress [mcp_json_escape [get_property PROGRESS $r]]
    dict set rd needs_refresh [expr {{[get_property NEEDS_REFRESH $r] ? "true" : "false"}}]
    lappend runs [mcp_json_dict $rd]
}}
dict set d runs "\\[[join $runs ,]\\]"
mcp_emit [mcp_json_dict $d]
close_project
"""
        return self.run_tcl(script, timeout=timeout, parse_json=True)

    def build_project(self, project_file: str, run_name: str = "impl_1", to_step: str = "write_bitstream",
                      jobs: int = 4, reset_runs: bool = False, timeout: int | None = None) -> RunResult:
        """Synthesise + implement (+ bitstream) a project and report the results."""
        xpr = resolve_path(project_file)
        script = f"""
open_project {tcl_path(xpr)}
set synth [get_property PARENT [get_runs {run_name}]]
if {{{1 if reset_runs else 0}}} {{ reset_run $synth }}
if {{[get_property PROGRESS [get_runs $synth]] ne "100%"}} {{
    launch_runs $synth -jobs {int(jobs)}
    wait_on_run $synth
}}
if {{[get_property PROGRESS [get_runs $synth]] ne "100%"}} {{
    puts "ERROR: synthesis run $synth did not complete: [get_property STATUS [get_runs $synth]]"
    exit 1
}}
launch_runs {run_name} -to_step {to_step} -jobs {int(jobs)}
wait_on_run {run_name}
set d [dict create]
dict set d run [mcp_json_escape {run_name}]
dict set d status [mcp_json_escape [get_property STATUS [get_runs {run_name}]]]
dict set d progress [mcp_json_escape [get_property PROGRESS [get_runs {run_name}]]]
dict set d directory [mcp_json_escape [get_property DIRECTORY [get_runs {run_name}]]]
dict set d wns [mcp_json_escape [get_property STATS.WNS [get_runs {run_name}]]]
dict set d tns [mcp_json_escape [get_property STATS.TNS [get_runs {run_name}]]]
dict set d whs [mcp_json_escape [get_property STATS.WHS [get_runs {run_name}]]]
dict set d bitstreams [mcp_json_list [glob -nocomplain -directory [get_property DIRECTORY [get_runs {run_name}]] *.bit]]
mcp_emit [mcp_json_dict $d]
close_project
"""
        return self.run_tcl(script, timeout=timeout, parse_json=True)

    def reports(self, project_file: str, run_name: str = "impl_1", reports: list[str] | None = None,
                timeout: int | None = None) -> RunResult:
        """Open an implemented run and generate timing/utilization/power/DRC reports to files."""
        xpr = resolve_path(project_file)
        wanted = [r.lower() for r in (reports or ["timing", "utilization"])]
        out_dir = self.scripts_dir() / f"{xpr.stem}_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmds = {
            "timing": f"report_timing_summary -file {tcl_path(out_dir / 'timing_summary.rpt')} -max_paths 10",
            "utilization": f"report_utilization -file {tcl_path(out_dir / 'utilization.rpt')} -hierarchical",
            "power": f"report_power -file {tcl_path(out_dir / 'power.rpt')}",
            "drc": f"report_drc -file {tcl_path(out_dir / 'drc.rpt')}",
            "methodology": f"report_methodology -file {tcl_path(out_dir / 'methodology.rpt')}",
            "io": f"report_io -file {tcl_path(out_dir / 'io.rpt')}",
            "clocks": f"report_clocks -file {tcl_path(out_dir / 'clocks.rpt')}",
            "route_status": f"report_route_status -file {tcl_path(out_dir / 'route_status.rpt')}",
        }
        unknown = [w for w in wanted if w not in cmds]
        if unknown:
            return RunResult(ok=False, software=self.id, command="", error=f"Unknown report(s): {unknown}. Known: {list(cmds)}")
        script = f"open_project {tcl_path(xpr)}\nopen_run {run_name}\n" + "\n".join(cmds[w] for w in wanted) + "\nclose_project\n"
        res = self.run_tcl(script, timeout=timeout)
        files = {w: str(out_dir / Path(cmds[w].split("-file")[1].split()[0].strip("{}")).name) for w in wanted}
        res.artifacts["reports"] = files
        res.data = {w: read_text_if_exists(f, 60_000) for w, f in files.items()}
        return res

    def program_device(self, bitstream: str, probes_file: str | None = None, device_index: int = 0,
                       hw_server: str = "localhost:3121", timeout: int | None = None) -> RunResult:
        """Program the first (or *device_index*) JTAG device with a .bit file via Hardware Manager."""
        bit = resolve_path(bitstream)
        ltx = f"set_property PROBES.FILE {tcl_path(resolve_path(probes_file))} $dev" if probes_file else ""
        script = f"""
open_hw_manager
connect_hw_server -url {hw_server}
open_hw_target
set dev [lindex [get_hw_devices] {int(device_index)}]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
set_property PROGRAM.FILE {tcl_path(bit)} $dev
{ltx}
program_hw_devices $dev
refresh_hw_device $dev
mcp_emit [mcp_json_dict [dict create device [mcp_json_escape $dev] bitstream [mcp_json_escape {str(bit).replace(chr(92), '/')}] done true]]
close_hw_manager
"""
        return self.run_tcl(script, timeout=timeout, parse_json=True)

    def create_project(self, name: str, directory: str, part: str, sources: list[str],
                       constraints: list[str] | None = None, top: str | None = None,
                       timeout: int | None = None) -> RunResult:
        """Create a new RTL project from source/constraint files."""
        d = resolve_path(directory)
        src_list = " ".join(tcl_path(resolve_path(s)) for s in sources)
        xdc_list = " ".join(tcl_path(resolve_path(c)) for c in (constraints or []))
        script = f"""
create_project {tcl_string(name)} {tcl_path(d / name)} -part {tcl_string(part)} -force
add_files -norecurse [list {src_list}]
{f'add_files -fileset constrs_1 -norecurse [list {xdc_list}]' if xdc_list else ''}
update_compile_order -fileset sources_1
{f'set_property top {tcl_string(top)} [current_fileset]' if top else ''}
mcp_emit [mcp_json_dict [dict create project [mcp_json_escape [get_property DIRECTORY [current_project]]/{name}.xpr] top [mcp_json_escape [get_property TOP [current_fileset]]]]]
close_project
"""
        return self.run_tcl(script, timeout=timeout, parse_json=True)

    def run_hls(self, script: str, is_file: bool = False, cwd: str | None = None,
                timeout: int | None = None) -> RunResult:
        """Run a Vitis HLS Tcl script (``vitis_hls -f script.tcl``)."""
        exe = find_executable("VITIS_HLS_EXE", ["vitis_hls", "vitis_hls.bat", "vivado_hls"], [
            "Xilinx/Vitis_HLS/20*/bin/vitis_hls.bat", "AMD/Vitis_HLS/20*/bin/vitis_hls.bat",
            "/tools/Xilinx/Vitis_HLS/20*/bin/vitis_hls", "/opt/Xilinx/Vitis_HLS/20*/bin/vitis_hls"])
        if not exe:
            return RunResult(ok=False, software=self.id, command="", error="vitis_hls not found; set VITIS_HLS_EXE.")
        path = resolve_path(script) if is_file else self.write_temp_script(script, ".tcl", "mcp_hls")
        return self.run_command([exe, "-f", str(path)], cwd=cwd or path.parent, timeout=timeout,
                                artifacts={"script": str(path)})


vivado_adapter = VivadoAdapter()
