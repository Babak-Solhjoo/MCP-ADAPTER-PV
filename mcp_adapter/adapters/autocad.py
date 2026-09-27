"""Autodesk AutoCAD adapter.

Headless execution uses the AutoCAD Core Console (``accoreconsole.exe /i drawing.dwg /s script.scr``),
which runs AutoCAD commands and AutoLISP without the GUI. Optional COM automation
(``AutoCAD.Application`` via pywin32) talks to a running AutoCAD session.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from .base import BaseAdapter, RunResult, read_text_if_exists, resolve_path


def lisp_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class AutoCADAdapter(BaseAdapter):
    id = "autocad"
    name = "AutoCAD (Core Console)"
    env_var = "AUTOCAD_CORE_CONSOLE_EXE"
    exe_names = ["accoreconsole.exe", "accoreconsole"]
    exe_patterns = [
        "Autodesk/AutoCAD 20*/accoreconsole.exe",
        "Autodesk/AutoCAD LT 20*/accoreconsole.exe",
        "Autodesk/AutoCAD 20*/AutoCAD 20*.app/Contents/MacOS/accoreconsole",
    ]
    install_hint = "Install AutoCAD (accoreconsole.exe ships with it) and set AUTOCAD_CORE_CONSOLE_EXE."

    # ---- core ----------------------------------------------------------------------------------
    def run_script(self, script: str, drawing: str | None = None, is_file: bool = False,
                   language: str = "en-US", timeout: int | None = None) -> RunResult:
        """Run an AutoCAD script (.scr text or path) headless, optionally on *drawing*.

        Script rules: one command/response per line; use command-line variants (prefix '-' or '_.'),
        and set FILEDIA 0 when commands take file names. End with _.QSAVE or _.SAVEAS to persist changes.
        """
        if not self.is_available():
            return self.unavailable()
        if is_file:
            scr = resolve_path(script)
        else:
            text = script if script.endswith("\n") else script + "\n"
            scr = self.write_temp_script(text, ".scr", "mcp_acad")
        args = [self.executable() or "accoreconsole.exe"]
        if drawing:
            dwg = resolve_path(drawing)
            if not dwg.exists():
                return RunResult(ok=False, software=self.id, command="", error=f"Drawing not found: {dwg}")
            args += ["/i", str(dwg)]
        args += ["/s", str(scr), "/l", language]
        res = self.run_command(args, timeout=timeout, artifacts={"script": str(scr)})
        # accoreconsole prints AutoCAD's "Unknown command" / "Invalid" diagnostics on stdout
        bad = [ln for ln in res.stdout.splitlines() if "Unknown command" in ln or "Invalid" in ln or "*Cancel*" in ln]
        if bad:
            res.data = {"diagnostics": bad}
        return res

    def run_lisp(self, lisp_code: str, drawing: str | None = None, call: str | None = None,
                 save: bool = False, timeout: int | None = None) -> RunResult:
        """Load AutoLISP code in the Core Console and optionally call an expression, then save.

        The generated script also defines (mcp-json-write <alist> <path>) so the LISP can return data.
        """
        result_file = self.scripts_dir() / f"lisp_result_{int(time.time() * 1000)}_{uuid.uuid4().hex}.json"
        helper = r"""
(defun mcp-json-escape (s / out i c)
  (setq out "" i 1)
  (while (<= i (strlen s))
    (setq c (substr s i 1))
    (cond ((= c "\"") (setq out (strcat out "\\\"")))
          ((= c "\\") (setq out (strcat out "\\\\")))
          ((= c "\n") (setq out (strcat out "\\n")))
          (T (setq out (strcat out c))))
    (setq i (1+ i)))
  out)
