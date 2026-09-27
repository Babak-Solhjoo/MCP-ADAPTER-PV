"""Task-driven agent that works through the MCP-ADAPTER tools (``mcp-adapter-agent``).

The MCP server is the capability layer; this package is the agent layer: it launches the server over
stdio, gives Claude every tool the server exposes, runs the agentic loop until the task is finished and
writes a Markdown report. Only the model calls leave the machine.
"""

__version__ = "0.1.0"
