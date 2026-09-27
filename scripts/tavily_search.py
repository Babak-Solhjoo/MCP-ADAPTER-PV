"""Small Tavily search helper used while researching software tool catalogs.

Usage:
    py scripts/tavily_search.py "MATLAB signal processing toolbox function list" [--max 5] [--depth basic|advanced]

Reads TAVILY_API (or TAVILY_API_KEY) from the environment or a .env file in the repo root.
Prints a compact JSON list of {title, url, content} results to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252, which cannot print emoji in results
    sys.stdout.reconfigure(encoding="utf-8")

import httpx

try:  # use the OS certificate store when available (fixes corporate-proxy SSL errors)
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass


def load_key() -> str:
    key = os.environ.get("TAVILY_API") or os.environ.get("TAVILY_API_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TAVILY_API") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("TAVILY_API key not found in environment or .env")


def search(query: str, max_results: int = 5, depth: str = "basic", include_raw: bool = False) -> list[dict]:
    payload = {
        "api_key": load_key(),
        "query": query,
        "max_results": max_results,
        "search_depth": depth,
        "include_answer": False,
        "include_raw_content": include_raw,
    }
    resp = httpx.post("https://api.tavily.com/search", json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("results", []):
        item = {"title": r.get("title"), "url": r.get("url"), "content": r.get("content")}
        if include_raw and r.get("raw_content"):
            item["raw_content"] = r["raw_content"][:20000]
        out.append(item)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--depth", choices=["basic", "advanced"], default="basic")
    parser.add_argument("--raw", action="store_true", help="include raw page content (truncated)")
    args = parser.parse_args()
    results = search(args.query, args.max, args.depth, args.raw)
    json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
