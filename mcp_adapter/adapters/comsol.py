"""COMSOL Multiphysics adapter.

Headless entry points (verified with COMSOL 6.4 on Windows):
* ``comsolbatch -inputfile model.mph|Model.class -outputfile out.mph -batchlog log`` – solve a model file or run a
  compiled Java model program (a model built by a class is saved as ``<out>_<ModelTag>.mph``);
* ``comsolcompile Model.java`` – compile a COMSOL Java API program (exit code is 0 even on failure, so the adapter
  checks the compiler message and the .class file);
* generated Java programs (``comsol_java``) inspect and evaluate solved models without any Python bridge;
* the installation index (``comsol_library``) exposes installed modules, User's Guides and ~2,000 Application
  Library examples with their documented Java build scripts;
* the MPh Python library (``pip install mph``) – optional, for Python scripting of the Java API.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

from .base import BaseAdapter, RunResult, extract_json_payload, resolve_path
from .comsol_java import EVAL_TYPES, evaluate_java, summary_java
from .comsol_library import find_example, find_root, load_index


class ComsolAdapter(BaseAdapter):
    id = "comsol"
    name = "COMSOL Multiphysics"
    env_var = "COMSOL_EXE"
    exe_names = ["comsolbatch", "comsolbatch.exe", "comsol", "comsol.exe"]
    exe_patterns = [
        "COMSOL/COMSOL*/Multiphysics/bin/win64/comsolbatch.exe",
        "COMSOL/COMSOL*/Multiphysics/bin/win64/comsol.exe",
        "COMSOL*/Multiphysics/bin/comsol",
        "/usr/local/comsol*/multiphysics/bin/comsol",
    ]
    install_hint = "Point COMSOL_EXE at comsolbatch.exe (Windows) or the comsol launcher (Linux/macOS)."

    def _base_args(self) -> list[str]:
        exe = self.executable() or "comsolbatch"
        stem = Path(exe).stem.lower()
        return [exe] if stem.startswith("comsolbatch") else [exe, "batch"]

    def run_batch(self, input_file: str, output_file: str | None = None, study: str | None = None,
                  parameters: dict[str, str | float] | None = None, cores: int | None = None,
                  batch_log: str | None = None, extra_args: list[str] | None = None,
                  timeout: int | None = None) -> RunResult:
        """Solve *input_file* (.mph or compiled .class) headless.

        parameters: {"L": "10[mm]", "T0": 293.15} -> passed as -pname/-plist (only works for models that
        set up a parametric sweep or read them via the Batch job; otherwise use run_mph_python).
        """
        if not self.is_available():
            return self.unavailable()
        src = resolve_path(input_file)
        if not src.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {src}")
        out = resolve_path(output_file) if output_file else self.scripts_dir() / f"{src.stem}_solved.mph"
        log = resolve_path(batch_log) if batch_log else self.scripts_dir() / f"{src.stem}_batch.log"
        args = self._base_args() + ["-inputfile", str(src), "-outputfile", str(out), "-batchlog", str(log)]
        if study:
            args += ["-study", study]
        if parameters:
            args += ["-pname", ",".join(parameters.keys()), "-plist", ",".join(str(v) for v in parameters.values())]
        if cores:
            args += ["-np", str(cores)]
        args += list(extra_args or [])
        res = self.run_command(args, cwd=src.parent, timeout=timeout,
                               artifacts={"output_file": str(out), "batch_log": str(log)})
        # A model built by a Java class is saved as <output>_<ModelTag>.mph; report the file COMSOL really wrote.
        log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        saved = re.findall(r"Saving model:\s*(.+?\.mph)\s*$", log_text, flags=re.MULTILINE)
        if saved:
            res.artifacts["output_file"] = saved[-1].strip()
        elif not out.exists():
            res.artifacts.pop("output_file", None)
        if log_text:
            res.artifacts["batch_log"] = str(log)
            # comsolbatch exits with code 0 after model errors; the log carries an /*****Error****/ block
            m = re.search(r"/\*+Error\*+/\s*/\*+/\s*(.*?)(?:Total time:|Saving model:|$)", log_text, flags=re.S)
            if m:
                res.ok = False
                res.error = " ".join(m.group(1).split())[:800] or "COMSOL reported an error (see batch log)"
            errors = [ln.strip() for ln in log_text.splitlines() if ln.strip().startswith(("ERROR", "Error:", "Exception"))]
            if errors:
                res.data = {**(res.data or {}), "log_errors": errors[:20]}
        return res

    def run_method(self, input_file: str, method_name: str, output_file: str | None = None,
                   timeout: int | None = None) -> RunResult:
        """Call a model method defined in the Application Builder (-methodcall)."""
        return self.run_batch(input_file, output_file, extra_args=["-methodcall", method_name], timeout=timeout)

    def compile_java(self, java_file: str, timeout: int | None = None) -> RunResult:
        """Compile a COMSOL Java API program with ``comsol compile`` (then run it with run_batch)."""
        if not self.is_available():
            return self.unavailable()
        src = resolve_path(java_file)
        exe = self.executable() or "comsol"
        launcher = exe
        if Path(exe).stem.lower().startswith("comsolbatch"):
            candidate = Path(exe).with_name("comsolcompile.exe" if os.name == "nt" else "comsolcompile")
            launcher = str(candidate) if candidate.exists() else str(Path(exe).with_name(Path(exe).name.replace("batch", "")))
            args = [launcher, str(src)]
        else:
            args = [launcher, "compile", str(src)]
        class_file = src.with_suffix(".class")
        started = time.time()
        res = self.run_command(args, cwd=src.parent, timeout=timeout, artifacts={"class_file": str(class_file)})
        # comsolcompile exits with code 0 even when compilation fails; trust its message and the .class file
        text = f"{res.stdout}\n{res.stderr}"
        fresh = class_file.exists() and class_file.stat().st_mtime >= started - 1
        if "Compilation failed" in text or "Failed to compile" in text or not fresh:
            res.ok = False
            res.error = ("Java compilation failed; see stdout for the compiler message"
                         if "Compilation failed" in text or "Failed to compile" in text
                         else "comsolcompile produced no new .class file")
        return res

    def run_mph_python(self, code: str, model_file: str | None = None, timeout: int | None = None) -> RunResult:
        """Run Python that uses the MPh library. The variables ``client`` and ``model`` are pre-created.

        Example code:
            model.parameter('L', '12[mm]')
            model.solve()
            print(model.evaluate('T', 'K'))
            model.save('result.mph')
        """
        try:
            import mph  # noqa: F401
        except ImportError:
            return RunResult(ok=False, software=self.id, command="",
                             error="The MPh library is not installed: pip install mph")
        prelude = ["import json, sys", "import mph", "client = mph.start()"]
        if model_file:
            prelude.append(f"model = client.load({str(resolve_path(model_file))!r})")
        else:
            prelude.append("model = None")
        script = "\n".join(prelude) + "\n" + code + "\n"
        path = self.write_temp_script(script, ".py", "mcp_mph")
        return self.run_command([sys.executable, str(path)], timeout=timeout, artifacts={"script": str(path)})

    # ---- COMSOL Java programs (no Python bridge needed) ---------------------------------------------
    def run_java_program(self, source: str, class_name: str, timeout: int | None = None,
                         output_file: str | None = None) -> RunResult:
        """Write, compile and run a Java program with comsolbatch; JSON between the markers becomes ``data``."""
        if not self.is_available():
            return self.unavailable()
        work = self.scripts_dir() / "programs"
        work.mkdir(parents=True, exist_ok=True)
        path = work / f"{class_name}.java"
        path.write_text(source, encoding="utf-8")
        comp = self.compile_java(str(path), timeout=timeout)
        if not comp.ok:
            return comp
        out = output_file or str(work / f"{class_name}_out.mph")
        res = self.run_batch(str(path.with_suffix(".class")), output_file=out, timeout=timeout)
        log = Path(res.artifacts.get("batch_log", "") or work / f"{class_name}_batch.log")
        text = res.stdout + "\n" + (log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")
        payload = extract_json_payload(text)
        if payload is not None:
            res.data = {**(res.data or {}), **(payload if isinstance(payload, dict) else {"value": payload})}
            if isinstance(payload, dict) and payload.get("error"):
                res.ok = False
                res.error = str(payload["error"])
        if res.ok and output_file is None:
            # the program only prints JSON: remove its sources, class, log and COMSOL's status file
            for p in work.glob(f"{class_name}*"):
                p.unlink(missing_ok=True)
            res.artifacts.pop("batch_log", None)
            res.artifacts.pop("output_file", None)
        else:
            res.artifacts["java"] = str(path)
        return res

    def inspect_model(self, model_file: str, evaluate: bool = True, timeout: int | None = None) -> RunResult:
        """Physics, multiphysics couplings, materials, studies, datasets, plots and derived values of an .mph."""
        src = resolve_path(model_file)
        if not src.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {src}")
        cls = f"McpInspect{int(time.time() * 1000) % 10**9}"
        return self.run_java_program(summary_java(cls, str(src), evaluate), cls, timeout)

    def evaluate(self, model_file: str, expressions: list[str], units: list[str] | None = None,
                 kind: str = "global", dataset: str | None = None, entities: list[int] | None = None,
                 timeout: int | None = None) -> RunResult:
        """Evaluate expressions on a solved model (global, or integral/average/max/min over domains/boundaries)."""
        src = resolve_path(model_file)
        if not src.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {src}")
        if kind not in EVAL_TYPES and kind not in EVAL_TYPES.values():
            return RunResult(ok=False, software=self.id, command="",
                             error=f"Unknown kind {kind!r}; use one of {', '.join(EVAL_TYPES)}")
        cls = f"McpEval{int(time.time() * 1000) % 10**9}"
        return self.run_java_program(evaluate_java(cls, str(src), expressions, units, kind, dataset or "", entities),
                                     cls, timeout)

    def build_from_java(self, java_file: str, output_file: str | None = None, timeout: int | None = None) -> RunResult:
        """Compile a COMSOL model Java file (e.g. an Application Library script) and run it to build and solve the
        model; the saved .mph path is reported in artifacts.output_file."""
        src = resolve_path(java_file)
        if not src.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {src}")
        comp = self.compile_java(str(src), timeout=timeout)
        if not comp.ok:
            return comp
        out = output_file or str(src.with_name(f"{src.stem}_solved.mph"))
        return self.run_batch(str(src.with_suffix(".class")), output_file=out, timeout=timeout)

    # ---- installation index: modules, User's Guides and Application Library -------------------------
    def library_index(self) -> dict | None:
        root = find_root(self.executable())
        if root is None:
            return None
        return load_index(root, self.cache_dir() / "example_index.json")

    def run_example(self, name: str, output_dir: str | None = None, method: str = "mph", study: str | None = None,
                    cores: int | None = None, timeout: int | None = None) -> RunResult:
        """Copy an Application Library example to *output_dir* and solve it: method "mph" re-solves the library
        model file with comsolbatch, method "java" rebuilds it from its documented Java script."""
        if not self.is_available():
            return self.unavailable()
        index = self.library_index()
        ex = find_example(index or {}, name)
        if ex is None:
            return RunResult(ok=False, software=self.id, command="",
                             error=f"No Application Library example named {name!r}; use comsol_search_examples")
        dest = resolve_path(output_dir) if output_dir else self.scripts_dir() / "examples"
        dest.mkdir(parents=True, exist_ok=True)
        note = ""
        if method != "java" and ex.get("preview"):
            if not ex.get("java"):
                return RunResult(ok=False, software=self.id, command="",
                                 error=(f"{ex['name']} is an Application Library preview file; download the full model "
                                        "in COMSOL (File > Application Libraries > Download) and pass its path to "
                                        "comsol_run_batch"))
            method = "java"
            note = "The library file is a preview, so the model was rebuilt from its documented Java script."
        if method == "java":
            if not ex.get("java"):
                return RunResult(ok=False, software=self.id, command="",
                                 error=f"{ex['name']} has no documented Java script; use method='mph'")
            java = dest / Path(ex["java"]).name
            shutil.copyfile(ex["java"], java)
            copied, missing = copy_companion_files(java, Path(ex["mph"]), dest)
            res = self.build_from_java(str(java), str(dest / f"{ex['name']}_solved.mph"), timeout=timeout)
            if copied:
                res.artifacts["companion_files"] = ";".join(copied)
            if missing:
                res.data = {**(res.data or {}), "missing_files": missing}
                if not res.ok:
                    res.error = (f"{res.error} | Files the script needs are not in the local Application Library: "
                                 f"{', '.join(missing)}. Download the example in COMSOL (Application Libraries window) "
                                 "and solve the downloaded .mph with comsol_run_batch.")
        else:
            mph = dest / f"{ex['name']}.mph"
            shutil.copyfile(ex["mph"], mph)
            res = self.run_batch(str(mph), str(dest / f"{ex['name']}_solved.mph"), study=study, cores=cores,
                                 timeout=timeout)
        res.data = {**(res.data or {}), "example": {k: ex[k] for k in ("title", "library_path", "physics", "studies")},
                    "method": method}
        if note:
            res.data["note"] = note
        return res

    def model_summary(self, model_file: str, timeout: int | None = None) -> RunResult:
        """Summarise parameters, physics, studies and results of an .mph file (requires MPh)."""
        code = """
