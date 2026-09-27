"""MATLAB adapter: runs code headless with ``matlab -batch``.

Set ``MATLAB_EXE`` to the launcher (``.../bin/matlab.exe`` on Windows) if it is not on PATH.
Results can be returned as JSON by naming workspace variables in ``capture``; the generated
script serialises them with ``jsonencode`` between marker lines that the adapter parses.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .base import JSON_END, JSON_START, BaseAdapter, RunResult, resolve_path


def matlab_string(text: str) -> str:
    """Return *text* as a single-quoted MATLAB char literal."""
    return "'" + text.replace("'", "''") + "'"


def to_matlab_literal(value: Any) -> str:
    """Convert a JSON-like Python value to MATLAB source."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return matlab_string(value)
    if value is None:
        return "[]"
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "[" + " ".join(repr(v) for v in value) + "]"
        return "{" + ", ".join(to_matlab_literal(v) for v in value) + "}"
    if isinstance(value, dict):
        parts = ", ".join(f"{matlab_string(k)}, {to_matlab_literal(v)}" for k, v in value.items())
        return f"struct({parts})"
    return matlab_string(json.dumps(value))


class MatlabAdapter(BaseAdapter):
    id = "matlab"
    name = "MATLAB"
    env_var = "MATLAB_EXE"
    exe_names = ["matlab", "matlab.exe"]
    exe_patterns = [
        "MATLAB/R20*/bin/matlab.exe",
        "MATLAB_R20*.app/bin/matlab",
        "MATLAB/R20*/bin/matlab",
        "/usr/local/MATLAB/R20*/bin/matlab",
    ]
    install_hint = "MATLAB R2019a or newer is required for the -batch flag."

    # ---- core ----------------------------------------------------------------------------------
    def _batch_args(self, statement: str) -> list[str]:
        exe = self.executable()
        args = [exe or "matlab", "-batch", statement]
        if os.name == "nt":
            args.insert(1, "-wait")
        return args

    def _wrap_script(self, code: str, capture: list[str] | None) -> str:
        lines = [
            "try",
            code,
        ]
        if capture:
            fields = ", ".join(f"{matlab_string(v)}, {v}" for v in capture)
            lines.append(f"    mcp_out__ = struct({fields});")
            lines.append(
                f"    fprintf('\\n{JSON_START}%s{JSON_END}\\n', jsonencode(mcp_out__, 'ConvertInfAndNaN', true));"
            )
        lines += [
            "catch mcp_err__",
            "    fprintf(2, 'MATLAB error: %s\\n', getReport(mcp_err__, 'extended', 'hyperlinks', 'off'));",
            "    exit(1);",
            "end",
        ]
        return "\n".join(lines) + "\n"

    def run_code(self, code: str, capture: list[str] | None = None, cwd: str | None = None,
                 timeout: int | None = None) -> RunResult:
        """Execute MATLAB *code* (any number of statements) in a fresh headless session."""
        if not self.is_available():
            return self.unavailable()
        script = self.write_temp_script(self._wrap_script(code, capture), ".m", "mcp_run")
        statement = f"run({matlab_string(str(script))})"
        if cwd:
            statement = f"cd({matlab_string(str(resolve_path(cwd)))}); " + statement
        result = self.run_command(self._batch_args(statement), timeout=timeout,
                                  artifacts={"script": str(script)}, parse_json=bool(capture))
        return result

    def run_file(self, path: str, args: list[str] | None = None, capture: list[str] | None = None,
                 timeout: int | None = None) -> RunResult:
        """Run an existing .m script or function file. Function files receive *args* verbatim."""
        p = resolve_path(path)
        if not p.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {p}")
        arglist = ", ".join(args or [])
        call = f"{p.stem}({arglist})" if args else f"run({matlab_string(str(p))})"
        code = f"cd({matlab_string(str(p.parent))});\n{call}"
        return self.run_code(code, capture=capture, timeout=timeout)

    def call_function(self, name: str, args: list[Any] | None = None, nargout: int = 1,
                      timeout: int | None = None) -> RunResult:
        """Call a MATLAB function with JSON-typed arguments and return its outputs as JSON."""
        arg_src = ", ".join(to_matlab_literal(a) for a in (args or []))
        outs = [f"out{i + 1}" for i in range(max(0, nargout))]
        lhs = f"[{', '.join(outs)}] = " if outs else ""
        code = f"{lhs}{name}({arg_src});"
        return self.run_code(code, capture=outs or None, timeout=timeout)

    def eval_expression(self, expression: str, timeout: int | None = None) -> RunResult:
        """Evaluate a single expression and return the value as JSON."""
        code = f"mcp_value__ = {expression};"
        return self.run_code(code, capture=["mcp_value__"], timeout=timeout)

    def version(self) -> RunResult:
        return self.run_code("v = version; r = matlabroot; t = ver;", capture=["v", "r"])

    def installed_toolboxes(self) -> RunResult:
        code = "tb = ver; names = {tb.Name}; versions = {tb.Version};"
        return self.run_code(code, capture=["names", "versions"])

    def save_figure_code(self, plot_code: str, output_file: str, dpi: int = 150) -> RunResult:
        """Run plotting code headless and export the current figure to *output_file* (png/pdf/svg/eps)."""
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        code = (
            "f = figure('Visible', 'off');\n"
            f"{plot_code}\n"
            f"exportgraphics(gcf, {matlab_string(str(out))}, 'Resolution', {int(dpi)});\n"
            f"saved = {matlab_string(str(out))};"
        )
        res = self.run_code(code, capture=["saved"])
        res.artifacts["figure"] = str(out)
        return res


matlab_adapter = MatlabAdapter()
