"""Autodesk EAGLE adapter (schematic capture and PCB layout).

Verified against EAGLE 9.6.0 on Windows (``C:\\Program Files\\EAGLE 9.6.0``) in September 2026, after Autodesk
retired EAGLE on 7 June 2026 and shut down its licensing servers:

* ``eaglecon.exe`` is the console twin of ``eagle.exe``. With ``-X`` it runs the CAM Processor without a GUI and
  without signing in: ``-dCAMJOB -j<job.cam> -o<dir> board.brd`` processes a JSON CAM job and writes Gerber files,
  the Gerber job file, Excellon drill data and assembly data under ``<dir>/CAMOutputs/...`` (about 4 s for a small
  board); a legacy device (``-dGERBER_RS274X``, ``-dEXCELLON``, ... from ``bin/eagle.def``) plus a layer list writes a
  single file. ``eaglecon -?`` prints ``EAGLE Version 9.6.0 ...`` and the option list.
* The editor route (``-C "commands" design``) opens the editor, which now stops at the Autodesk "Sign in" window
  when no valid session exists. ``run_commands`` detects that window, closes the process and says so; the adapter
  never signs in.
* ``.sch``/``.brd``/``.lbr`` files are XML (``doc/eagle.dtd``, coordinates in mm), so designs are read here without
  EAGLE: parts, nets, signals, layers, design rules, BOM and netlist.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

from .base import BaseAdapter, RunResult, resolve_path

DESIGN_SUFFIXES = (".sch", ".brd", ".lbr")
_VERSION_RE = re.compile(r"EAGLE Version\s+(\d+\.\d+\.\d+)")
_LAYER_SETUP_RE = re.compile(r"\d+")
STANDARD_CAM_JOBS = (1, 2, 4, 6, 8, 10, 12, 14, 16)
SIGN_IN_TITLES = ("sign in",)
MAX_ITEMS = 2000


class EagleAdapter(BaseAdapter):
    id = "eagle"
    name = "Autodesk EAGLE"
    env_var = "EAGLE_EXE"
    exe_names = ["eaglecon.exe", "eagle.exe", "eagle"]
    exe_patterns = [
        "EAGLE 9.*/eaglecon.exe",
        "EAGLE-9.*/eaglecon.exe",
        "EAGLE*/eaglecon.exe",
        "EAGLE*/eagle.exe",
        "Autodesk/EAGLE*/eaglecon.exe",
        "/Applications/EAGLE-*/EAGLE.app/Contents/MacOS/EAGLE",
        "/opt/eagle*/eagle",
    ]
    install_hint = ("Install Autodesk EAGLE 9.x and set EAGLE_EXE to eaglecon.exe (…\\EAGLE 9.6.0\\eaglecon.exe). "
                    "EAGLE was retired on 7 June 2026: the command-line CAM Processor still runs, the editors need "
                    "an Autodesk sign-in.")

    # ---- paths ---------------------------------------------------------------------------------
    def console(self) -> str | None:
        """eaglecon.exe (keeps the console, returns when done); falls back to the configured executable."""
        exe = self.executable()
        if not exe:
            return None
        p = Path(exe)
        if p.name.lower() == "eagle.exe":
            con = p.with_name("eaglecon.exe")
            if con.exists():
                return str(con)
        return exe

    def install_dir(self) -> Path | None:
        exe = self.executable()
        return Path(exe).parent if exe else None

    def cam_jobs(self) -> list[dict[str, str]]:
        """CAM job files shipped with the installation (examples/cam/**.cam)."""
        root = self.install_dir()
        if not root or not (root / "examples" / "cam").is_dir():
            return []
        return [{"name": p.stem, "path": str(p), "group": p.parent.name}
                for p in sorted((root / "examples" / "cam").rglob("*.cam"))]

    def find_cam_job(self, job: str) -> Path | None:
        if not job:
            return None
        p = resolve_path(job)
        if p.exists():
            return p
        key = Path(job).stem.lower()
        for j in self.cam_jobs():
            if j["name"].lower() == key:
                return Path(j["path"])
        return None

    def devices(self) -> list[dict[str, str]]:
        """Legacy output devices from bin/eagle.def (name and long description)."""
        root = self.install_dir()
        path = None
        if root:
            for cand in (root / "bin" / "eagle.def", root / "eagle.def"):
                if cand.exists():
                    path = cand
                    break
        return parse_devices(path.read_text(encoding="latin-1")) if path else []

    # ---- version -------------------------------------------------------------------------------
    def version(self, timeout: int | None = None) -> RunResult:
        if not self.is_available():
            return self.unavailable()
        res = self.run_command([self.console() or "eaglecon", "-?"], timeout=timeout or 60)
        m = _VERSION_RE.search((res.stdout or "") + (res.stderr or ""))
        if m:
            res.ok, res.error = True, None
            res.data = {"version": m.group(1), "executable": self.console()}
        elif res.ok:
            res.ok, res.error = False, "EAGLE did not print a version banner"
        return res

    # ---- CAM -----------------------------------------------------------------------------------
    def cam_job(self, board: str, job: str | None = None, output_dir: str | None = None,
                variant: str | None = None, timeout: int | None = None) -> RunResult:
        """Process a JSON CAM job on a board: eaglecon -X -N -dCAMJOB -j<job> -o<dir> [-A<variant>] board.brd."""
        if not self.is_available():
            return self.unavailable()
        brd = resolve_path(board)
        if not brd.exists() or brd.suffix.lower() != ".brd":
            return RunResult(ok=False, software=self.id, command="", error=f"Board (.brd) not found: {brd}")
        note = None
        if job:
            job_path = self.find_cam_job(job)
            if job_path is None:
                return RunResult(ok=False, software=self.id, command="",
                                 error=f"CAM job not found: {job} (give a path or a shipped job name; see eagle_cam_jobs)")
        else:
            layers = copper_layer_count(brd)
            n = min((k for k in STANDARD_CAM_JOBS if k >= layers), default=16)
            job_path = self.find_cam_job(f"example_{n}_layer")
            if job_path is None:
                return RunResult(ok=False, software=self.id, command="",
                                 error="No CAM job given and the shipped example jobs were not found; pass cam_job")
            note = f"Board uses {layers} copper layer(s); used the shipped job {job_path.name}."
        out = resolve_path(output_dir) if output_dir else self.scripts_dir() / f"{brd.stem}_cam"
        out.mkdir(parents=True, exist_ok=True)
        args = [self.console() or "eaglecon", "-X", "-N", "-dCAMJOB", f"-j{job_path}", f"-o{out}"]
        if variant:
            args.append(f"-A{variant}")
        args.append(str(brd))
        start = time.time()
        res = self.run_command(args, cwd=brd.parent, timeout=timeout or 600)
        files = sorted(str(p) for p in out.rglob("*") if p.is_file() and p.stat().st_mtime >= start - 2)
        res.artifacts.update({"output_dir": str(out), "cam_job": str(job_path), "files": files})
        res.data = {"file_count": len(files), "gerber_files": [f for f in files if f.lower().endswith((".gbr", ".gbrjob"))],
                    "drill_files": [f for f in files if f.lower().endswith((".xln", ".drl", ".drd", ".exc"))]}
        if note:
            res.data["note"] = note
        if files:
            res.ok, res.error = True, None
        elif res.ok or not res.error:
            res.ok, res.error = False, "The CAM Processor finished without writing any file; check the job and the board"
        return res

    def cam_output(self, board: str, device: str, layers: list[str | int], output_file: str,
                   flags: list[str] | None = None, timeout: int | None = None) -> RunResult:
        """Legacy single-output CAM run: eaglecon -X -N -d<device> -o<file> board.brd <layers...>."""
        if not self.is_available():
            return self.unavailable()
        brd = resolve_path(board)
        if not brd.exists() or brd.suffix.lower() != ".brd":
            return RunResult(ok=False, software=self.id, command="", error=f"Board (.brd) not found: {brd}")
        if not layers:
            return RunResult(ok=False, software=self.id, command="", error="Give at least one layer (number or name)")
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [self.console() or "eaglecon", "-X", "-N", f"-d{device}", f"-o{out}", *(flags or []), str(brd),
                *[str(x) for x in layers]]
        start = time.time()
        res = self.run_command(args, cwd=brd.parent, timeout=timeout or 300)
        res.artifacts["output_file"] = str(out)
        if out.exists() and out.stat().st_mtime >= start - 2:
            res.ok, res.error = True, None
        elif res.ok or not res.error:
            res.ok, res.error = False, f"No output written to {out}; check the device name (eagle_list_devices) and layers"
        return res

    # ---- editor commands (needs a signed-in EAGLE) ---------------------------------------------
    def run_commands(self, design: str, commands: str, timeout: int | None = None) -> RunResult:
        """Open *design* in the editor and execute *commands* (eagle -C). QUIT is appended so EAGLE exits.

        Since the June 2026 licensing shutdown the editor usually stops at the Autodesk 'Sign in' window; that is
        detected, the process is closed and the result says so (the adapter never signs in)."""
        if not self.is_available():
            return self.unavailable()
        path = resolve_path(design)
        if path.suffix.lower() not in DESIGN_SUFFIXES:
            return RunResult(ok=False, software=self.id, command="", error="Design must be a .sch, .brd or .lbr file")
        cmd = commands.strip().rstrip(";")
        if not re.search(r"(^|;)\s*quit\s*$", cmd, re.I):
            cmd += "; QUIT"
        args = [self.console() or "eaglecon", "-N", "-C", cmd + ";", str(path)]
        start = time.time()
        limit = timeout or 120
        try:
            proc = subprocess.Popen(args, cwd=str(path.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            return RunResult(ok=False, software=self.id, command=" ".join(args), error=str(exc))
        blocked = None
        while proc.poll() is None and time.time() - start < limit:
            title = blocking_window(proc.pid)
            if title:
                blocked = title
                break
            time.sleep(1.0)
        if proc.poll() is None:
            proc.kill()
            out, err = proc.communicate(timeout=10)
            msg = (f"EAGLE's editor is waiting at the '{blocked}' window: since Autodesk retired EAGLE (7 June 2026) "
                   "the editors need an Autodesk sign-in, which this adapter never performs. Use eagle_cam_job for "
                   "manufacturing data and eagle_read_design / eagle_bom / eagle_netlist to read the design."
                   if blocked else f"EAGLE did not finish within {limit} s")
            return RunResult(ok=False, software=self.id, command=" ".join(args), stdout=out or "", stderr=err or "",
                             duration_s=time.time() - start, error=msg)
        out, err = proc.communicate()
        return RunResult(ok=proc.returncode == 0, software=self.id, command=" ".join(args), returncode=proc.returncode,
                         stdout=out or "", stderr=err or "", duration_s=time.time() - start,
                         error=None if proc.returncode == 0 else f"EAGLE exited with code {proc.returncode}")


# ---- helpers usable without EAGLE -------------------------------------------------------------
def parse_devices(text: str) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\[([A-Za-z0-9_+-]+)\]", s)
        if m:
            current = {"name": m.group(1), "type": "", "description": ""}
            devices.append(current)
        elif current is not None and not s.startswith(";"):
            if s.lower().startswith("type") and not current["type"]:
                current["type"] = s.split("=", 1)[-1].strip() if "=" in s else s.split(None, 1)[-1].strip()
            elif s.lower().startswith("long") and not current["description"]:
                current["description"] = s.split("=", 1)[-1].strip().strip('"')
    return devices


def blocking_window(pid: int) -> str | None:
    """Title of a visible 'Sign in' window owned by *pid* (Windows only; None elsewhere)."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[str] = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.strip().lower() in SIGN_IN_TITLES:
                    found.append(buf.value.strip())
            return True

        user32.EnumWindows(proto(callback), 0)
        return found[0] if found else None
    except Exception:  # noqa: BLE001 - window inspection is best effort
        return None


def _load(path: str | Path) -> tuple[Path, ET.Element]:
    p = resolve_path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if p.suffix.lower() not in DESIGN_SUFFIXES:
        raise ValueError(f"Expected an EAGLE .sch, .brd or .lbr file, got {p.suffix}")
    with p.open("rb") as fh:
        head = fh.read(64).lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"<"):
        raise ValueError(f"{p.name} is in EAGLE's binary format (before 6.0), which only EAGLE reads. The CAM "
                         "Processor (eagle_cam_job) still processes it; to read parts and nets, open and save it in "
                         "EAGLE 6 or newer (or in Fusion Electronics), which converts it to XML.")
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{p.name} is not an XML EAGLE file (EAGLE 6.0 or newer): {exc}") from None
    if root.tag != "eagle":
        raise ValueError(f"{p.name} is not an EAGLE file (root element <{root.tag}>)")
    return p, root


def _attrs(el: ET.Element) -> dict[str, str]:
    return {a.get("name", ""): a.get("value", "") for a in el.findall("attribute") if a.get("value") not in (None, "")}


def copper_layer_count(board: str | Path) -> int:
    """Copper layers of a board from its design rules' layerSetup (e.g. '(1*16)' = 2, '(1*2*15*16)' = 4)."""
    try:
        _p, root = _load(board)
    except (FileNotFoundError, ValueError):
        return 2
    for param in root.iter("param"):
        if param.get("name") == "layerSetup":
            nums = {int(n) for n in _LAYER_SETUP_RE.findall(param.get("value", "")) if 1 <= int(n) <= 16}
            if nums:
                return len(nums)
    used = {int(w.get("layer", 0)) for w in root.iter("wire") if 1 <= int(w.get("layer", 0) or 0) <= 16}
    return max(len(used), 1)


def _library_packages(root: ET.Element) -> dict[tuple[str, str, str], str]:
    """(library, deviceset, device) -> package name, from the libraries embedded in a schematic."""
    out: dict[tuple[str, str, str], str] = {}
    for lib in root.iter("library"):
        lname = lib.get("name", "")
        for ds in lib.iter("deviceset"):
            for dev in ds.iter("device"):
                out[(lname, ds.get("name", ""), dev.get("name", ""))] = dev.get("package", "")
    return out


def read_design(path: str | Path, max_items: int = 500) -> dict[str, Any]:
    """Summary of an EAGLE XML design: kind, version, layers in use, libraries, parts/elements, nets/signals."""
    p, root = _load(path)
    drawing = root.find("drawing")
    limit = max(1, min(int(max_items), MAX_ITEMS))
    info: dict[str, Any] = {"file": str(p), "eagle_version": root.get("version", ""), "kind": "unknown"}
    if drawing is None:
        return info
    layers = [{"number": int(ly.get("number", 0)), "name": ly.get("name", "")}
              for ly in drawing.iter("layer") if ly.get("active", "yes") == "yes"]
    sch, brd, lbr = drawing.find("schematic"), drawing.find("board"), drawing.find("library")
    if sch is not None:
        pk = _library_packages(sch)
        parts = []
        for part in sch.iter("part"):
            key = (part.get("library", ""), part.get("deviceset", ""), part.get("device", ""))
            # Without a user value EAGLE shows the device set + device (+ technology) name as the value.
            value = part.get("value") or (key[1] + key[2] + part.get("technology", "")).replace("*", "")
            parts.append({"name": part.get("name"), "value": value, "library": key[0],
                          "deviceset": key[1], "device": key[2], "package": pk.get(key, ""),
                          "technology": part.get("technology", ""), "attributes": _attrs(part)})
        nets = []
        for net in sch.iter("net"):
            pins = [f"{r.get('part')}.{r.get('pin')}" for r in net.iter("pinref")]
            nets.append({"name": net.get("name"), "class": net.get("class", "0"), "pins": pins})
        info.update(kind="schematic", sheets=len(sch.findall("sheets/sheet")),
                    modules=[m.get("name") for m in sch.findall("modules/module")],
                    variants=[v.get("name") for v in sch.iter("variantdef")],
                    libraries=sorted({lib.get("name", "") for lib in sch.iter("library")}),
                    part_count=len(parts), net_count=len(nets), parts=parts[:limit], nets=nets[:limit])
    elif brd is not None:
        elements = [{"name": e.get("name"), "value": e.get("value") or "", "library": e.get("library", ""),
                     "package": e.get("package", ""), "x_mm": float(e.get("x", 0)), "y_mm": float(e.get("y", 0)),
                     "rotation": e.get("rot", "R0"), "attributes": _attrs(e)} for e in brd.iter("element")]
        signals = []
        for sig in brd.iter("signal"):
            signals.append({"name": sig.get("name"), "class": sig.get("class", "0"),
                            "pads": [f"{c.get('element')}.{c.get('pad')}" for c in sig.iter("contactref")],
                            "wires": len(sig.findall("wire")), "vias": len(sig.findall("via")),
                            "polygons": len(sig.findall("polygon"))})
        rules = {prm.get("name"): prm.get("value") for prm in brd.iter("param")}
        outline = [w for w in brd.iter("wire") if w.get("layer") == "20"]
        xs = [float(w.get(k, 0)) for w in outline for k in ("x1", "x2")]
        ys = [float(w.get(k, 0)) for w in outline for k in ("y1", "y2")]
        info.update(kind="board", copper_layers=copper_layer_count(p),
                    size_mm=[round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)] if xs and ys else None,
                    libraries=sorted({lib.get("name", "") for lib in brd.iter("library")}),
                    element_count=len(elements), signal_count=len(signals),
                    unrouted_signals=[s["name"] for s in signals if len(s["pads"]) > 1 and not (s["wires"] or s["polygons"])][:limit],
                    design_rules={k: rules[k] for k in ("layerSetup", "mdWireWire", "msWidth", "msDrill", "rvPadTop")
                                  if k in rules},
                    elements=elements[:limit], signals=signals[:limit])
    elif lbr is not None:
        info.update(kind="library", name=p.stem,
                    packages=[pk.get("name") for pk in lbr.iter("package")][:limit],
                    symbols=[s.get("name") for s in lbr.iter("symbol")][:limit],
                    devicesets=[{"name": d.get("name"), "prefix": d.get("prefix", ""),
                                 "devices": [dv.get("name") or "''" for dv in d.iter("device")]}
                                for d in lbr.iter("deviceset")][:limit])
    info["layers_in_use"] = layers if len(layers) <= 80 else layers[:80]
    return info


def bom(path: str | Path, output_csv: str | None = None) -> dict[str, Any]:
    """Bill of materials grouped by value, package and part-number attributes; optional CSV next to the results."""
    info = read_design(path, max_items=MAX_ITEMS)
    rows = info.get("parts") if info["kind"] == "schematic" else info.get("elements")
    if rows is None:
        raise ValueError("A BOM needs a schematic (.sch) or a board (.brd)")
    groups: dict[tuple, list[str]] = defaultdict(list)
    meta: dict[tuple, dict[str, str]] = {}
    for r in rows:
        if info["kind"] == "schematic" and not r["package"]:
            continue  # frames, supply symbols and other parts without a package are not assembled
        a = {k.upper(): v for k, v in r["attributes"].items()}
        mpn = a.get("MPN") or a.get("MANUFACTURER_PART_NUMBER") or a.get("MPN#") or ""
        mf = a.get("MANUFACTURER") or a.get("MF") or ""
        key = (r["value"], r["package"], mf, mpn, r.get("deviceset", ""))
        groups[key].append(r["name"])
        meta[key] = {"value": r["value"], "package": r["package"], "manufacturer": mf, "mpn": mpn,
                     "deviceset": r.get("deviceset", "")}

    def natural(s: str) -> list:
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]

    items = [{**meta[k], "quantity": len(v), "designators": sorted(v, key=natural)} for k, v in groups.items()]
    items.sort(key=lambda it: natural(it["designators"][0]))
    out: dict[str, Any] = {"file": info["file"], "source": info["kind"], "lines": len(items),
                           "parts": sum(i["quantity"] for i in items), "items": items}
    if output_csv:
        target = resolve_path(output_csv)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Quantity", "Designators", "Value", "Package", "Manufacturer", "MPN"])
            for i in items:
                w.writerow([i["quantity"], " ".join(i["designators"]), i["value"], i["package"], i["manufacturer"], i["mpn"]])
        out["csv"] = str(target)
    return out


def netlist(path: str | Path, output_file: str | None = None) -> dict[str, Any]:
    """Nets of a schematic (part.pin) or signals of a board (element.pad); optional text netlist file."""
    info = read_design(path, max_items=MAX_ITEMS)
    if info["kind"] == "schematic":
        nets = [{"name": n["name"], "class": n["class"], "nodes": n["pins"]} for n in info["nets"]]
    elif info["kind"] == "board":
        nets = [{"name": s["name"], "class": s["class"], "nodes": s["pads"]} for s in info["signals"]]
    else:
        raise ValueError("A netlist needs a schematic (.sch) or a board (.brd)")
    nets = [n for n in nets if n["nodes"]]
    out: dict[str, Any] = {"file": info["file"], "source": info["kind"], "net_count": len(nets), "nets": nets}
    if output_file:
        target = resolve_path(output_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(f"{n['name']}\t{' '.join(n['nodes'])}" for n in nets) + "\n", encoding="utf-8")
        out["netlist_file"] = str(target)
    return out


eagle_adapter = EagleAdapter()
