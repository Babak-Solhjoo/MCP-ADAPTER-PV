"""Optional live documentation search backed by the Tavily API (needs TAVILY_API in .env)."""
from __future__ import annotations

from typing import Any

import httpx

from .config import allow_internet, tavily_key

try:  # use the OS certificate store when available (fixes corporate-proxy SSL errors)
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

DOC_DOMAINS: dict[str, list[str]] = {
    "matlab": ["mathworks.com"],
    "simulink": ["mathworks.com"],
    "mathematica": ["reference.wolfram.com", "wolfram.com"],
    "comsol": ["comsol.com", "doc.comsol.com"],
    "photoshop": ["helpx.adobe.com", "developer.adobe.com", "adobe.com"],
    "orcad": ["cadence.com", "orcad.com", "ema-eda.com"],
    "altium": ["altium.com"],
    "proteus": ["labcenter.com"],
    "vivado": ["docs.amd.com", "xilinx.com", "amd.com", "adaptivesupport.amd.com"],
    "autocad": ["help.autodesk.com", "autodesk.com", "knowledge.autodesk.com"],
    "hfss": ["ansys.com", "ansyshelp.ansys.com", "aedt.docs.pyansys.com", "innovationspace.ansys.com"],
    "drawio": ["drawio.com", "diagrams.net", "github.com"],
}

SOFTWARE_LABEL = {
    "matlab": "MATLAB", "simulink": "Simulink", "mathematica": "Wolfram Mathematica",
    "comsol": "COMSOL Multiphysics", "photoshop": "Adobe Photoshop", "orcad": "OrCAD",
    "altium": "Altium Designer", "proteus": "Proteus", "vivado": "Vivado", "autocad": "AutoCAD",
    "hfss": "Ansys HFSS", "drawio": "draw.io",
}


def is_enabled() -> bool:
    """Live search needs both a Tavily key and the MCP_ADAPTER_ALLOW_INTERNET opt-in."""
    return tavily_key() is not None and allow_internet()


def search_docs(query: str, software: str | None = None, max_results: int = 5,
                depth: str = "basic", restrict_to_vendor: bool = True) -> dict[str, Any]:
    if not allow_internet():
        return {
            "ok": False,
            "error": "Internet access is disabled (MCP_ADAPTER_ALLOW_INTERNET=false). Run mcp-adapter-setup or "
                     "set MCP_ADAPTER_ALLOW_INTERNET=true in .env to enable live documentation search.",
        }
    key = tavily_key()
    if not key:
        return {
            "ok": False,
            "error": "TAVILY_API is not configured. Add it to .env to enable live documentation search.",
        }
    full_query = f"{SOFTWARE_LABEL.get(software, software)} {query}" if software else query
    payload: dict[str, Any] = {
        "api_key": key,
        "query": full_query,
        "max_results": max(1, min(max_results, 10)),
        "search_depth": "advanced" if depth == "advanced" else "basic",
        "include_answer": True,
    }
    if software and restrict_to_vendor and software in DOC_DOMAINS:
        payload["include_domains"] = DOC_DOMAINS[software]
    try:
        resp = httpx.post("https://api.tavily.com/search", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Tavily request failed: {exc}", "query": full_query}
    results = [
        {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content"), "score": r.get("score")}
        for r in data.get("results", [])
    ]
    return {"ok": True, "query": full_query, "answer": data.get("answer"), "results": results}