(defun mcp-json-value (v)
  (cond ((= (type v) 'STR) (strcat "\"" (mcp-json-escape v) "\""))
        ((= (type v) 'INT) (itoa v))
        ((= (type v) 'REAL) (rtos v 2 6))
        ((null v) "null")
        ((= v T) "true")
        ((= (type v) 'LIST)
         (if (and (car v) (= (type (car v)) 'LIST) (= (type (caar v)) 'STR))
           (strcat "{" (mcp-json-join (mapcar '(lambda (p) (strcat "\"" (mcp-json-escape (car p)) "\":" (mcp-json-value (cdr p)))) v)) "}")
           (strcat "[" (mcp-json-join (mapcar 'mcp-json-value v)) "]")))
        (T (strcat "\"" (mcp-json-escape (vl-princ-to-string v)) "\""))))
(defun mcp-json-join (lst / s)
  (setq s "")
  (foreach x lst (setq s (if (= s "") x (strcat s "," x))))
  s)
(defun mcp-json-write (alist path / f)
  (setq f (open path "w"))
  (write-line (mcp-json-value alist) f)
  (close f)
  path)
(setq *mcp-result-file* %RESULT%)
""".replace("%RESULT%", lisp_string(str(result_file).replace("\\", "/")))
        lsp = self.write_temp_script(helper + "\n" + lisp_code + "\n", ".lsp", "mcp_lisp")
        lsp_path = str(lsp).replace("\\", "/")
        lines = ["_.FILEDIA 0", f'(load "{lsp_path}")']
        if call:
            lines.append(call)
        if save:
            lines.append("_.QSAVE")
        lines.append("_.FILEDIA 1")
        res = self.run_script("\n".join(lines) + "\n", drawing=drawing, timeout=timeout)
        res.artifacts["lisp_file"] = str(lsp)
        if result_file.exists():
            try:
                res.data = json.loads(result_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                res.data = {"raw": read_text_if_exists(result_file)}
            res.artifacts["result_file"] = str(result_file)
        return res

    # ---- ready-made actions ----------------------------------------------------------------------
    def drawing_info(self, drawing: str, timeout: int | None = None) -> RunResult:
        """Return layers, block definitions, entity counts by type and extents of a drawing."""
        lisp = r"""
(defun mcp-count-entities (/ e counts typ pair)
  (setq counts '() e (entnext))
  (while e
    (setq typ (cdr (assoc 0 (entget e))))
    (setq pair (assoc typ counts))
    (if pair (setq counts (subst (cons typ (1+ (cdr pair))) pair counts)) (setq counts (cons (cons typ 1) counts)))
    (setq e (entnext e)))
  counts)
(defun mcp-table-names (tbl / rec names)
  (setq names '() rec (tblnext tbl T))
  (while rec (setq names (cons (cdr (assoc 2 rec)) names) rec (tblnext tbl)))
  (reverse names))
(mcp-json-write
  (list (cons "drawing" (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")))
        (cons "acadver" (getvar "ACADVER"))
        (cons "insunits" (getvar "INSUNITS"))
        (cons "layers" (mcp-table-names "LAYER"))
        (cons "blocks" (vl-remove-if '(lambda (n) (wcmatch n "`**")) (mcp-table-names "BLOCK")))
        (cons "layouts" (mcp-table-names "LAYOUT"))
        (cons "textstyles" (mcp-table-names "STYLE"))
        (cons "dimstyles" (mcp-table-names "DIMSTYLE"))
        (cons "entity_counts" (mcp-count-entities))
        (cons "extmin" (mapcar 'rtos (getvar "EXTMIN")))
        (cons "extmax" (mapcar 'rtos (getvar "EXTMAX"))))
  *mcp-result-file*)
"""
        return self.run_lisp(lisp, drawing=drawing, timeout=timeout)

    def export_dxf(self, drawing: str, output_file: str, precision: int = 16, timeout: int | None = None) -> RunResult:
        """Export a drawing to DXF with DXFOUT."""
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        script = f'_.FILEDIA 0\n_.DXFOUT\n"{out}"\n{int(precision)}\n_.FILEDIA 1\n'
        res = self.run_script(script, drawing=drawing, timeout=timeout)
        res.artifacts["dxf"] = str(out)
        if res.ok and not out.exists():
            res.ok = False
            res.error = "DXFOUT finished but the output file was not created; check stdout diagnostics."
        return res

    def plot_to_pdf(self, drawing: str, output_file: str, layout: str = "Model", paper: str = "ISO A3 (420.00 x 297.00 MM)",
                    plot_area: str = "Extents", timeout: int | None = None) -> RunResult:
        """Plot a layout to PDF with the command-line -PLOT sequence (DWG To PDF.pc3)."""
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        # -PLOT prompts: detailed? / layout / printer / paper / units / orientation / upside down / area /
        # scale / offset / plot styles? / style table / lineweights / [shade / write to file] / save changes / proceed
        script = "\n".join([
            "_.FILEDIA 0",
            "_.-PLOT", "Y", layout, "DWG To PDF.pc3", paper, "M", "L", "N", plot_area, "F", "C", "Y", ".",
            "Y", "N" if layout.lower() == "model" else "Y", f'"{out}"', "N", "Y", "_.FILEDIA 1", "",
        ]) + "\n"
        res = self.run_script(script, drawing=drawing, timeout=timeout)
        res.artifacts["pdf"] = str(out)
        if res.ok and not out.exists():
            res.ok = False
            res.error = "-PLOT completed without producing the PDF; the prompt sequence may differ for this layout/version."
        return res

    def batch(self, script: str, drawings: list[str], is_file: bool = False, timeout: int | None = None) -> dict[str, Any]:
        """Run the same script against many drawings; returns per-drawing results."""
        results = {}
        for dwg in drawings:
            results[dwg] = self.run_script(script, drawing=dwg, is_file=is_file, timeout=timeout).to_dict()
        return {"count": len(results), "results": results}

    # ---- live session via COM --------------------------------------------------------------------
    def com_send_command(self, command: str, wait_seconds: float = 1.0) -> RunResult:
        """Send a command string to a running AutoCAD instance (Windows COM, pywin32 required)."""
        if os.name != "nt":
            return RunResult(ok=False, software=self.id, command="", error="COM automation is Windows-only.")
        try:
            import win32com.client  # type: ignore
        except ImportError:
            return RunResult(ok=False, software=self.id, command="", error="pip install pywin32 to use COM automation.")
        try:
            app = win32com.client.GetActiveObject("AutoCAD.Application")
        except Exception:
            try:
                app = win32com.client.Dispatch("AutoCAD.Application")
                app.Visible = True
            except Exception as exc:
                return RunResult(ok=False, software=self.id, command="", error=f"Could not attach to AutoCAD: {exc}")
        try:
            doc = app.ActiveDocument
            doc.SendCommand(command if command.endswith("\n") else command + "\n")
            time.sleep(wait_seconds)
            return RunResult(ok=True, software=self.id, command=f"SendCommand({command!r})", returncode=0,
                             stdout=f"Sent to {doc.Name}")
        except Exception as exc:
            return RunResult(ok=False, software=self.id, command="SendCommand", error=str(exc))


autocad_adapter = AutoCADAdapter()
