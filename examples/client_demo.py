"""Minimal MCP client that launches the server over stdio, lists tools and calls a few of them.

    python examples/client_demo.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _text(result) -> str:
    if getattr(result, "structuredContent", None):
        return json.dumps(result.structuredContent, indent=2)[:1500]
    return "\n".join(getattr(c, "text", "") for c in result.content)[:1500]


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_adapter.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"{len(tools.tools)} tools available, e.g.: {[t.name for t in tools.tools[:8]]}\n")

            print("--- time_now ---")
            print(_text(await session.call_tool("time_now", {"timezone": "Europe/Berlin"})))

            print("\n--- search_tools ---")
            print(_text(await session.call_tool("search_tools", {"query": "low pass filter", "software": "matlab", "limit": 5})))

            print("\n--- describe_tool ---")
            print(_text(await session.call_tool("describe_tool", {"software": "vivado", "name": "launch_runs"})))

            print("\n--- adapter_status ---")
            print(_text(await session.call_tool("adapter_status", {})))


if __name__ == "__main__":
    asyncio.run(main())
