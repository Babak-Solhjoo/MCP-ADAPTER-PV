"""Altair Feko adapter (computational electromagnetics).

Verified against Feko 2026.1 on Windows (``C:\\Program Files\\Altair\\2026.1\\feko\\bin``):
* ``runfeko MODEL [-np N] [--use-gpu] [--priority x]`` – runs CADFEKO_BATCH/PREFEKO when needed and then the
  solver; writes ``MODEL.out`` (text report), ``MODEL.bof`` (binary results) and the requested exports
  (.ffe/.efe/.hfe/.os/.sNp). A bare ``runfeko`` prints the version banner and the usage text; ``--version`` or
  ``--help`` as the first argument is taken as a file name and fails.
* ``cadfeko_batch MODEL.cfx [-# VAR=VALUE ...] [--force-mesh]`` – batch processor for .cfx files: change model
  variables and re-mesh without the GUI. It does not run scripts.
* ``cadfeko [MODEL] --non-interactive --run-script SCRIPT.lua [--configure-script "a=1 b=[[x]]"]`` and the same
  switches for ``postfeko`` – run Lua automation scripts without user interaction (Feko sets the working
  directory to the script's folder, so scripts should use absolute paths).
* ``prefeko``, ``optfeko``, ``editfeko`` live next to ``runfeko``; there is no separate adaptfeko executable.

Only ``runfeko`` is needed for detection; the other executables are looked up next to it.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .base import BaseAdapter, RunResult, read_text_if_exists, resolve_path

MODEL_SUFFIXES = (".cfx", ".pre", ".fek", ".nfp")
EXPORT_SUFFIXES = (".ffe", ".efe", ".hfe", ".os", ".s1p", ".s2p", ".s3p", ".s4p", ".snp", ".csv")
_VERSION_RE = re.compile(r"RUNFEKO Version\s+(\S+)(?:\s+from\s+(\S+))?")


def _feko_home_patterns() -> list[str]:
    home = os.environ.get("FEKO_HOME") or os.environ.get("ALTAIR_HOME")
    if not home:
        return []
    return [os.path.join(home, "bin", "runfeko.exe"), os.path.join(home, "bin", "runfeko"),
            os.path.join(home, "feko", "bin", "runfeko.exe"), os.path.join(home, "feko", "bin", "runfeko")]


class FekoAdapter(BaseAdapter):
    id = "feko"
    name = "Altair Feko"
    env_var = "FEKO_EXE"
    exe_names = ["runfeko.exe", "runfeko"]
    exe_patterns = _feko_home_patterns() + [
        "Altair/20*/feko/bin/runfeko.exe",
        "Altair/*/feko/bin/runfeko.exe",
        "FEKO/*/bin/runfeko.exe",
        "/opt/altair/20*/feko/bin/runfeko",
        "/opt/feko/*/bin/runfeko",
    ]
    install_hint = "Install Altair Feko and set FEKO_EXE to runfeko.exe (…\\Altair\\<version>\\feko\\bin\\runfeko.exe)."

    # ---- helpers -------------------------------------------------------------------------------
    def sibling(self, name: str) -> str | None:
        """Path of another Feko executable (cadfeko, postfeko, cadfeko_batch, prefeko, optfeko) next to runfeko."""
        exe = self.executable()
        if not exe:
            return None
        folder = Path(exe).parent
        for candidate in (name + ".exe", name, name + ".bat"):
            p = folder / candidate
            if p.exists():
                return str(p)
        return None

    def _model(self, model: str) -> tuple[Path | None, str | None]:
        path = resolve_path(model)
        if not path.exists():
            return None, f"Model not found: {path}"
        if path.suffix.lower() not in MODEL_SUFFIXES:
            return None, f"Expected a .cfx, .pre, .fek or .nfp model, got {path.suffix}"
        return path, None

    # ---- core ----------------------------------------------------------------------------------
    def version(self, timeout: int | None = None) -> RunResult:
        """Version from the banner that a bare ``runfeko`` prints (the program has no --version switch)."""
        if not self.is_available():
            return self.unavailable()
        res = self.run_command([self.executable() or "runfeko"], timeout=timeout or 120)
        m = _VERSION_RE.search(res.stdout or "")
        if m:
            res.ok = True
            res.error = None
            res.data = {"version": m.group(1), "date": m.group(2), "executable": self.executable()}
        return res

    def solve(self, model: str, processes: int | None = None, use_gpu: bool = False, priority: int | None = None,
              extra_args: list[str] | None = None, timeout: int | None = None) -> RunResult:
        """Run ``runfeko`` on a model and return the parsed .out summary plus produced files."""
        if not self.is_available():
            return self.unavailable()
        path, err = self._model(model)
        if err:
            return RunResult(ok=False, software=self.id, command="", error=err)
        assert path is not None
        args = [self.executable() or "runfeko", path.name]
        if processes:
            args += ["-np", str(int(processes))]
        if use_gpu:
            args.append("--use-gpu")
        if priority is not None:
            args += ["--priority", str(int(priority))]
        args += list(extra_args or [])
        started = time.time()
        out_file = path.with_suffix(".out")
        res = self.run_command(args, cwd=path.parent, timeout=timeout, artifacts={"model": str(path)})
        if out_file.exists():
            res.artifacts["out"] = str(out_file)
            res.data = parse_out_text(read_text_if_exists(out_file, limit=4_000_000))
            if res.data.get("errors"):
                res.ok = False
                res.error = (res.error or "") + " Solver reported errors (see data.errors)."
        bof = path.with_suffix(".bof")
        if bof.exists():
            res.artifacts["bof"] = str(bof)
        produced = [str(p) for p in path.parent.iterdir()
                    if p.suffix.lower() in EXPORT_SUFFIXES and p.stat().st_mtime >= started - 1]
        if produced:
            res.artifacts["exports"] = ";".join(sorted(produced))
        return res

    def batch_process(self, model: str, variables: dict[str, str] | None = None, force_mesh: bool = False,
                      timeout: int | None = None) -> RunResult:
        """``cadfeko_batch``: set model variables (-# VAR=VALUE) and/or re-mesh a .cfx without the GUI."""
        if not self.is_available():
            return self.unavailable()
        exe = self.sibling("cadfeko_batch")
        if not exe:
            return RunResult(ok=False, software=self.id, command="", error=f"cadfeko_batch not found next to {self.executable()}")
        path = resolve_path(model)
        if not path.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"Model not found: {path}")
        if path.suffix.lower() != ".cfx":
            return RunResult(ok=False, software=self.id, command="", error=f"cadfeko_batch needs a .cfx model, got {path.suffix}")
        args = [exe, path.name]
        for key, value in (variables or {}).items():
            args += ["-#", f"{key}={value}"]
        if force_mesh:
            args.append("--force-mesh")
        res = self.run_command(args, cwd=path.parent, timeout=timeout, artifacts={"model": str(path)})
        if path.with_suffix(".cfm").exists():
            res.artifacts["mesh"] = str(path.with_suffix(".cfm"))
        return res

    def _run_script(self, app: str, script: str, is_file: bool, model: str | None, configure: str | None,
                    timeout: int | None, prefix: str) -> RunResult:
        if not self.is_available():
            return self.unavailable()
        exe = self.sibling(app)
        if not exe:
            return RunResult(ok=False, software=self.id, command="", error=f"{app} not found next to {self.executable()}")
        if is_file:
            path = resolve_path(script)
            if not path.exists():
                return RunResult(ok=False, software=self.id, command="", error=f"Script not found: {path}")
        else:
            path = self.write_temp_script(script, ".lua", prefix)
        args = [exe]
        cwd = None
        if model:
            mp = resolve_path(model)
            if not mp.exists():
                return RunResult(ok=False, software=self.id, command="", error=f"Model not found: {mp}")
            args.append(str(mp))
            cwd = mp.parent
        args += ["--non-interactive", "--run-script", str(path)]
        if configure:
            args += ["--configure-script", configure]
        return self.run_command(args, cwd=cwd, timeout=timeout, artifacts={"script": str(path)})

    def run_cadfeko_script(self, script: str, is_file: bool = False, model: str | None = None,
                           configure: str | None = None, timeout: int | None = None) -> RunResult:
        """Execute CADFEKO Lua (cf API) with ``cadfeko --non-interactive --run-script``; the script saves the project."""
        return self._run_script("cadfeko", script, is_file, model, configure, timeout, "mcp_cadfeko")

    def run_postfeko_script(self, script: str, is_file: bool = False, model: str | None = None,
                            configure: str | None = None, timeout: int | None = None) -> RunResult:
        """Execute POSTFEKO Lua (pf API) with ``postfeko --non-interactive --run-script`` to extract or export results."""
        return self._run_script("postfeko", script, is_file, model, configure, timeout, "mcp_postfeko")

    # ---- no executable needed --------------------------------------------------------------------
    def parse_out(self, out_file: str) -> RunResult:
        path = resolve_path(out_file)
        if not path.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {path}")
        data = parse_out_text(read_text_if_exists(path, limit=4_000_000))
        return RunResult(ok=not data["errors"], software=self.id, command=f"parse {path.name}", data=data,
                         artifacts={"out": str(path)})


