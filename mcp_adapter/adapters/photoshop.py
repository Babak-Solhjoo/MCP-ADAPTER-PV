"""Adobe Photoshop adapter (ExtendScript/JSX automation).

Execution strategy, in order:
1. Windows COM automation (``Photoshop.Application`` via pywin32) – runs the script and waits.
2. macOS ``osascript`` (``do javascript file``).
3. Launching ``Photoshop.exe <script.jsx>`` – Photoshop runs the script; no return value is captured
   except through the JSON result file the generated prelude writes.

Every generated script gets an ``mcpResult(obj)`` helper that serialises *obj* to a result file which
the adapter reads back into ``RunResult.data``.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .base import BaseAdapter, RunResult, resolve_path

JSX_PRELUDE = r"""
// ---- MCP adapter prelude ----
var mcpResultFile = new File("%RESULT%");
function mcpToJSON(o) {
    if (o === null || o === undefined) return "null";
    var t = typeof o;
    if (t === "number" || t === "boolean") return String(o);
    if (t === "string") return '"' + o.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n").replace(/\r/g, "") + '"';
    if (o instanceof Array) { var a = []; for (var i = 0; i < o.length; i++) a.push(mcpToJSON(o[i])); return "[" + a.join(",") + "]"; }
    if (t === "object") { var p = []; for (var k in o) { if (o.hasOwnProperty(k)) p.push('"' + k + '":' + mcpToJSON(o[k])); } return "{" + p.join(",") + "}"; }
    return '"' + String(o) + '"';
}
function mcpResult(obj) {
    mcpResultFile.encoding = "UTF-8";
    mcpResultFile.open("w");
    mcpResultFile.write(mcpToJSON(obj));
    mcpResultFile.close();
}
// ---- end prelude ----
"""


def jsx_string(text: str) -> str:
    return json.dumps(text)


class PhotoshopAdapter(BaseAdapter):
    id = "photoshop"
    name = "Adobe Photoshop"
    env_var = "PHOTOSHOP_EXE"
    exe_names = ["Photoshop.exe"]
    exe_patterns = [
        "Adobe/Adobe Photoshop */Photoshop.exe",
        "Adobe Photoshop */Adobe Photoshop *.app/Contents/MacOS/Adobe Photoshop *",
    ]
    install_hint = "Install Photoshop; on Windows also `pip install pywin32` for synchronous COM execution."

    # ---- execution -----------------------------------------------------------------------------
    def _result_path(self) -> Path:
        return self.scripts_dir() / f"result_{int(time.time() * 1000)}.json"

    def run_jsx(self, script: str, is_file: bool = False, wait: bool = True,
                timeout: int | None = None) -> RunResult:
        """Run ExtendScript (JSX) in Photoshop. *script* is code or, if is_file, a path."""
        if not self.is_available() and os.name == "nt":
            try:
                import win32com.client  # noqa: F401
            except ImportError:
                return self.unavailable()
        code = Path(resolve_path(script)).read_text(encoding="utf-8") if is_file else script
        result_file = self._result_path()
        full = JSX_PRELUDE.replace("%RESULT%", str(result_file).replace("\\", "/")) + "\ntry {\n" + code + (
            "\n} catch (e) { mcpResult({error: String(e), line: e.line}); }\n"
        )
        path = self.write_temp_script(full, ".jsx", "mcp_ps")
        artifacts = {"script": str(path), "result_file": str(result_file)}

        res: RunResult | None = None
        if os.name == "nt":
            res = self._run_via_com(path, artifacts)
        elif sys.platform == "darwin":
            app_name = os.environ.get("PHOTOSHOP_APP_NAME", "Adobe Photoshop 2025")
            res = self.run_command(
                ["osascript", "-e", f'tell application "{app_name}" to do javascript file "{path}"'],
                timeout=timeout, artifacts=artifacts)
        if res is None or (not res.ok and res.error and "COM" in res.error):
            exe = self.executable()
            if not exe:
                return res or self.unavailable()
            res = self.launch_detached([exe, str(path)])
            res.artifacts.update(artifacts)
            if wait:
                deadline = time.time() + (timeout or 120)
                while time.time() < deadline and not result_file.exists():
                    time.sleep(1)
        if result_file.exists():
            try:
                res.data = json.loads(result_file.read_text(encoding="utf-8"))
                if isinstance(res.data, dict) and res.data.get("error"):
                    res.ok = False
                    res.error = f"JSX error: {res.data['error']}"
            except json.JSONDecodeError:
                res.data = {"raw": result_file.read_text(encoding="utf-8", errors="replace")}
        return res

    def _run_via_com(self, script_path: Path, artifacts: dict[str, Any]) -> RunResult:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except ImportError:
            return RunResult(ok=False, software=self.id, command="COM", error="COM unavailable: pip install pywin32",
                             artifacts=artifacts)
        start = time.time()
        try:
            pythoncom.CoInitialize()
            app = win32com.client.Dispatch("Photoshop.Application")
            app.DoJavaScriptFile(str(script_path))
            return RunResult(ok=True, software=self.id, command=f"Photoshop.Application.DoJavaScriptFile({script_path})",
                             returncode=0, duration_s=time.time() - start, artifacts=artifacts)
        except Exception as exc:  # pywin32 raises com_error
            return RunResult(ok=False, software=self.id, command="Photoshop.Application.DoJavaScriptFile",
                             error=f"COM call failed: {exc}", duration_s=time.time() - start, artifacts=artifacts)

    # ---- high level actions ---------------------------------------------------------------------
    def run_action(self, action: str, action_set: str, document: str | None = None,
                   save_as: str | None = None, timeout: int | None = None) -> RunResult:
        """Play a recorded Action (Actions panel) on an optional document and save the result."""
        lines = []
        if document:
            lines.append(f"var doc = app.open(new File({jsx_string(str(resolve_path(document)))}));")
        lines.append(f"app.doAction({jsx_string(action)}, {jsx_string(action_set)});")
        if save_as:
            lines.append(self._save_snippet(save_as))
        lines.append("mcpResult({done: true, document: app.documents.length ? app.activeDocument.name : null});")
        return self.run_jsx("\n".join(lines), timeout=timeout)

    def document_info(self, document: str | None = None, timeout: int | None = None) -> RunResult:
        """Return size, resolution, mode and the layer tree of a document (opens it if a path is given)."""
        open_line = f"var doc = app.open(new File({jsx_string(str(resolve_path(document)))}));" if document else "var doc = app.activeDocument;"
        code = open_line + r"""
