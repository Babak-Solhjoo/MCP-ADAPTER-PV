"""Wolfram Mathematica / Wolfram Engine adapter built on ``wolframscript``.

Set ``WOLFRAMSCRIPT_EXE`` if wolframscript is not on PATH. Works with the free Wolfram Engine too.
"""
from __future__ import annotations

from .base import BaseAdapter, RunResult, resolve_path


def wl_string(text: str) -> str:
    """Return *text* as a Wolfram Language string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MathematicaAdapter(BaseAdapter):
    id = "mathematica"
    name = "Wolfram Mathematica (wolframscript)"
    env_var = "WOLFRAMSCRIPT_EXE"
    exe_names = ["wolframscript", "wolframscript.exe"]
    exe_patterns = [
        "Wolfram Research/Mathematica/*/wolframscript.exe",
        "Wolfram Research/Wolfram Engine/*/wolframscript.exe",
        "Wolfram Research/WolframScript/wolframscript.exe",
        "Wolfram/*/wolframscript.exe",
        "Mathematica.app/Contents/MacOS/wolframscript",
        "Wolfram Engine.app/Contents/MacOS/wolframscript",
        "/usr/local/bin/wolframscript",
        "/usr/bin/wolframscript",
    ]
    install_hint = "Install Mathematica or the free Wolfram Engine plus WolframScript."

    def evaluate(self, code: str, output_format: str = "text", timeout: int | None = None,
                 print_all: bool = False) -> RunResult:
        """Evaluate Wolfram Language *code* with ``wolframscript -code``.

        output_format: "text" (OutputForm), "inputform" (parseable), "json" (ExportString[..., "JSON"]),
                       "tex" (TeXForm) or "fullform".
        """
        if not self.is_available():
            return self.unavailable()
        fmt = (output_format or "text").lower()
        wrapped = {
            "inputform": f"ToString[({code}), InputForm]",
            "json": f'ExportString[({code}), "JSON"]',
            "tex": f"ToString[TeXForm[({code})]]",
            "fullform": f"ToString[FullForm[({code})]]",
        }.get(fmt, code)
        args = [self.executable() or "wolframscript", "-code", wrapped]
        if print_all:
            args += ["-print", "all"]
        res = self.run_command(args, timeout=timeout)
        if res.ok and fmt == "json":
            import json

            try:
                res.data = json.loads(res.stdout)
            except json.JSONDecodeError:
                pass
        return res

    def run_file(self, path: str, args: list[str] | None = None, timeout: int | None = None) -> RunResult:
        """Run a .wls/.wl/.m script file; extra *args* are visible via $ScriptCommandLine."""
        if not self.is_available():
            return self.unavailable()
        p = resolve_path(path)
        if not p.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {p}")
        cmd = [self.executable() or "wolframscript", "-file", str(p)] + list(args or [])
        return self.run_command(cmd, cwd=p.parent, timeout=timeout)

    def run_script_text(self, script: str, args: list[str] | None = None, timeout: int | None = None) -> RunResult:
        """Save multi-line Wolfram Language *script* to a .wls file and run it."""
        path = self.write_temp_script(script, ".wls", "mcp_wl")
        res = self.run_file(str(path), args, timeout)
        res.artifacts["script"] = str(path)
        return res

    def export(self, expression: str, output_file: str, export_format: str | None = None,
               options: str | None = None, timeout: int | None = None) -> RunResult:
        """Export the result of *expression* (a plot, image, dataset...) to *output_file* via Export[]."""
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        fmt = f", {wl_string(export_format)}" if export_format else ""
        opts = f", {options}" if options else ""
        code = f"Export[{wl_string(str(out))}, ({expression}){fmt}{opts}]"
        res = self.evaluate(code, "text", timeout)
        res.artifacts["output_file"] = str(out)
        return res

    def version(self) -> RunResult:
        return self.evaluate('StringJoin[$Version, " | ", ToString[$VersionNumber], " | ", $InstallationDirectory]')

    def wolfram_alpha(self, query: str, timeout: int | None = None) -> RunResult:
        """Query Wolfram|Alpha through WolframAlpha[] (needs internet + Wolfram account)."""
        return self.evaluate(f'WolframAlpha[{wl_string(query)}, "Result"]', "text", timeout)

    def solve_json(self, code: str, timeout: int | None = None) -> RunResult:
        """Evaluate and return JSON when possible, falling back to InputForm text."""
        res = self.evaluate(code, "json", timeout)
        if res.ok and res.data is None:
            return self.evaluate(code, "inputform", timeout)
        return res


mathematica_adapter = MathematicaAdapter()
