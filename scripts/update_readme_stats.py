"""Regenerate the catalog statistics table in README.md between the CATALOG_STATS markers."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_adapter.catalog import Catalog  # noqa: E402

START = "<!-- CATALOG_STATS -->"
END = "<!-- /CATALOG_STATS -->"


def render_table() -> str:
    rows = Catalog().stats()
    lines = ["| Software | Vendor | Reference version | Categories | Catalog entries |", "|---|---|---|---:|---:|"]
    total = 0
    for r in rows:
        total += r["tools"]
        lines.append(f"| {r['name']} | {r['vendor']} | {r['version_reference']} | {r['categories']} | {r['tools']} |")
    lines.append(f"| **Total** | | | | **{total}** |")
    return "\n".join(lines)


def main() -> None:
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    block = f"{START}\n{render_table()}\n{END}"
    if END in text:
        new = re.sub(re.escape(START) + r".*?" + re.escape(END), block, text, flags=re.DOTALL)
    else:
        new = text.replace(START, block, 1)
    readme.write_text(new, encoding="utf-8")
    print(render_table())


if __name__ == "__main__":
    main()
