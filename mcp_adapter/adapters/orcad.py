"""Cadence OrCAD adapter: PSpice batch simulation, Capture Tcl scripting, output parsing.

Environment variables:
  PSPICE_EXE          path to pspice.exe (default search: Cadence/SPB_*/tools/bin/pspice.exe)
  PSPICE_ARGS         override the batch flag(s) used to run a circuit (default "-r")
  ORCAD_CAPTURE_EXE   path to capture.exe
  ORCAD_CAPTURE_TCL_CMD optional command template to run a Tcl file at Capture start-up, e.g.
                      "{exe} -tcl {script}" (the exact switch depends on the OrCAD release; when unset the
                      adapter writes the script and tells you to `source` it in the Capture command window)
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..config import env
from .base import BaseAdapter, RunResult, read_text_if_exists, resolve_path


class PSpiceAdapter(BaseAdapter):
    id = "orcad"
    name = "OrCAD PSpice"
    env_var = "PSPICE_EXE"
    exe_names = ["pspice.exe", "pspice"]
    exe_patterns = [
        "Cadence/SPB_*/tools/bin/pspice.exe",
        "Cadence/SPB_*/tools/pspice/pspice.exe",
        "Cadence/OrCAD_*/tools/bin/pspice.exe",
        "OrCAD/OrCAD_*/tools/bin/pspice.exe",
    ]
    install_hint = "Install OrCAD/PSpice and set PSPICE_EXE to pspice.exe."

    def simulate(self, circuit_file: str, extra_args: list[str] | None = None,
                 timeout: int | None = None) -> RunResult:
        """Run a PSpice netlist/circuit file (.cir/.net/.sim) in batch mode and parse the .out file."""
        if not self.is_available():
            return self.unavailable()
        src = resolve_path(circuit_file)
        if not src.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {src}")
        flags = (env("PSPICE_ARGS") or "-r").split()
        args = [self.executable() or "pspice.exe", *flags, str(src), *(extra_args or [])]
        out_file = src.with_suffix(".out")
        dat_file = src.with_suffix(".dat")
        res = self.run_command(args, cwd=src.parent, timeout=timeout,
                               artifacts={"out_file": str(out_file), "dat_file": str(dat_file)})
        if out_file.exists():
            parsed = parse_pspice_output(out_file)
            res.data = parsed
            if parsed["errors"]:
                res.ok = False
                res.error = f"{len(parsed['errors'])} PSpice error(s); see data.errors"
        return res

    def simulate_netlist(self, netlist: str, name: str = "circuit", timeout: int | None = None) -> RunResult:
        """Write *netlist* text (PSpice syntax, ending with .END) to a .cir file and simulate it."""
        if ".end" not in netlist.lower():
            netlist = netlist.rstrip() + "\n.END\n"
        path = self.scripts_dir() / f"{re.sub(r'[^A-Za-z0-9_-]', '_', name)}.cir"
        path.write_text(netlist, encoding="utf-8")
        res = self.simulate(str(path), timeout=timeout)
        res.artifacts["circuit_file"] = str(path)
        return res


class CaptureAdapter(BaseAdapter):
    id = "orcad"
    name = "OrCAD Capture"
    env_var = "ORCAD_CAPTURE_EXE"
    exe_names = ["capture.exe"]
    exe_patterns = [
        "Cadence/SPB_*/tools/bin/capture.exe",
        "Cadence/SPB_*/tools/capture/capture.exe",
        "Cadence/OrCAD_*/tools/bin/capture.exe",
        "OrCAD/OrCAD_*/tools/bin/capture.exe",
    ]
    install_hint = "Install OrCAD Capture and set ORCAD_CAPTURE_EXE to capture.exe."

    def open_design(self, design_file: str) -> RunResult:
        """Launch Capture with a .dsn/.opj file (detached)."""
        if not self.is_available():
            return self.unavailable()
        p = resolve_path(design_file)
        return self.launch_detached([self.executable() or "capture.exe", str(p)], cwd=str(p.parent))

    def run_tcl(self, script: str, is_file: bool = False, timeout: int | None = None) -> RunResult:
        """Run (or prepare) a Capture Tcl script.

        If ORCAD_CAPTURE_TCL_CMD is configured the script is executed through it, otherwise the
        script is written to disk and the result explains how to `source` it in the Command Window.
        """
        path = resolve_path(script) if is_file else self.write_temp_script(script, ".tcl", "mcp_capture")
        template = env("ORCAD_CAPTURE_TCL_CMD")
        if template:
            exe = self.executable() or "capture.exe"
            cmd = template.format(exe=exe, script=str(path))
            args = [a.strip('"') for a in re.findall(r'"[^"]*"|\S+', cmd)]
            return self.run_command(args, timeout=timeout, artifacts={"script": str(path)})
        tcl_path = str(path).replace("\\", "/")
        return RunResult(
            ok=True, software=self.id, command="", returncode=None,
            stdout=(
                "Tcl script written. OrCAD Capture executes Tcl from its Command Window "
                "(View > Toolbar > Command Window). Run:\n"
                f"    source {{{tcl_path}}}\n"
                "Set ORCAD_CAPTURE_TCL_CMD (e.g. \"{exe} -tcl {script}\") to run scripts automatically at start-up "
                "if your OrCAD release supports it."
            ),
            artifacts={"script": str(path)},
        )


def parse_pspice_output(out_file: str | os.PathLike) -> dict[str, Any]:
    """Extract errors, warnings, bias-point node voltages and job statistics from a PSpice .out file."""
    text = read_text_if_exists(out_file)
    errors = [ln.strip() for ln in text.splitlines() if "ERROR" in ln.upper()]
    warnings = [ln.strip() for ln in text.splitlines() if "WARNING" in ln.upper()]
    node_voltages: dict[str, float] = {}
    in_bias = False
    for ln in text.splitlines():
        if "SMALL SIGNAL BIAS SOLUTION" in ln:
            in_bias = True
            continue
        if in_bias and ("VOLTAGE SOURCE CURRENTS" in ln or "TOTAL POWER DISSIPATION" in ln):
            in_bias = False
        if in_bias:
            for m in re.finditer(r"\(\s*([^)\s]+)\s*\)\s+(-?[\d.]+(?:E[+-]?\d+)?)", ln, flags=re.IGNORECASE):
                try:
                    node_voltages[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
    power = re.search(r"TOTAL POWER DISSIPATION\s+(-?[\d.]+E[+-]?\d+)\s+WATTS", text, flags=re.IGNORECASE)
    return {
        "errors": errors,
        "warnings": warnings,
        "node_voltages": node_voltages,
        "total_power_w": float(power.group(1)) if power else None,
        "has_probe_data": ".dat" in text.lower() or "PROBE" in text,
        "out_excerpt": text[-4000:],
    }


pspice_adapter = PSpiceAdapter()
capture_adapter = CaptureAdapter()