function layerTree(container) {
    var out = [];
    for (var i = 0; i < container.layers.length; i++) {
        var l = container.layers[i];
        var item = {name: l.name, kind: String(l.typename), visible: l.visible, opacity: l.opacity, blendMode: String(l.blendMode)};
        if (l.typename == "LayerSet") item.children = layerTree(l);
        out.push(item);
    }
    return out;
}
mcpResult({
    name: doc.name, path: String(doc.fullName), width: doc.width.as("px"), height: doc.height.as("px"),
    resolution: doc.resolution, mode: String(doc.mode), bitsPerChannel: String(doc.bitsPerChannel),
    layerCount: doc.layers.length, channels: doc.channels.length, layers: layerTree(doc)
});
"""
        return self.run_jsx(code, timeout=timeout)

    def _save_snippet(self, output_path: str, quality: int = 90) -> str:
        out = resolve_path(output_path)
        ext = out.suffix.lower()
        f = jsx_string(str(out))
        if ext in (".jpg", ".jpeg"):
            return f"var o = new JPEGSaveOptions(); o.quality = {max(1, min(12, round(quality / 8.34)))}; doc.saveAs(new File({f}), o, true, Extension.LOWERCASE);"
        if ext == ".png":
            return f"var o = new PNGSaveOptions(); o.compression = 6; doc.saveAs(new File({f}), o, true, Extension.LOWERCASE);"
        if ext in (".tif", ".tiff"):
            return f"var o = new TiffSaveOptions(); o.imageCompression = TIFFEncoding.TIFFLZW; doc.saveAs(new File({f}), o, true, Extension.LOWERCASE);"
        if ext == ".psd":
            return f"var o = new PhotoshopSaveOptions(); doc.saveAs(new File({f}), o, true, Extension.LOWERCASE);"
        if ext == ".gif":
            return f"var o = new GIFSaveOptions(); doc.saveAs(new File({f}), o, true, Extension.LOWERCASE);"
        if ext == ".pdf":
            return f"var o = new PDFSaveOptions(); doc.saveAs(new File({f}), o, true, Extension.LOWERCASE);"
        return f"doc.saveAs(new File({f}), undefined, true, Extension.LOWERCASE);"

    OPERATIONS = {
        "resize": "doc.resizeImage({width}, {height}, undefined, ResampleMethod.BICUBIC);",
        "resize_width": "doc.resizeImage(UnitValue({width}, 'px'), undefined, undefined, ResampleMethod.BICUBIC);",
        "canvas": "doc.resizeCanvas(UnitValue({width}, 'px'), UnitValue({height}, 'px'), AnchorPosition.MIDDLECENTER);",
        "crop": "doc.crop([{left}, {top}, {right}, {bottom}]);",
        "rotate": "doc.rotateCanvas({angle});",
        "flip_horizontal": "doc.flipCanvas(Direction.HORIZONTAL);",
        "flip_vertical": "doc.flipCanvas(Direction.VERTICAL);",
        "grayscale": "doc.changeMode(ChangeMode.GRAYSCALE);",
        "rgb": "doc.changeMode(ChangeMode.RGB);",
        "cmyk": "doc.changeMode(ChangeMode.CMYK);",
        "flatten": "doc.flatten();",
        "merge_visible": "doc.mergeVisibleLayers();",
        "auto_contrast": "doc.activeLayer.autoContrast();",
        "auto_levels": "doc.activeLayer.autoLevels();",
        "brightness_contrast": "doc.activeLayer.adjustBrightnessContrast({brightness}, {contrast});",
        "levels": "doc.activeLayer.adjustLevels({input_low}, {input_high}, {gamma}, {output_low}, {output_high});",
        "gaussian_blur": "doc.activeLayer.applyGaussianBlur({radius});",
        "unsharp_mask": "doc.activeLayer.applyUnSharpMask({amount}, {radius}, {threshold});",
        "sharpen": "doc.activeLayer.applySharpen();",
        "invert": "doc.activeLayer.invert();",
        "desaturate": "doc.activeLayer.desaturate();",
        "hue_saturation": "doc.activeLayer.adjustHueSaturation? doc.activeLayer.adjustHueSaturation({hue}, {saturation}, {lightness}) : null;",
        "resolution": "doc.resizeImage(undefined, undefined, {dpi}, ResampleMethod.NONE);",
        "trim": "doc.trim(TrimType.TRANSPARENT, true, true, true, true);",
        "add_text": "var tl = doc.artLayers.add(); tl.kind = LayerKind.TEXT; tl.textItem.contents = {text}; tl.textItem.size = {size}; tl.textItem.position = [{x}, {y}];",
    }

    def batch_process(self, input_files: list[str], output_dir: str, operations: list[dict[str, Any]],
                      output_format: str = "png", quality: int = 90, timeout: int | None = None) -> RunResult:
        """Open each file, apply *operations* in order and save to *output_dir*.

        operations: [{"op": "resize_width", "width": 1200}, {"op": "unsharp_mask", "amount": 80, "radius": 1.2, "threshold": 2}]
        Supported ops: see PhotoshopAdapter.OPERATIONS keys.
        """
        out_dir = resolve_path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        op_lines = []
        for op in operations:
            template = self.OPERATIONS.get(op.get("op", ""))
            if not template:
                return RunResult(ok=False, software=self.id, command="",
                                 error=f"Unknown operation {op.get('op')!r}. Known: {', '.join(sorted(self.OPERATIONS))}")
            params = {k: (json.dumps(v) if isinstance(v, str) else v) for k, v in op.items() if k != "op"}
            try:
                op_lines.append(template.format(**params))
            except KeyError as exc:
                return RunResult(ok=False, software=self.id, command="", error=f"Operation {op['op']} needs parameter {exc}")
        files_js = "[" + ", ".join(jsx_string(str(resolve_path(f))) for f in input_files) + "]"
        code = f"""
