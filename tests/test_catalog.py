"""Validates every catalog JSON file against CATALOG_SCHEMA.md and exercises the search API."""
import json
from pathlib import Path

import pytest

from mcp_adapter.catalog import KNOWN_SOFTWARE, Catalog, resolve_software
from mcp_adapter.config import CATALOG_DIR

REQUIRED = ("name", "kind", "description", "usage", "example")
ALLOWED_KINDS = {
    "function", "command", "tcl-command", "menu", "tool", "panel", "block", "api", "script-object",
    "system-variable", "filter", "adjustment", "physics-interface", "study", "mesh", "boundary",
    "excitation", "shortcut", "library", "format", "solver", "result", "reference", "example",
}

chunk_files = sorted(p for p in CATALOG_DIR.glob("*/[0-9]*.json"))


@pytest.mark.parametrize("path", chunk_files, ids=[f"{p.parent.name}/{p.name}" for p in chunk_files])
def test_chunk_file_is_valid(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("category"), "category missing"
    tools = data.get("tools")
    assert isinstance(tools, list) and tools, "tools list missing/empty"
    assert len(tools) <= 50, "chunk files should hold about 40 tools at most"
    names = set()
    for tool in tools:
        for field in REQUIRED:
            assert tool.get(field), f"{tool.get('name')}: missing {field}"
        assert tool["kind"] in ALLOWED_KINDS, f"{tool['name']}: unknown kind {tool['kind']}"
        for p in tool.get("parameters", []):
            assert "name" in p and "description" in p
        key = tool["name"].lower()
        assert key not in names, f"duplicate tool {tool['name']} in {path.name}"
        names.add(key)


@pytest.mark.parametrize("software", [d.name for d in CATALOG_DIR.iterdir() if d.is_dir()])
def test_meta_file(software: str):
    meta_path = CATALOG_DIR / software / "meta.json"
    assert meta_path.exists(), "meta.json missing"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["id"] == software
    for field in ("name", "vendor", "description", "automation"):
        assert meta.get(field), f"meta.{field} missing"
    assert meta["automation"].get("summary")


def test_resolve_aliases():
    assert resolve_software("Wolfram Mathematica") == "mathematica"
    assert resolve_software("draw.io") == "drawio"
    assert resolve_software("AMD Vivado") == "vivado"
    assert resolve_software("ansys hfss") == "hfss"
    with pytest.raises(ValueError):
        resolve_software("kicad")


def test_search_and_get():
    cat = Catalog()
    ids = cat.software_ids()
    assert set(ids) >= {"matlab", "mathematica"}
    hits = cat.search("fourier transform", software="matlab", limit=5)
    assert hits and any(h[1].name.lower() == "fft" for h in hits)
    entry = cat.get("matlab", "FFT")
    assert entry is not None and entry.usage
    md = entry.to_markdown()
    assert "Usage" in md and "Example" in md


def test_known_software_have_catalogs():
    cat = Catalog()
    present = set(cat.software_ids())
    missing = [s for s in KNOWN_SOFTWARE if s not in present]
    assert not missing, f"missing catalogs for {missing}"
    for s in KNOWN_SOFTWARE:
        assert len(cat.get_software(s).tools) >= 50, f"{s} catalog is too small"
