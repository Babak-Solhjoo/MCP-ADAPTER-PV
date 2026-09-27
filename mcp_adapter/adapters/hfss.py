"""Ansys HFSS / Electronics Desktop adapter.

Headless entry points:
* ``ansysedt.exe -ng -RunScriptAndExit script.py [project.aedt]`` – IronPython/CPython scripts using
  the oDesktop/oProject/oDesign object model (what "Tools > Record Script" produces);
* ``ansysedt.exe -ng -BatchSolve [design:setup] project.aedt`` – solve without the GUI;
* PyAEDT (``pip install pyaedt``) – a Pythonic API; used when installed.
"""
from __future__ import annotations

import json
import sys
import time

from .base import BaseAdapter, RunResult, read_text_if_exists, resolve_path

SCRIPT_PRELUDE = r"""
import json
import ScriptEnv
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
oDesktop.RestoreWindow()
mcp_result_file = %RESULT%
def mcp_result(obj):
    f = open(mcp_result_file, "w")
    f.write(json.dumps(obj, default=str))
    f.close()
"""


class HFSSAdapter(BaseAdapter):
    id = "hfss"
    name = "Ansys HFSS (Electronics Desktop)"
    env_var = "ANSYS_EDT_EXE"
    exe_names = ["ansysedt.exe", "ansysedt"]
    exe_patterns = [
        "AnsysEM/v*/Win64/ansysedt.exe",
        "ANSYS Inc/v*/AnsysEM/Win64/ansysedt.exe",
        "AnsysEM/AnsysEM*/Win64/ansysedt.exe",
        "/opt/AnsysEM/v*/Linux64/ansysedt",
        "/ansys_inc/v*/AnsysEM/Linux64/ansysedt",
    ]
    install_hint = "Install Ansys Electronics Desktop and set ANSYS_EDT_EXE to ansysedt.exe."

    # ---- core ----------------------------------------------------------------------------------
    def run_script(self, script: str, project: str | None = None, is_file: bool = False,
                   non_graphical: bool = True, script_args: str | None = None, beta_features: bool = False,
                   timeout: int | None = None) -> RunResult:
        """Run an AEDT IronPython script and exit. Scripts get ``mcp_result(obj)`` to return JSON."""
        if not self.is_available():
            return self.unavailable()
        result_file = self.scripts_dir() / f"result_{int(time.time() * 1000)}.json"
        if is_file:
            path = resolve_path(script)
            if not path.exists():
                return RunResult(ok=False, software=self.id, command="", error=f"File not found: {path}")
        else:
            body = SCRIPT_PRELUDE.replace("%RESULT%", json.dumps(str(result_file))) + "\n" + script + "\n"
            path = self.write_temp_script(body, ".py", "mcp_aedt")
        args = [self.executable() or "ansysedt"]
        if non_graphical:
            args.append("-ng")
        if beta_features:
            args.append("-features=beta")
        if script_args:
            args += ["-ScriptArgs", script_args]
        args += ["-RunScriptAndExit", str(path)]
        cwd = None
        if project:
            prj = resolve_path(project)
            args.append(str(prj))
            cwd = prj.parent
        res = self.run_command(args, cwd=cwd, timeout=timeout,
                               artifacts={"script": str(path), "result_file": str(result_file)})
        if result_file.exists():
            try:
                res.data = json.loads(result_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                res.data = {"raw": read_text_if_exists(result_file)}
        return res

    def batch_solve(self, project: str, design: str | None = None, setup: str | None = None,
                    cores: int | None = None, distributed: bool = False, machinelist: str | None = None,
                    batch_options: dict[str, str] | None = None, timeout: int | None = None) -> RunResult:
        """Solve a project (optionally one design:setup) with -BatchSolve."""
        if not self.is_available():
            return self.unavailable()
        prj = resolve_path(project)
        if not prj.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"Project not found: {prj}")
        args = [self.executable() or "ansysedt", "-ng"]
        if distributed:
            args.append("-Distributed")
        if machinelist:
            args += ["-machinelist", machinelist]
        elif cores:
            args += ["-machinelist", f"list=localhost:-1:{int(cores)}:90%"]
        for k, v in (batch_options or {}).items():
            args += ["-batchoptions", f"{k}={v}"]
        args.append("-BatchSolve")
        if design and setup:
            args.append(f"{design}:{setup}")
        elif design:
            args.append(design)
        args.append(str(prj))
        log = prj.with_suffix(".log")
        res = self.run_command(args, cwd=prj.parent, timeout=timeout, artifacts={"log": str(log)})
        if log.exists():
            res.data = {"log_tail": read_text_if_exists(log)[-6000:]}
        return res

    # ---- ready-made scripts ---------------------------------------------------------------------
    def project_info(self, project: str, timeout: int | None = None) -> RunResult:
        """List designs, variables, setups/sweeps, boundaries and excitations of a project."""
        script = r"""
oProject = oDesktop.GetActiveProject()
info = {"project": oProject.GetName(), "path": oProject.GetPath(), "designs": []}
for name in oProject.GetDesigns():
    oDesign = oProject.SetActiveDesign(name)
    d = {"name": name, "type": oDesign.GetDesignType(), "solution_type": ""}
    try:
        d["solution_type"] = oDesign.GetSolutionType()
    except Exception:
        pass
    try:
        d["variables"] = dict((v, oDesign.GetVariableValue(v)) for v in oDesign.GetVariables())
    except Exception:
        d["variables"] = {}
    try:
        oModule = oDesign.GetModule("AnalysisSetup")
        d["setups"] = dict((s, list(oModule.GetSweeps(s))) for s in oModule.GetSetups())
    except Exception:
        d["setups"] = {}
    try:
        oBnd = oDesign.GetModule("BoundarySetup")
        d["boundaries"] = list(oBnd.GetBoundaries())
        d["excitations"] = list(oBnd.GetExcitations())
    except Exception:
        pass
    try:
        oEditor = oDesign.SetActiveEditor("3D Modeler")
        d["solids"] = list(oEditor.GetObjectsInGroup("Solids"))
        d["sheets"] = list(oEditor.GetObjectsInGroup("Sheets"))
    except Exception:
        pass
    info["designs"].append(d)
try:
    info["project_variables"] = dict((v, oProject.GetVariableValue(v)) for v in oProject.GetVariables())
except Exception:
    pass
mcp_result(info)
"""
        return self.run_script(script, project=project, timeout=timeout)

    def set_variables_and_solve(self, project: str, design: str, variables: dict[str, str],
                                setup: str | None = None, save: bool = True, timeout: int | None = None) -> RunResult:
        """Change design variables (e.g. {"L": "12mm"}) then analyze a setup (or all setups)."""
        var_lines = "\n".join(
            f'oDesign.ChangeProperty(["NAME:AllTabs", ["NAME:LocalVariableTab", ["NAME:PropServers", "LocalVariables"], '
            f'["NAME:ChangedProps", ["NAME:{k}", "Value:=", {json.dumps(v)}]]]])'
            for k, v in variables.items()
        )
        analyze = f'oDesign.Analyze({json.dumps(setup)})' if setup else "oDesign.AnalyzeAll()"
        script = f"""
oProject = oDesktop.GetActiveProject()
oDesign = oProject.SetActiveDesign({json.dumps(design)})
{var_lines}
{analyze}
{"oProject.Save()" if save else ""}
mcp_result({{"design": {json.dumps(design)}, "variables": {json.dumps(variables)}, "solved": True}})
"""
        return self.run_script(script, project=project, timeout=timeout)

    def export_touchstone(self, project: str, design: str, setup: str, sweep: str, output_file: str,
                          timeout: int | None = None) -> RunResult:
        """Export S-parameters of *setup : sweep* to a Touchstone file (.sNp)."""
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        script = f"""
oProject = oDesktop.GetActiveProject()
oDesign = oProject.SetActiveDesign({json.dumps(design)})
oModule = oDesign.GetModule("Solutions")
oModule.ExportNetworkData("", [{json.dumps(setup + " : " + sweep)}], 3, {json.dumps(str(out))},
    ["All"], True, 50, "S", -1, 0, 15, True, False, False)
mcp_result({{"touchstone": {json.dumps(str(out))}}})
"""
        res = self.run_script(script, project=project, timeout=timeout)
        res.artifacts["touchstone"] = str(out)
        return res

    def export_report_csv(self, project: str, design: str, report_name: str, output_file: str,
                          timeout: int | None = None) -> RunResult:
        """Export an existing report (Results tree) to CSV via ReportSetup.ExportToFile."""
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        script = f"""
oProject = oDesktop.GetActiveProject()
oDesign = oProject.SetActiveDesign({json.dumps(design)})
oModule = oDesign.GetModule("ReportSetup")
oModule.ExportToFile({json.dumps(report_name)}, {json.dumps(str(out))}, False)
mcp_result({{"csv": {json.dumps(str(out))}}})
"""
        res = self.run_script(script, project=project, timeout=timeout)
        res.artifacts["csv"] = str(out)
        return res

    def run_pyaedt(self, code: str, project: str | None = None, design: str | None = None,
                   version: str | None = None, non_graphical: bool = True, timeout: int | None = None) -> RunResult:
        """Run Python that uses PyAEDT; ``hfss`` (an ansys.aedt.core.Hfss instance) is pre-created."""
        try:
            import ansys.aedt.core  # noqa: F401
        except ImportError:
            try:
                import pyaedt  # noqa: F401
            except ImportError:
                return RunResult(ok=False, software=self.id, command="", error="PyAEDT is not installed: pip install pyaedt")
        kwargs = [f"non_graphical={non_graphical}", "new_desktop=True", "close_on_exit=True"]
        if project:
            kwargs.append(f"project={str(resolve_path(project))!r}")
        if design:
            kwargs.append(f"design={design!r}")
        if version:
            kwargs.append(f"version={version!r}")
        script = (
            "import json, sys\n"
            "try:\n    from ansys.aedt.core import Hfss\nexcept ImportError:\n    from pyaedt import Hfss\n"
            f"hfss = Hfss({', '.join(kwargs)})\n"
            "try:\n" + "\n".join("    " + ln for ln in code.splitlines()) + "\n"
            "finally:\n    hfss.release_desktop(close_projects=False, close_desktop=True)\n"
        )
        path = self.write_temp_script(script, ".py", "mcp_pyaedt")
        return self.run_command([sys.executable, str(path)], timeout=timeout, artifacts={"script": str(path)})


hfss_adapter = HFSSAdapter()