var inputs = {files_js};
var outDir = {jsx_string(str(out_dir))};
var results = [];
app.displayDialogs = DialogModes.NO;
for (var i = 0; i < inputs.length; i++) {{
    var doc = app.open(new File(inputs[i]));
    {chr(10).join(op_lines)}
    var base = doc.name.replace(/\\.[^.]+$/, "");
    var outPath = outDir + "/" + base + ".{output_format.lower().lstrip('.')}";
    {self._save_snippet("PLACEHOLDER." + output_format.lower().lstrip('.'), quality).replace(jsx_string(str(resolve_path("PLACEHOLDER." + output_format.lower().lstrip('.')))), "outPath")}
    results.push(outPath);
    doc.close(SaveOptions.DONOTSAVECHANGES);
}}
mcpResult({{processed: results.length, files: results}});
"""
        res = self.run_jsx(code, timeout=timeout)
        res.artifacts["output_dir"] = str(out_dir)
        return res

    def export_layers(self, document: str, output_dir: str, output_format: str = "png",
                      timeout: int | None = None) -> RunResult:
        """Export every top-level layer of *document* as an individual image."""
        out_dir = resolve_path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        code = f"""
var doc = app.open(new File({jsx_string(str(resolve_path(document)))}));
var outDir = {jsx_string(str(out_dir))};
app.displayDialogs = DialogModes.NO;
var names = [];
var vis = [];
for (var i = 0; i < doc.layers.length; i++) vis.push(doc.layers[i].visible);
for (var i = 0; i < doc.layers.length; i++) {{
    for (var j = 0; j < doc.layers.length; j++) doc.layers[j].visible = (i == j);
    var safe = doc.layers[i].name.replace(/[^a-zA-Z0-9_-]+/g, "_");
    var f = new File(outDir + "/" + (i + 1) + "_" + safe + ".{output_format.lower()}");
    var o = new ExportOptionsSaveForWeb(); o.format = SaveDocumentType.{ 'JPEG' if output_format.lower() in ('jpg','jpeg') else 'PNG' }; o.PNG8 = false; o.transparency = true; o.quality = 90;
    doc.exportDocument(f, ExportType.SAVEFORWEB, o);
    names.push(String(f.fsName));
}}
for (var i = 0; i < doc.layers.length; i++) doc.layers[i].visible = vis[i];
mcpResult({{exported: names}});
"""
        return self.run_jsx(code, timeout=timeout)


photoshop_adapter = PhotoshopAdapter()