_FLOAT = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
_SOURCE_KEYS = ("Current in A", "Admittance in A/V", "Impedance in Ohm", "Power in W", "Voltage in V")
_SUMMARY_WORDS = ("unknowns", "triangles", "segments", "tetrahedra", "voxels", "memory", "cpu-time", "cpu time",
                  "total time", "wall time", "number of", "solution of the linear", "basis functions")


_FREQ_RE = re.compile(r"(?:FREQ\s*=\s*|[Ff]requency\s*[:=]?\s*)(" + _FLOAT + r")(?:\s*Hz)?")
_FF_ROW_RE = re.compile(r"^\s*" + r"\s+".join([_FLOAT] * 9))


def _is_heading(s: str) -> bool:
    """Feko prints section titles as centred all-caps lines; table rows and process lists are excluded."""
    if s != s.upper() or len(s) < 12 or "|" in s or s[0].isdigit() or s.startswith("*") or re.match(r"^[A-Z]{2}:", s):
        return False
    return len(re.findall(r"[A-Z]{3,}", s)) >= 2 and not re.search(_FLOAT + r"\s*$", s)


def parse_out_text(text: str) -> dict:
    """Extract the useful parts of a Feko .out report by keyword, independent of column layout.

    Verified against a Feko 2026.1 report: frequencies (``FREQ = ...``), per-source impedance/current/power blocks
    (``DATA OF THE VOLTAGE SOURCE NO. n``), far-field tables (max directivity and its direction, sample count,
    gain offset, active power), mesh/memory/time summary lines, warnings and errors.
    """
    lines = text.splitlines()
    sections: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    errors: list[str] = []
    summary: list[str] = []
    frequencies: list[float] = []
    sources: list[dict] = []
    far_fields: list[dict] = []
    current: dict | None = None
    ff: dict | None = None
    in_ff_table = False
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        upper = s.upper()
        if upper.startswith("WARNING"):
            warnings.append(s[:300])
        elif upper.startswith("ERROR"):
            errors.append(s[:300])
        if _is_heading(s) and s not in seen:
            seen.add(s)
            sections.append(s)
        m = _FREQ_RE.search(s)
        if m and ("FREQ" in s or "Hz" in s):
            f = float(m.group(1))
            if not frequencies or frequencies[-1] != f:
                frequencies.append(f)
        m = re.match(r"DATA OF THE (\w[\w ]*?SOURCE)\s*(?:NO\.?\s*)?(\d+)", upper)
        if m:
            current = {"kind": m.group(1).title(), "number": int(m.group(2)),
                       "frequency_hz": frequencies[-1] if frequencies else None}
            sources.append(current)
            continue
        if current is not None:
            for key in _SOURCE_KEYS:
                if s.startswith(key):
                    nums = [float(x) for x in re.findall(_FLOAT, s[len(key):])]
                    if nums:
                        entry = {"real": nums[0]}
                        if len(nums) > 1:
                            entry["imag"] = nums[1]
                        if len(nums) > 2:
                            entry["magnitude"] = nums[2]
                        if len(nums) > 3:
                            entry["phase_deg"] = nums[3]
                        current[key.split(" in ")[0].lower()] = entry
                    break
        m = re.match(r"Far field request with name:\s*(\S+)", s)
        if m:
            ff = {"request": m.group(1), "frequency_hz": frequencies[-1] if frequencies else None, "samples": 0,
                  "max_directivity_dbi": None, "max_direction_deg": None}
            far_fields.append(ff)
            in_ff_table = False
            continue
        if ff is not None:
            if s.startswith("THETA") and "PHI" in s:
                in_ff_table = True
                continue
            if in_ff_table:
                row = _FF_ROW_RE.match(s)
                if row:
                    nums = [float(x) for x in re.findall(_FLOAT, s)[:9]]
                    total = nums[8]
                    ff["samples"] += 1
                    if total > -900 and (ff["max_directivity_dbi"] is None or total > ff["max_directivity_dbi"]):
                        ff["max_directivity_dbi"] = total
                        ff["max_direction_deg"] = {"theta": nums[0], "phi": nums[1]}
                    continue
                in_ff_table = False
            m = re.search(r"Gain is a factor of\s*(" + _FLOAT + r")\s*\(\s*(" + _FLOAT + r")\s*dB\)", s)
            if m:
                ff["gain_minus_directivity_db"] = float(m.group(2))
            m = re.search(r"active power of\s*(" + _FLOAT + r")\s*W", s)
            if m:
                ff["active_power_w"] = float(m.group(1))
        low = s.lower()
        if any(w in low for w in _SUMMARY_WORDS) and re.search(_FLOAT, s) and len(summary) < 60 and "|" not in s:
            summary.append(s[:200])
    return {
        "frequencies_hz": frequencies[:500],
        "sources": sources[:200],
        "far_fields": far_fields[:50],
        "sections": sections[:200],
        "summary": summary,
        "warnings": warnings[:100],
        "errors": errors[:100],
        "line_count": len(lines),
    }


