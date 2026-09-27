"""Compatibility shim over the MCP Python SDK.

mcp 1.x exposes ``mcp.server.fastmcp.FastMCP``; mcp 2.x renamed it to
``mcp.server.mcpserver.MCPServer``. Both provide the same decorator-based API used here
(`.tool()`, `.resource()`, `.prompt()`, `.run(transport=...)`), but they configure the HTTP
bind address differently, which ``run_server`` hides.
"""
from __future__ import annotations

from typing import Any

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as ServerClass  # type: ignore
    MCP_MAJOR = 2
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as ServerClass  # type: ignore
    MCP_MAJOR = 1


def create_server(name: str, instructions: str, version: str = "") -> ServerClass:
    kwargs = {"name": name, "instructions": instructions}
    if MCP_MAJOR >= 2 and version:
        kwargs["version"] = version
    return ServerClass(**kwargs)


def run_server(server: ServerClass, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000,
               transport_security: Any = None, auth_token: str | None = None) -> None:
    """Run the server; for HTTP transports apply the bind address and transport-security settings, and with
    *auth_token* require 'Authorization: Bearer <token>' on every request."""
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if auth_token:
        _run_http_with_token(server, transport, host, port, transport_security, auth_token)
        return
    if MCP_MAJOR >= 2:
        kwargs: dict[str, Any] = {"host": host, "port": port}
        if transport_security is not None:
            kwargs["transport_security"] = transport_security
        server.run(transport=transport, **kwargs)  # type: ignore[arg-type]
        return
    settings = server.settings  # type: ignore[attr-defined]
    settings.host = host
    settings.port = port
    if transport_security is not None and hasattr(settings, "transport_security"):
        settings.transport_security = transport_security
    server.run(transport=transport)  # type: ignore[arg-type]


def build_http_app(server: ServerClass, transport: str, host: str, port: int, transport_security: Any = None) -> Any:
    """The Starlette app of an HTTP transport (streamable-http or sse)."""
    if MCP_MAJOR >= 2:
        kwargs: dict[str, Any] = {"host": host}
        if transport_security is not None:
            kwargs["transport_security"] = transport_security
        return server.streamable_http_app(**kwargs) if transport == "streamable-http" else server.sse_app(**kwargs)
    settings = server.settings  # type: ignore[attr-defined]
    settings.host, settings.port = host, port
    if transport_security is not None and hasattr(settings, "transport_security"):
        settings.transport_security = transport_security
    return server.streamable_http_app() if transport == "streamable-http" else server.sse_app()


def _run_http_with_token(server: ServerClass, transport: str, host: str, port: int, transport_security: Any,
                         token: str) -> None:
    import uvicorn

    from .http_auth import BearerTokenMiddleware

    app = BearerTokenMiddleware(build_http_app(server, transport, host, port, transport_security), token)
    uvicorn.run(app, host=host, port=port, log_level="info")


__all__ = ["ServerClass", "create_server", "run_server", "build_http_app", "MCP_MAJOR"]
