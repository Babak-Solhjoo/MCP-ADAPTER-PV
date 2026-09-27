"""draw.io (diagrams.net) adapter.

* Pure-Python diagram generation: build mxGraph XML from nodes/edges with automatic layered layout,
  decode/encode compressed diagram data.
* Export through the desktop CLI (``draw.io -x -f png -o out.png in.drawio``) when installed.
"""
from __future__ import annotations

import base64
import html
import os
import re
import sys
import urllib.parse
import zlib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from .base import BaseAdapter, RunResult, resolve_path

SHAPE_STYLES: dict[str, str] = {
    "rectangle": "rounded=0;whiteSpace=wrap;html=1;",
    "rounded": "rounded=1;whiteSpace=wrap;html=1;",
    "ellipse": "ellipse;whiteSpace=wrap;html=1;",
    "circle": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;",
    "rhombus": "rhombus;whiteSpace=wrap;html=1;",
    "diamond": "rhombus;whiteSpace=wrap;html=1;",
    "decision": "rhombus;whiteSpace=wrap;html=1;",
    "process": "rounded=1;whiteSpace=wrap;html=1;",
    "terminator": "rounded=1;whiteSpace=wrap;html=1;arcSize=50;",
    "start": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#d5e8d4;strokeColor=#82b366;",
    "end": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#f8cecc;strokeColor=#b85450;",
    "document": "shape=document;whiteSpace=wrap;html=1;boundedLbl=1;",
    "cylinder": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;",
    "database": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;",
    "cloud": "ellipse;shape=cloud;whiteSpace=wrap;html=1;",
    "note": "shape=note;whiteSpace=wrap;html=1;backgroundOutline=1;darkOpacity=0.05;size=14;",
    "hexagon": "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;",
    "parallelogram": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;",
    "data": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;",
    "triangle": "triangle;whiteSpace=wrap;html=1;",
    "actor": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;",
    "text": "text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;",
    "card": "shape=card;whiteSpace=wrap;html=1;",
    "step": "shape=step;perimeter=stepPerimeter;whiteSpace=wrap;html=1;",
    "container": "swimlane;whiteSpace=wrap;html=1;",
    "swimlane": "swimlane;whiteSpace=wrap;html=1;",
    "cube": "shape=cube;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;darkOpacity=0.05;",
    "component": "shape=component;align=left;spacingLeft=36;whiteSpace=wrap;html=1;",
    "umlclass": "swimlane;fontStyle=1;align=center;verticalAlign=top;childLayout=stackLayout;html=1;",
}

EDGE_STYLE_DEFAULT = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _layered_positions(node_ids: list[str], edges: list[tuple[str, str]], direction: str = "TB",
                       width: int = 120, height: int = 60, gap_x: int = 60, gap_y: int = 60) -> dict[str, tuple[int, int]]:
    """Assign layers with a longest-path scheme (cycles broken by BFS order) and stack nodes per layer."""
    succ: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {n: 0 for n in node_ids}
    for a, b in edges:
        if a in indeg and b in indeg and a != b:
            succ[a].append(b)
            indeg[b] += 1
    layer: dict[str, int] = {}
    queue = deque(n for n in node_ids if indeg[n] == 0)
    remaining = dict(indeg)
    while queue:
        n = queue.popleft()
        layer.setdefault(n, 0)
        for m in succ[n]:
            layer[m] = max(layer.get(m, 0), layer[n] + 1)
            remaining[m] -= 1
            if remaining[m] == 0:
                queue.append(m)
    for n in node_ids:  # nodes inside cycles
        if n not in layer:
            preds = [layer[a] for a, b in edges if b == n and a in layer]
            layer[n] = (max(preds) + 1) if preds else 0
    by_layer: dict[int, list[str]] = defaultdict(list)
    for n in node_ids:
        by_layer[layer[n]].append(n)
    positions: dict[str, tuple[int, int]] = {}
    max_count = max(len(v) for v in by_layer.values()) if by_layer else 1
    for lv, members in sorted(by_layer.items()):
        span = len(members)
        for idx, n in enumerate(members):
            offset = (max_count - span) * (width + gap_x) // 2
            if direction.upper() == "LR":
                x = 40 + lv * (width + gap_x * 2)
                y = 40 + offset + idx * (height + gap_y)
            else:
                x = 40 + offset + idx * (width + gap_x)
                y = 40 + lv * (height + gap_y * 2)
            positions[n] = (x, y)
    return positions


