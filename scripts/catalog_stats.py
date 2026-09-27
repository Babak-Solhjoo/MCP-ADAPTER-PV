"""Print a Markdown table with the size of every software catalog (used in README and CI)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_adapter.catalog import Catalog  # noqa: E402


def main() -> None:
    cat = Catalog()
    rows = cat.stats()
    print("| Software | Vendor | Reference version | Categories | Catalog entries |")
    print("|---|---|---|---:|---:|")
    total = 0
    for r in rows:
        total += r["tools"]
        print(f"| {r['name']} | {r['vendor']} | {r['version_reference']} | {r['categories']} | {r['tools']} |")
    print(f"| **Total** | | | | **{total}** |")


if __name__ == "__main__":
    main()
