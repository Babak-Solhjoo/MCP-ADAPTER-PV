"""Generate catalog chunk files for the COMSOL Application Library examples from a local COMSOL installation.

Usage:  py scripts/build_comsol_examples.py [--root "C:/Program Files/COMSOL/COMSOL64/Multiphysics"]

Every example that has a documentation page becomes one catalog entry of kind "example" with factual data only:
title, module, topic, physics interfaces, multiphysics couplings and study steps (parsed from the documented Java
build script), where to open it and how to run it through the MCP tools. No documentation prose is copied.
Output: mcp_adapter/catalogs/comsol/99_examples_<module>_<nn>.json (at most 45 entries per file).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mcp_adapter.adapters.comsol_library import build_index, find_root, type_name  # noqa: E402

OUT_DIR = REPO / "mcp_adapter" / "catalogs" / "comsol"
PER_FILE = 45
DISPLAY = {
    "ACDC_Module": "AC/DC Module", "COMSOL_Multiphysics": "COMSOL Multiphysics",
    "CFD_Module": "CFD Module", "RF_Module": "RF Module", "MEMS_Module": "MEMS Module",
    "Fuel_Cell_and_Electrolyzer_Module": "Fuel Cell & Electrolyzer Module",
    "Liquid_and_Gas_Properties_Module": "Liquid & Gas Properties Module",
    "LiveLink_for_PTC_Creo_Parametric": "LiveLink for PTC Creo Parametric",
}
_PHYS_LINE = re.compile(r'^\s*model\.(?:component\("[^"]+"\)\.)?(?:physics|multiphysics)\(\)\.create\(.*\);\s*$'
                        r'|^\s*model\.study\("[^"]+"\)\.create\(.*\);\s*$')


def display(module: str) -> str:
    return DISPLAY.get(module, module.replace("_", " "))


def slug(module: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", module.lower()).strip("_").replace("_module", "")


def java_excerpt(java: str, limit: int = 8) -> str:
    try:
        lines = Path(java).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    picked = list(dict.fromkeys(ln.strip() for ln in lines if _PHYS_LINE.match(ln)))
    return "\n".join(picked[:limit])


def entry(ex: dict) -> dict:
    mod = display(ex["module"])
    topic = ex["topic"].replace("_", " ") or "(top level)"
    phys = [p.split(": ", 1) for p in ex["physics"]]
    multi = [p.split(": ", 1) for p in ex["multiphysics"]]
    studies = ex["studies"]
    parts = [f"Application Library example of the {mod} ({topic}): {ex['title']}."]
    if phys:
        parts.append("Physics interfaces: " + ", ".join(f"{type_name(t)} ({tag})" for tag, t in phys) + ".")
    if multi:
        parts.append("Multiphysics couplings: " + ", ".join(type_name(t) for _, t in multi) + ".")
    if studies:
        parts.append("Study steps: " + ", ".join(type_name(s) for s in studies) + ".")
    parts.append("A ready model to start from for a similar simulation.")
    code = ex["html"].split("com.comsol.help.models.")[-1].split("\\")[0].split("/")[0] if ex["html"] else ""
    docs = (f"https://doc.comsol.com/6.3/doc/com.comsol.help.models.{code}/{ex['name']}.html" if code
            else "https://www.comsol.com/models")
    excerpt = java_excerpt(ex["java"]) if ex["java"] else ""
    return {
        "name": f"{ex['title']} [{mod} example]",
        "kind": "example",
        "description": " ".join(parts),
        "usage": (f"MCP: comsol_run_example(\"{ex['library_path']}\") to solve it, comsol_example_info(\"{ex['name']}\", "
                  f"include_java=true) for its build script. COMSOL: File > Application Libraries > "
                  f"{ex['library_path'].replace('/', ' > ').replace('_', ' ')}"),
        "parameters": ([{"name": tag, "description": f"physics interface {type_name(t)} (API type {t})"} for tag, t in phys]
                       + [{"name": tag, "description": f"multiphysics coupling {type_name(t)} (API type {t})"} for tag, t in multi]
                       + [{"name": f"study step {i}", "description": f"{type_name(st)} (API type {st})"} for i, st in enumerate(studies, 1)]),
        "example": excerpt or f"comsol_run_example(\"{ex['name']}\")",
        "notes": (f"Library path {ex['library_path']}; model file {ex['size_mb']} MB. "
                  + ("Documented Java build script available (rebuild with comsol_run_example method='java'). "
                     if ex["java"] else "")
                  + ("The installed library file is a preview (download the full model in COMSOL); "
                     "comsol_run_example rebuilds it from the Java script instead. " if ex.get("preview") else "")
                  + "Requires the module licence to open and solve."),
        "docs": docs,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    root = Path(args.root) if args.root else find_root(
        str(Path("C:/Program Files/COMSOL/COMSOL64/Multiphysics/bin/win64/comsolbatch.exe")))
    if root is None or not (root / "applications").is_dir():
        raise SystemExit("COMSOL installation not found; pass --root")
    index = build_index(root)
    for old in OUT_DIR.glob("99_examples_*.json"):
        old.unlink()
    by_module: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for ex in index["examples"]:
        if not ex["html"]:
            continue  # no documentation page: auxiliary files, geometry parts, app variants
        e = entry(ex)
        key = e["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        by_module.setdefault(ex["module"], []).append(e)
    total = files = 0
    for module, entries in sorted(by_module.items()):
        entries.sort(key=lambda e: e["name"].lower())
        chunks = [entries[i:i + PER_FILE] for i in range(0, len(entries), PER_FILE)]
        for n, chunk in enumerate(chunks, 1):
            suffix = f" ({n}/{len(chunks)})" if len(chunks) > 1 else ""
            data = {
                "category": f"Application Library examples: {display(module)}{suffix}",
                "description": (f"Ready-made, documented example simulations installed with the {display(module)}. "
                                "Each lists its physics interfaces, couplings and study steps; solve one with "
                                "comsol_run_example or adapt its Java build script."),
                "tools": chunk,
            }
            path = OUT_DIR / f"99_examples_{slug(module)}_{n:02d}.json"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            total += len(chunk)
            files += 1
    print(f"wrote {total} example entries in {files} files for {len(by_module)} modules")


if __name__ == "__main__":
    main()