def build_diagram_xml(nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None,
                      direction: str = "TB", title: str = "Page-1", node_width: int = 120,
                      node_height: int = 60) -> str:
    """Build an uncompressed draw.io file (mxfile) from node and edge dictionaries.

    node: {"id": "a", "label": "Start", "shape": "start", "style": "fillColor=#fff;", "x": 10, "y": 10,
           "width": 120, "height": 60, "parent": "container-id"}
    edge: {"source": "a", "target": "b", "label": "yes", "style": "dashed=1;", "id": "e1"}
    """
    edges = edges or []
    ids = [str(n["id"]) for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("node ids must be unique")
    auto = _layered_positions(ids, [(str(e["source"]), str(e["target"])) for e in edges], direction,
                              node_width, node_height)
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for n in nodes:
        nid = str(n["id"])
        shape = str(n.get("shape", "rectangle")).lower()
        style = SHAPE_STYLES.get(shape, SHAPE_STYLES["rectangle"]) + str(n.get("style", ""))
        w = int(n.get("width", node_width))
        h = int(n.get("height", node_height))
        x, y = auto[nid]
        x = int(n.get("x", x))
        y = int(n.get("y", y))
        parent = _esc(n.get("parent", "1"))
        cells.append(
            f'<mxCell id={quoteattr(nid)} value={quoteattr(str(n.get("label", nid)))} style={quoteattr(style)} '
            f'vertex="1" parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
    for i, e in enumerate(edges):
        eid = str(e.get("id", f"e{i + 1}"))
        style = EDGE_STYLE_DEFAULT + str(e.get("style", ""))
        cells.append(
            f'<mxCell id={quoteattr(eid)} value={quoteattr(str(e.get("label", "")))} style={quoteattr(style)} '
            f'edge="1" parent="1" source={quoteattr(str(e["source"]))} target={quoteattr(str(e["target"]))}>'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
    model = (
        '<mxGraphModel dx="1000" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
        'fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="827" math="0" shadow="0"><root>'
        + "".join(cells) + "</root></mxGraphModel>"
    )
    return (
        '<mxfile host="mcp-adapter" type="device" version="26.0.0">'
        f'<diagram id="mcp-{re.sub(r"[^a-zA-Z0-9]", "-", title)}" name={quoteattr(title)}>{model}</diagram></mxfile>'
    )


MAX_INFLATED_BYTES = 50 * 1024 * 1024  # a real diagram is far smaller; guards against decompression bombs


def decode_diagram(data: str, max_bytes: int = MAX_INFLATED_BYTES) -> str:
    """Decode the compressed <diagram> payload (base64 + raw deflate + URL encoding) to XML.

    Inflation stops at *max_bytes*: a few kilobytes of crafted deflate data could otherwise expand to gigabytes."""
    raw = base64.b64decode(data.strip())
    d = zlib.decompressobj(-15)
    inflated = d.decompress(raw, max_bytes + 1)
    if len(inflated) > max_bytes or d.unconsumed_tail:
        raise ValueError(f"diagram data expands beyond {max_bytes // (1024 * 1024)} MB; refusing to decode it")
    return urllib.parse.unquote(inflated.decode("utf-8"))


def encode_diagram(xml_text: str) -> str:
    """Compress mxGraphModel XML the way draw.io does."""
    quoted = urllib.parse.quote(xml_text, safe="")
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = comp.compress(quoted.encode("utf-8")) + comp.flush()
    return base64.b64encode(data).decode("ascii")


def read_diagram_file(path: str) -> dict[str, Any]:
    """Read a .drawio/.xml file and return its pages as uncompressed mxGraphModel XML plus cell summary."""
    text = Path(resolve_path(path)).read_text(encoding="utf-8")
    pages = []
    for m in re.finditer(r"<diagram([^>]*)>(.*?)</diagram>", text, flags=re.DOTALL):
        attrs, body = m.group(1), m.group(2).strip()
        name = re.search(r'name="([^"]*)"', attrs)
        if body.startswith("<mxGraphModel"):
            xml_body = body
        else:
            try:
                xml_body = decode_diagram(body)
            except Exception as exc:
                xml_body = f"<!-- could not decode: {exc} -->"
        vertices = re.findall(r'<mxCell[^>]*vertex="1"[^>]*>', xml_body)
        edge_cells = re.findall(r'<mxCell[^>]*edge="1"[^>]*>', xml_body)
        labels = [html.unescape(v) for v in re.findall(r'value="([^"]*)"', " ".join(vertices))]
        pages.append({"name": html.unescape(name.group(1)) if name else "", "vertices": len(vertices),
                      "edges": len(edge_cells), "labels": labels[:200], "xml": xml_body})
    return {"file": str(resolve_path(path)), "pages": pages}


class DrawioAdapter(BaseAdapter):
    id = "drawio"
    name = "draw.io Desktop"
    env_var = "DRAWIO_EXE"
    exe_names = ["draw.io", "drawio", "draw.io.exe"]
    exe_patterns = [
        "draw.io/draw.io.exe",
        "draw.io.app/Contents/MacOS/draw.io",
        "/opt/drawio/drawio",
        "/usr/bin/drawio",
        "/snap/bin/drawio",
    ]
    install_hint = "Install draw.io Desktop (github.com/jgraph/drawio-desktop) for PNG/PDF/SVG export; XML generation works without it."

    def create_diagram(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None,
                       output_file: str | None = None, direction: str = "TB", title: str = "Page-1",
                       export_format: str | None = None) -> RunResult:
        """Generate a .drawio file from nodes/edges; optionally export it (png/svg/pdf) via the CLI."""
        try:
            xml_text = build_diagram_xml(nodes, edges, direction, title)
        except (KeyError, ValueError) as exc:
            return RunResult(ok=False, software=self.id, command="", error=f"Invalid diagram spec: {exc}")
        out = resolve_path(output_file) if output_file else self.scripts_dir() / f"diagram_{title}.drawio"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(xml_text, encoding="utf-8")
        res = RunResult(ok=True, software=self.id, command="build_diagram_xml", returncode=0,
                        artifacts={"drawio_file": str(out)}, data={"nodes": len(nodes), "edges": len(edges or [])})
        if export_format:
            exp = self.export(str(out), str(out.with_suffix("." + export_format.lower())), export_format)
            res.artifacts.update(exp.artifacts)
            res.stdout = exp.stdout
            if not exp.ok:
                res.stderr = exp.stderr
                res.error = f"Diagram written but export failed: {exp.error}"
        return res

    def flowchart(self, steps: list[str], output_file: str | None = None, title: str = "Flowchart",
                  direction: str = "TB", export_format: str | None = None) -> RunResult:
        """Quick linear flowchart: Start -> step1 -> step2 ... -> End."""
        nodes = [{"id": "start", "label": "Start", "shape": "start", "width": 80, "height": 80}]
        edges = []
        prev = "start"
        for i, s in enumerate(steps, 1):
            nid = f"s{i}"
            shape = "decision" if s.rstrip().endswith("?") else "process"
            nodes.append({"id": nid, "label": s, "shape": shape, "width": 160, "height": 70})
            edges.append({"source": prev, "target": nid})
            prev = nid
        nodes.append({"id": "end", "label": "End", "shape": "end", "width": 80, "height": 80})
        edges.append({"source": prev, "target": "end"})
        return self.create_diagram(nodes, edges, output_file, direction, title, export_format)

    def export(self, input_file: str, output_file: str, export_format: str = "png", page_index: int | None = None,
               all_pages: bool = False, transparent: bool = False, scale: float | None = None,
               border: int | None = None, width: int | None = None, height: int | None = None,
               crop: bool = False, embed_diagram: bool = False, timeout: int | None = None) -> RunResult:
        """Export a .drawio file with the desktop CLI (formats: png, jpg, svg, pdf, vsdx, xml)."""
        if not self.is_available():
            return self.unavailable("XML generation still works without the executable.")
        src = resolve_path(input_file)
        if not src.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {src}")
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [self.executable() or "draw.io", "-x", "-f", export_format.lower(), "-o", str(out)]
        if page_index is not None:
            args += ["-p", str(page_index)]
        if all_pages:
            args.append("-a")
        if transparent:
            args.append("-t")
        if scale:
            args += ["-s", str(scale)]
        if border is not None:
            args += ["-b", str(border)]
        if width:
            args += ["--width", str(width)]
        if height:
            args += ["--height", str(height)]
        if crop:
            args.append("--crop")
        if embed_diagram:
            args.append("-e")
        if sys.platform.startswith("linux") and os.geteuid() == 0:  # type: ignore[attr-defined]
            args.append("--no-sandbox")
        args.append(str(src))
        res = self.run_command(args, timeout=timeout, artifacts={"output_file": str(out)})
        if res.ok and not out.exists():
            res.ok = False
            res.error = "draw.io exited without writing the output file."
        return res

    def read(self, path: str) -> RunResult:
        try:
            return RunResult(ok=True, software=self.id, command="read_diagram_file", returncode=0, data=read_diagram_file(path))
        except (OSError, ValueError) as exc:
            return RunResult(ok=False, software=self.id, command="read_diagram_file", error=str(exc))


drawio_adapter = DrawioAdapter()