LUA_TEMPLATES: dict[str, str] = {
    "dipole": """-- CADFEKO: half-wave wire dipole, voltage source, 3D far field; saves and solves.
-- Run with: cadfeko --non-interactive --run-script this_file.lua
-- cf.Point and numeric properties take numbers (not expression strings), so values are computed in Lua.
local freq = %FREQ%
local lam = 299792458 / freq
local len = 0.47 * lam
app = cf.GetApplication()
project = app:NewProject()
project.Variables:Add("freq", tostring(freq))
project.Variables:Add("lam", "c0/freq")
project.Variables:Add("len", "0.47*lam")
wire = project.Geometry:AddLine(cf.Point(0, 0, -len / 2), cf.Point(0, 0, len / 2))
wire.Label = "Dipole"
port = project.Ports:AddWirePort(wire.Wires[1])
port.Location = cf.Enums.WirePortLocationEnum.Middle
cfg = project.SolutionConfigurations[1]
src = cfg.Sources:AddVoltageSource(port)
src.Impedance = 50
cfg.Frequency.Start = freq  -- the frequency belongs to the solution configuration
ff = cfg.FarFields:Add3DPattern()
project.Mesher.Settings.WireRadius = lam / 400
project.Mesher:Mesh()
app:SaveAs([[%MODEL%]])
project.Launcher:RunFEKO()
""",
    "export_source_data": """-- POSTFEKO: write frequency, input impedance (re/im) and power of excitation 1 to CSV. Verified with Feko 2026.1.
-- Run with: postfeko --non-interactive --run-script this_file.lua
app = pf.GetApplication()
app:OpenFile([[%MODEL%]])
cfg = app.Models[1].Configurations[1]
-- Excitations (not Sources); quantities: Impedance, Admittance, Voltage, Current, Power, MismatchLoss, SourceType
ds = cfg.Excitations[1]:GetDataSet()
f = io.open([[%OUTPUT%]], "w")
f:write("frequency_hz,z_real_ohm,z_imag_ohm,power_w\\n")
for i = 1, ds.Axes["Frequency"].Count do
  local z = ds[i].Impedance
  f:write(string.format("%g,%g,%g,%g\\n", ds.Axes["Frequency"]:ValueAt(i), z.re, z.im, ds[i].Power))
end
f:close()
""",
    "export_farfield": """-- POSTFEKO: write the far-field pattern of request 1 (theta, phi, directivity/gain/realised gain in dBi) to CSV.
-- Verified with Feko 2026.1. Run with: postfeko --non-interactive --run-script this_file.lua
app = pf.GetApplication()
app:OpenFile([[%MODEL%]])
cfg = app.Models[1].Configurations[1]
-- axes Frequency/Theta/Phi (indexed ds[f][t][p]); quantities EFieldTheta/EFieldPhi (complex, V) and the constants
-- DirectivityFactor/GainFactor/RealisedGainFactor: directivity = (|Etheta|^2 + |Ephi|^2) * DirectivityFactor, etc.
ds = cfg.FarFields[1]:GetDataSet()
local nf, nt, np = ds.Axes["Frequency"].Count, ds.Axes["Theta"].Count, ds.Axes["Phi"].Count
local function db(x) if x and x > 0 then return 10 * math.log10(x) else return -999 end end
f = io.open([[%OUTPUT%]], "w")
f:write("frequency_hz,theta_deg,phi_deg,directivity_dbi,gain_dbi,realised_gain_dbi\\n")
for i = 1, nf do
  for j = 1, nt do
    for k = 1, np do
      local v = ds[i][j][k]
      local et, ep = v.EFieldTheta, v.EFieldPhi
      local e2 = et.re * et.re + et.im * et.im + ep.re * ep.re + ep.im * ep.im
      f:write(string.format("%g,%g,%g,%.3f,%.3f,%.3f\\n", ds.Axes["Frequency"]:ValueAt(i), ds.Axes["Theta"]:ValueAt(j),
              ds.Axes["Phi"]:ValueAt(k), db(e2 * v.DirectivityFactor), db(e2 * v.GainFactor), db(e2 * v.RealisedGainFactor)))
    end
  end
end
f:close()
""",
    "rcs_sweep": """-- CADFEKO: monostatic RCS of an existing model over phi with a plane wave (run with the model as argument).
-- Run with: cadfeko model.cfx --non-interactive --run-script this_file.lua
app = cf.GetApplication()
project = app.Project
cfg = project.SolutionConfigurations[1]
pw = cfg.Sources:AddPlaneWave(90, 0)          -- theta, phi of the first incidence direction
pw.StartTheta = 90
pw.EndTheta = 90
pw.ThetaIncrement = 0
pw.StartPhi = 0                                -- loop over phi for the monostatic sweep
pw.EndPhi = 180
pw.PhiIncrement = 2
ff = cfg.FarFields:AddRequestInPlaneWaveIncidentDirection()  -- backscatter direction = monostatic RCS
project.Mesher:Mesh()
app:Save()
project.Launcher:RunFEKO()
""",
}


def lua_template(kind: str, model: str = "D:/feko/model.cfx", output: str = "D:/feko/result.csv",
                 frequency_hz: float = 300e6) -> str:
    if kind not in LUA_TEMPLATES:
        raise KeyError(f"Unknown template {kind!r}; choose one of {', '.join(LUA_TEMPLATES)}")
    return (LUA_TEMPLATES[kind].replace("[[%MODEL%]]", lua_long_string(model.replace("\\", "/")))
            .replace("[[%OUTPUT%]]", lua_long_string(output.replace("\\", "/")))
            .replace("%FREQ%", repr(float(frequency_hz))))


def lua_long_string(text: str) -> str:
    """A Lua long-bracket literal whose level is chosen so that *text* cannot close it (e.g. a path with ']]')."""
    level = 0
    while "]" + "=" * level + "]" in text:
        level += 1
    return "[" + "=" * level + "[" + text + "]" + "=" * level + "]"


feko_adapter = FekoAdapter()