info = {
    'name': model.name(),
    'parameters': model.parameters(),
    'geometries': model.geometries(),
    'physics': model.physics(),
    'materials': model.materials(),
    'studies': model.studies(),
    'datasets': model.datasets(),
    'plots': model.plots(),
    'exports': model.exports(),
}
print(json.dumps(info, indent=2, default=str))
"""
        return self.run_mph_python(code, model_file, timeout)


_DATA_FILE = re.compile(r'"([^"\\/:*?<>|]+\.(?:mph|mphbin|mphtxt|txt|csv|dat|xlsx|step|stp|x_b|x_t|igs|iges|stl|dxf|'
                        r'dwg|gds|nas|sldprt|ipt|prt|par|m|wav|png|jpg))"', re.IGNORECASE)


def copy_companion_files(java: Path, library_mph: Path, dest: Path) -> tuple[list[str], list[str]]:
    """Copy data files that a documented example Java script opens by bare file name (geometry sequences,
    interpolation tables, CAD parts...) from the Application Library into the working folder."""
    code = "\n".join(ln for ln in java.read_text(encoding="utf-8", errors="replace").splitlines()
                     if not ln.lstrip().startswith("//"))
    names = set(_DATA_FILE.findall(code))
    if not names:
        return [], []
    search_dirs = [library_mph.parent, library_mph.parents[1] if len(library_mph.parents) > 1 else library_mph.parent]
    apps_root = next((p for p in library_mph.parents if p.name == "applications"), None)
    copied, missing = [], []
    for name in sorted(names):
        target = dest / name
        if target.exists():
            continue
        found = next((d / name for d in search_dirs if (d / name).is_file()), None)
        if found is None:
            for d in search_dirs[1:] + ([apps_root] if apps_root else []):
                found = next(iter(d.rglob(name)), None) if d and d.is_dir() else None
                if found:
                    break
        if found:
            shutil.copyfile(found, target)
            copied.append(str(target))
        else:
            missing.append(name)
    return copied, missing


comsol_adapter = ComsolAdapter()
