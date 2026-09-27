"""Bearer-token authentication for the HTTP transports (streamable-http and SSE).

In ``network`` mode the server refuses to start without ``MCP_ADAPTER_AUTH_TOKEN``; every HTTP request must then
carry ``Authorization: Bearer <token>``. In ``local`` mode the token is optional (loopback binding and Host/Origin
checks already keep other machines out) but is enforced whenever it is set. stdio opens no socket and needs none.
"""
from __future__ import annotations

import hmac
import json
import secrets
from typing import Any

MIN_TOKEN_LENGTH = 24


def generate_token() -> str:
    """A new random token (43 URL-safe characters, 256 bits)."""
    return secrets.token_urlsafe(32)


class BearerTokenMiddleware:
    """Pure ASGI middleware: rejects HTTP/WebSocket requests without the right bearer token; lifespan passes through."""

    def __init__(self, app: Any, token: str):
        if not token or len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"the auth token must be at least {MIN_TOKEN_LENGTH} characters")
        self.app = app
        self._token = token.encode("utf-8")

    def _authorized(self, scope: dict) -> bool:
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                scheme, _, credentials = value.partition(b" ")
                if scheme.lower() == b"bearer" and hmac.compare_digest(credentials.strip(), self._token):
                    return True
        return False

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket") and not self._authorized(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = json.dumps({"error": "missing or invalid bearer token"}).encode("utf-8")
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b'Bearer realm="mcp-adapter"'),
                                    (b"content-length", str(len(body)).encode("ascii"))]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
