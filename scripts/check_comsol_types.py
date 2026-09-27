"""Check the COMSOL API type strings used in the catalog against a local COMSOL installation.

Usage:  py scripts/check_comsol_types.py [--root "C:/Program Files/COMSOL/COMSOL64/Multiphysics"]

Collects every ``physics().create("tag", "Type", ...)`` and ``multiphysics().create("tag", "Type", ...)`` call in
the COMSOL catalog chunks (usage/example fields) and reports types that the installation does not know. Known
types come from COMSOL's code-completion data (``data/completion/physics.xml``) the physics, coupling and feature ids registered in the
plugins' plugin.xml files, plus every interface and coupling used by the documented Application Library Java scripts. For each unknown type the known types for the same default tag are shown when available.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mcp_adapter.adapters.comsol_library import build_index  # noqa: E402

CALL = re.compile(r'(multi)?physics\(\)\.create\(\\?"([A-Za-z0-9_]+)\\?",\s*\\?"([A-Za-z0-9_]+)\\?"')
PLUGIN_ID = re.compile(r'<(?:physicsid|couplingid|featureid)\s+id="([A-Za-z0-9_]+)"')
ELEMENT = re.compile(r'<([A-Za-z][A-Za-z0-9_]*) [^>]*\bid="([A-Za-z0-9_]+)"')


def known_types(root: Path) -> tuple[set[str], dict[str, set[str]]]:
    types: set[str] = set()
    by_tag: dict[str, set[str]] = defaultdict(set)
    f = root / "data" / "completion" / "physics.xml"
    if f.exists():
        for typ, tag in ELEMENT.findall(f.read_text(encoding="utf-8", errors="replace")):
            types.add(typ)
            by_tag[tag].add(typ)
    for jar in sorted((root / "plugins").glob("*.jar")):
        try:
            with zipfile.ZipFile(jar) as z:
                if "plugin.xml" not in z.namelist():
                    continue
                xml = z.read("plugin.xml").decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile):
            continue
        types.update(PLUGIN_ID.findall(xml))
    for ex in build_index(root)["examples"]:
        for item in ex["physics"] + ex["multiphysics"]:
            tag, typ = item.split(": ", 1)
            types.add(typ)
            by_tag[re.sub(r"\d+$", "", tag)].add(typ)
    return types, by_tag


def check(root: Path) -> list[tuple[str, str, str, str, list[str]]]:
    types, by_tag = known_types(root)
    if not types:
        raise SystemExit("no COMSOL type information found under the given --root")
    problems = []
    for f in sorted(glob.glob(str(REPO / "mcp_adapter" / "catalogs" / "comsol" / "[0-9]*.json"))):
        for t in json.load(open(f, encoding="utf-8"))["tools"]:
            blob = " ".join(str(t.get(k, "")) for k in ("usage", "example"))
            for _multi, tag, typ in CALL.findall(blob):
                if typ not in types:
                    hint = sorted(by_tag.get(re.sub(r"\d+$", "", tag), set()))
                    problems.append((Path(f).name, t["name"], tag, typ, hint))
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="C:/Program Files/COMSOL/COMSOL64/Multiphysics")
    args = ap.parse_args()
    problems = check(Path(args.root))
    seen = set()
    for fname, name, tag, typ, hint in problems:
        key = (fname, name, typ)
        if key in seen:
            continue
        seen.add(key)
        print(f"{fname} | {name[:70]} | {tag} -> {typ} | known for tag: {', '.join(hint) or '-'}")
    print(f"{len(seen)} unknown type strings")


if __name__ == "__main__":
    main()
