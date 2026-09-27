"""Catalog loader and search over the per-software tool catalogs.

Catalog layout (see CATALOG_SCHEMA.md):
    catalogs/<software>/meta.json        product description + automation entry points
    catalogs/<software>/NN_<slug>.json   one category per file, {"category", "description", "tools": [...]}
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import CATALOG_DIR

SOFTWARE_ALIASES: dict[str, str] = {
    "feko": "feko", "altair feko": "feko", "feko suite": "feko", "cadfeko": "feko", "postfeko": "feko", "editfeko": "feko",
    "matlab": "matlab",
    "mathworks": "matlab",
    "mathematica": "mathematica",
    "wolfram": "mathematica",
    "wolfram mathematica": "mathematica",
    "wolfram language": "mathematica",
    "wolframscript": "mathematica",
    "comsol": "comsol",
    "comsol multiphysics": "comsol",
    "photoshop": "photoshop",
    "adobe photoshop": "photoshop",
    "ps": "photoshop",
    "orcad": "orcad",
    "orcad x": "orcad",
    "pspice": "orcad",
    "capture": "orcad",
    "cadence": "orcad",
    "allegro": "orcad",
    "altium": "altium",
    "altium designer": "altium",
    "proteus": "proteus",
    "isis": "proteus",
    "ares": "proteus",
    "labcenter": "proteus",
    "vivado": "vivado",
    "amd vivado": "vivado",
    "xilinx": "vivado",
    "xilinx vivado": "vivado",
    "autocad": "autocad",
    "autodesk autocad": "autocad",
    "hfss": "hfss",
    "ansys hfss": "hfss",
    "ansys": "hfss",
    "electronics desktop": "hfss",
    "aedt": "hfss",
    "simulink": "simulink",
    "eagle": "eagle", "autodesk eagle": "eagle", "eagle cad": "eagle", "eaglecad": "eagle", "cadsoft eagle": "eagle",
    "eagle pcb": "eagle", "eagle schematic": "eagle", "eaglecon": "eagle",
    "drawio": "drawio",
    "draw.io": "drawio",
    "diagrams.net": "drawio",
    "diagramsnet": "drawio",
}

KNOWN_SOFTWARE = [
    "matlab", "mathematica", "comsol", "photoshop", "orcad", "altium",
    "proteus", "vivado", "autocad", "hfss", "simulink", "drawio", "feko", "eagle",
]


def resolve_software(name: str) -> str:
    """Map a user-supplied software name/alias to a catalog id. Raises ValueError if unknown."""
    key = (name or "").strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in SOFTWARE_ALIASES:
        return SOFTWARE_ALIASES[key]
    compact = key.replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
    for alias, sid in SOFTWARE_ALIASES.items():
        if alias.replace(" ", "").replace(".", "") == compact:
            return sid
    raise ValueError(f"Unknown software {name!r}. Known ids: {', '.join(KNOWN_SOFTWARE)}")


@dataclass
class ToolEntry:
    software: str
    category: str
    name: str
    kind: str = ""
    description: str = ""
    usage: str = ""
    example: str = ""
    notes: str = ""
    docs: str = ""
    parameters: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self, full: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not full:
            d = {k: d[k] for k in ("software", "category", "name", "kind", "description")}
        return {k: v for k, v in d.items() if v not in ("", [], None)}

    def to_markdown(self) -> str:
        lines = [f"### {self.name}  ({self.software} / {self.category})"]
        if self.kind:
            lines.append(f"*Kind:* {self.kind}")
        if self.description:
            lines.append(f"\n{self.description}")
        if self.usage:
            lines.append(f"\n**Usage**\n```\n{self.usage}\n```")
        if self.parameters:
            lines.append("\n**Parameters**")
            for p in self.parameters:
                lines.append(f"- `{p.get('name', '')}` - {p.get('description', '')}")
        if self.example:
            lines.append(f"\n**Example**\n```\n{self.example}\n```")
        if self.notes:
            lines.append(f"\n**Notes:** {self.notes}")
        if self.docs:
            lines.append(f"\n**Docs:** {self.docs}")
        return "\n".join(lines)


@dataclass
class SoftwareCatalog:
    id: str
    meta: dict[str, Any]
    categories: list[dict[str, Any]]
    tools: list[ToolEntry]

    @property
    def name(self) -> str:
        return self.meta.get("name", self.id)


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_software(software_dir: Path) -> SoftwareCatalog:
    sid = software_dir.name
    meta_path = software_dir / "meta.json"
    meta = _read_json(meta_path) if meta_path.exists() else {"id": sid, "name": sid}
    categories: list[dict[str, Any]] = []
    tools: list[ToolEntry] = []
    for chunk in sorted(software_dir.glob("[0-9]*.json")):
        try:
            data = _read_json(chunk)
        except json.JSONDecodeError as exc:  # keep the server alive even if one file is broken
            categories.append({"name": f"(invalid file {chunk.name})", "description": str(exc), "count": 0})
            continue
        cat_name = data.get("category") or chunk.stem
        entries = data.get("tools", [])
        categories.append({"name": cat_name, "description": data.get("description", ""), "count": len(entries)})
        for raw in entries:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            tools.append(
                ToolEntry(
                    software=sid,
                    category=cat_name,
                    name=str(raw.get("name")),
                    kind=str(raw.get("kind", "")),
                    description=str(raw.get("description", "")),
                    usage=str(raw.get("usage", "")),
                    example=str(raw.get("example", "")),
                    notes=str(raw.get("notes", "")),
                    docs=str(raw.get("docs", "")),
                    parameters=[p for p in raw.get("parameters", []) if isinstance(p, dict)],
                )
            )
    return SoftwareCatalog(id=sid, meta=meta, categories=categories, tools=tools)


class Catalog:
    """In-memory index over all software catalogs."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else CATALOG_DIR
        self._loaded: dict[str, SoftwareCatalog] = {}

    # ---- loading -------------------------------------------------------------------------------
    def software_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        ids = [p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith("_")]
        return sorted(ids, key=lambda s: (KNOWN_SOFTWARE.index(s) if s in KNOWN_SOFTWARE else 99, s))

    def get_software(self, software: str) -> SoftwareCatalog:
        sid = resolve_software(software)
        if sid not in self._loaded:
            path = self.root / sid
            if not path.exists():
                raise FileNotFoundError(f"No catalog folder for {sid!r} at {path}")
            self._loaded[sid] = _load_software(path)
        return self._loaded[sid]

    def reload(self) -> None:
        self._loaded.clear()

    def all(self) -> list[SoftwareCatalog]:
        return [self.get_software(sid) for sid in self.software_ids()]

    # ---- queries -------------------------------------------------------------------------------
    def stats(self) -> list[dict[str, Any]]:
        out = []
        for sc in self.all():
            out.append({
                "id": sc.id,
                "name": sc.name,
                "vendor": sc.meta.get("vendor", ""),
                "version_reference": sc.meta.get("version_reference", ""),
                "categories": len(sc.categories),
                "tools": len(sc.tools),
            })
        return out

    def categories(self, software: str) -> list[dict[str, Any]]:
        return self.get_software(software).categories

    def tools(self, software: str, category: str | None = None, kind: str | None = None) -> list[ToolEntry]:
        sc = self.get_software(software)
        items = sc.tools
        if category:
            c = category.lower()
            items = [t for t in items if c in t.category.lower()]
        if kind:
            k = kind.lower()
            items = [t for t in items if t.kind.lower() == k]
        return items

    def get(self, software: str, name: str) -> ToolEntry | None:
        sc = self.get_software(software)
        target = name.strip().lower()
        for t in sc.tools:
            if t.name.lower() == target:
                return t
        # tolerate punctuation/whitespace differences (e.g. "Place Part (P,P)" vs "place part")
        norm = re.sub(r"[^a-z0-9]", "", target)
        if norm:
            for t in sc.tools:
                if re.sub(r"[^a-z0-9]", "", t.name.lower()) == norm:
                    return t
        for t in sc.tools:
            if t.name.lower().startswith(target) or target in t.name.lower():
                return t
        return None

    def search(self, query: str, software: str | None = None, limit: int = 20,
               kind: str | None = None, category: str | None = None) -> list[tuple[float, ToolEntry]]:
        """Rank tools by a simple weighted term match over name/category/description/usage."""
        terms = [t for t in re.split(r"[^a-z0-9_.#$]+", query.lower()) if t]
        if not terms:
            return []
        pool: Iterable[ToolEntry]
        if software:
            pool = self.tools(software, category=category, kind=kind)
        else:
            pool = [t for sc in self.all() for t in sc.tools]
            if category:
                pool = [t for t in pool if category.lower() in t.category.lower()]
            if kind:
                pool = [t for t in pool if t.kind.lower() == kind.lower()]
        q = query.strip().lower()
        scored: list[tuple[float, ToolEntry]] = []
        for t in pool:
            name = t.name.lower()
            score = 0.0
            if name == q:
                score += 100
            elif name.startswith(q):
                score += 60
            elif q in name:
                score += 40
            cat = t.category.lower()
            desc = t.description.lower()
            usage = t.usage.lower()
            notes = t.notes.lower()
            for term in terms:
                if term == name:
                    score += 30
                elif term in name:
                    score += 12
                if term in cat:
                    score += 6
                if term in desc:
                    score += 4
                if term in usage:
                    score += 3
                if term in notes:
                    score += 1
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: (-x[0], x[1].name.lower()))
        return scored[:limit]


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()
