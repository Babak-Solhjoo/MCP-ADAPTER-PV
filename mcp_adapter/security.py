"""Network security policy for the MCP server.

The policy is chosen once (``mcp-adapter-setup``) and stored in ``.env``:

    MCP_ADAPTER_NETWORK_MODE=local      # local (default) or network
    MCP_ADAPTER_ALLOW_INTERNET=false    # enable tools that call external web APIs (Tavily, Wolfram|Alpha)
    MCP_ADAPTER_ALLOWED_HOSTS=          # network mode only: Host header allow-list, e.g. "192.168.1.20:8000,myhost:*"
    MCP_ADAPTER_AUTH_TOKEN=             # bearer token for HTTP transports: required in network mode

``local`` mode is enforced in code, not only by the OS firewall:
* HTTP/SSE transports may only bind loopback addresses; any other ``--host`` aborts start-up;
* DNS-rebinding protection rejects requests whose Host/Origin header is not localhost;
* stdio (the default transport) never opens a socket at all.

``network`` mode additionally requires a bearer token: the server refuses to start an HTTP transport without
MCP_ADAPTER_AUTH_TOKEN, and every request must send ``Authorization: Bearer <token>``.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any

from .config import allow_internet as _allow_internet
from .config import auth_token as _auth_token
from .config import env, tavily_key
from .config import network_mode as _network_mode

try:  # available in mcp >= 1.10 and 2.x
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:  # pragma: no cover
    TransportSecuritySettings = None  # type: ignore[assignment]

LOCAL = "local"
NETWORK = "network"
DEFAULT_LOOPBACK_HOST = "127.0.0.1"
INTERNET_TOOLS = ("search_docs_online", "mathematica_wolfram_alpha")


class SecurityError(RuntimeError):
    """Raised when a requested network configuration violates the configured policy."""


def is_loopback_host(host: str | None) -> bool:
    """True for 127.0.0.0/8, ::1 and 'localhost' (with or without IPv6 brackets)."""
    if not host:
        return False
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _split_list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass
class SecurityPolicy:
    network_mode: str = LOCAL
    allow_internet: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    auth_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> SecurityPolicy:
        return cls(
            network_mode=_network_mode(),
            allow_internet=_allow_internet(),
            allowed_hosts=_split_list(env("MCP_ADAPTER_ALLOWED_HOSTS")),
            auth_token=_auth_token(),
        )

    @property
    def is_local(self) -> bool:
        return self.network_mode == LOCAL

    # ---- bind address --------------------------------------------------------------------------
    def effective_host(self, requested: str | None) -> str:
        """Return the address the HTTP/SSE transport may bind to, or raise SecurityError."""
        host = (requested or DEFAULT_LOOPBACK_HOST).strip()
        if self.is_local and not is_loopback_host(host):
            raise SecurityError(
                f"MCP_ADAPTER_NETWORK_MODE=local forbids binding to {host!r}. The server may only listen on "
                "127.0.0.1/localhost. To expose it on the network, run `mcp-adapter-setup` and choose network "
                "mode (or set MCP_ADAPTER_NETWORK_MODE=network in .env), then restart."
            )
        return host

    def check_http_start(self) -> None:
        """Refuse an HTTP/SSE transport without a bearer token (also in local mode: other accounts on the same
        computer share 127.0.0.1) or without the SDK's Host/Origin protection."""
        from .http_auth import MIN_TOKEN_LENGTH

        if TransportSecuritySettings is None:
            raise SecurityError("the installed mcp package has no Host/Origin (DNS-rebinding) protection; "
                                "upgrade it (pip install -U 'mcp>=1.23') before using an HTTP transport")
        if not self.auth_token:
            raise SecurityError(
                "HTTP transports require MCP_ADAPTER_AUTH_TOKEN: without it any program or other user on this "
                "computer (and, in network mode, anyone who can reach the port) could run the automation tools. Run "
                "`mcp-adapter-setup` (it generates one) or set a random value of at least 24 characters in .env; "
                "clients then send 'Authorization: Bearer <token>'. stdio needs no token.")
        if self.auth_token and len(self.auth_token) < MIN_TOKEN_LENGTH:
            raise SecurityError(f"MCP_ADAPTER_AUTH_TOKEN is too short (at least {MIN_TOKEN_LENGTH} characters).")

    def transport_security(self, host: str, port: int) -> Any:
        """TransportSecuritySettings enforcing the Host/Origin allow-list for HTTP transports."""
        if TransportSecuritySettings is None:
            return None
        if self.is_local:
            hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
            origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
            return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                             allowed_origins=origins)
        if self.allowed_hosts:
            hosts = list(self.allowed_hosts) + ["127.0.0.1:*", "localhost:*"]
            origins = [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]
            return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                             allowed_origins=origins)
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    # ---- reporting -----------------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "network_mode": self.network_mode,
            "listens_on_network": not self.is_local,
            "bind_restriction": "loopback only (127.0.0.1/localhost)" if self.is_local else
                                ("Host allow-list: " + ", ".join(self.allowed_hosts) if self.allowed_hosts
                                 else "any interface given with --host (no Host allow-list configured)"),
            "dns_rebinding_protection": self.is_local or bool(self.allowed_hosts),
            "http_authentication": ("bearer token required (MCP_ADAPTER_AUTH_TOKEN is set)" if self.auth_token else
                                    "MISSING: HTTP transports refuse to start until MCP_ADAPTER_AUTH_TOKEN is set "
                                    "(stdio needs none)"),
            "stdio_transport_opens_port": False,
            "allow_internet": self.allow_internet,
            "internet_tools": {
                name: ("enabled" if self.allow_internet else "disabled (MCP_ADAPTER_ALLOW_INTERNET=false)")
                for name in INTERNET_TOOLS
            },
            "tavily_key_configured": tavily_key() is not None,
            "how_to_change": "run `mcp-adapter-setup`, or edit MCP_ADAPTER_NETWORK_MODE / MCP_ADAPTER_ALLOW_INTERNET "
                             "in .env and restart the server",
        }

    def startup_banner(self, transport: str, host: str | None, port: int | None) -> str:
        if transport == "stdio":
            return f"[mcp-adapter] transport=stdio (no network port), network_mode={self.network_mode}, " \
                   f"internet_tools={'on' if self.allow_internet else 'off'}"
        scope = "LOOPBACK ONLY" if self.is_local else "NETWORK (reachable from other machines)"
        return (f"[mcp-adapter] transport={transport} listening on {host}:{port} - {scope}; "
                f"internet_tools={'on' if self.allow_internet else 'off'}, "
                f"auth={'bearer token' if self.auth_token else 'none'}. "
                + ("If Windows Firewall asks, choose DENY/Cancel: loopback traffic works without a firewall rule."
                   if self.is_local else "Make sure the firewall rule is intentional.")
                )
