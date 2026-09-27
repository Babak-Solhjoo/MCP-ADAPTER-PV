"""Interactive post-install setup: choose the network security policy and write it to .env.

    mcp-adapter-setup                      # interactive questions
    mcp-adapter-setup --show               # print the current policy
    mcp-adapter-setup --non-interactive --mode local --allow-internet no
    mcp-adapter-setup --non-interactive --mode network --allowed-hosts 192.168.1.20:8000

The answers are stored as plain keys in .env, so they can be changed later by editing the file
(or by running this command again) and restarting the server.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import ENV_FILE, REPO_ROOT, allow_internet, auth_token, network_mode, tavily_key
from .fileperm import restrict_to_owner
from .http_auth import generate_token

KEYS_DOC = {
    "MCP_ADAPTER_NETWORK_MODE": "local = loopback only (default); network = may listen on other interfaces",
    "MCP_ADAPTER_ALLOW_INTERNET": "true/false - enable tools that call external web APIs (Tavily, Wolfram|Alpha)",
    "MCP_ADAPTER_ALLOWED_HOSTS": "network mode only: comma-separated Host header allow-list, e.g. 192.168.1.20:8000",
    "TAVILY_API": "Tavily API key (only used when MCP_ADAPTER_ALLOW_INTERNET=true)",
}

BANNER = """
MCP-ADAPTER security setup
==========================
This server can run in two ways:
  * stdio (default)  - the MCP client starts it as a child process; NO network port is opened.
  * HTTP/SSE         - it listens on a TCP port. In LOCAL mode it may only bind 127.0.0.1 and rejects
                       requests whose Host header is not localhost, enforced in code (not only by the firewall).
Two decisions are stored in .env and can be changed any time:
  1. network mode  : local (this computer only) or network (other machines may connect)
  2. internet tools: whether tools that call external web APIs (Tavily docs search, Wolfram|Alpha) exist at all
"""


_UNSAFE_CHARS = re.compile("[\x00-\x1f\x7f\x85\u2028\u2029]")


def _env_value(value: object) -> str:
    v = _UNSAFE_CHARS.sub("", str(value))
    if v[:1] in ("'", '"') or " #" in v or v != v.strip():
        v = "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return v


def write_env_updates(env_path: Path, updates: dict[str, str]) -> Path:
    """Insert or replace KEY=value lines in *env_path*, keeping every other line untouched.

    Control characters and every Unicode line separator are removed from values, and values that dotenv would
    parse as quoted or as a comment are quoted, so that a value can never add or hide another setting."""
    updates = {k: _env_value(v) for k, v in updates.items() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)}
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in remaining:
            lines[i] = f"{m.group(1)}={remaining.pop(m.group(1))}"
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# --- mcp-adapter security policy (written by mcp-adapter-setup) ---")
        for key, value in remaining.items():
            lines.append(f"# {KEYS_DOC.get(key, '')}".rstrip())
            lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    restrict_to_owner(env_path)  # .env holds API keys: only the current user may read it
    return env_path


def _ask(prompt: str, default: str) -> str:
    try:
        answer = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def _yes(value: str) -> bool:
    return value.strip().lower() in ("y", "yes", "true", "1", "on")


def current_policy_text() -> str:
    mode = network_mode()
    lines = [
        f"network mode     : {mode} ({'loopback only' if mode == 'local' else 'reachable from other machines'})",
        f"internet tools   : {'enabled' if allow_internet() else 'disabled'}",
        f"Tavily key       : {'configured' if tavily_key() else 'not set'}",
        f".env file        : {ENV_FILE}",
    ]
    return "\n".join(lines)


def run_interactive(env_path: Path) -> dict[str, str]:
    print(BANNER)
    print("Current settings:\n" + current_policy_text() + "\n")
    choice = _ask("Who may connect to this server?  1 = this computer only (recommended)  2 = other machines on the network",
                  "1" if network_mode() == "local" else "2")
    updates: dict[str, str] = {}
    if not auth_token():  # every HTTP transport needs it; stdio ignores it
        updates["MCP_ADAPTER_AUTH_TOKEN"] = generate_token()
    if choice.strip() == "2":
        updates["MCP_ADAPTER_NETWORK_MODE"] = "network"
        hosts = _ask("Host names/IPs (with port) that clients will use, comma-separated (blank = no allow-list)", "")
        updates["MCP_ADAPTER_ALLOWED_HOSTS"] = hosts
    else:
        updates["MCP_ADAPTER_NETWORK_MODE"] = "local"
        updates["MCP_ADAPTER_ALLOWED_HOSTS"] = ""
    internet = _yes(_ask("Enable tools that call external web APIs (Tavily documentation search, Wolfram|Alpha)? y/n",
                         "y" if allow_internet() else "n"))
    updates["MCP_ADAPTER_ALLOW_INTERNET"] = "true" if internet else "false"
    if internet:
        key = _ask("Tavily API key (blank keeps the existing value)", "")
        if key:
            updates["TAVILY_API"] = key
    return updates


def print_next_steps(updates: dict[str, str], env_path: Path) -> None:
    mode = updates.get("MCP_ADAPTER_NETWORK_MODE", network_mode())
    print(f"\nSaved to {env_path}:")
    for k, v in updates.items():
        shown = "********" if k in ("TAVILY_API", "MCP_ADAPTER_AUTH_TOKEN") and v else v
        print(f"  {k}={shown}")
    print("\nWhat this means:")
    if mode == "local":
        print("  * HTTP/SSE transports can only bind 127.0.0.1; any other --host is refused at start-up.")
        print("  * If Windows Firewall ever asks about this program, choose DENY / Cancel: loopback traffic is not")
        print("    filtered by the firewall, so everything keeps working and nothing is reachable from outside.")
    else:
        print("  * The server may listen on other interfaces when started with --host <address>.")
        print("  * Every HTTP request must send 'Authorization: Bearer <MCP_ADAPTER_AUTH_TOKEN>' (the token is in .env);")
        print("    without the token the server refuses to start in network mode.")
        print("  * Allow it through the firewall only on the network profile you trust (usually Private, never Public).")
        if not updates.get("MCP_ADAPTER_ALLOWED_HOSTS"):
            print("  * WARNING: no Host allow-list configured; set MCP_ADAPTER_ALLOWED_HOSTS to restrict clients.")
    print("  * Change your mind later: run `mcp-adapter-setup` again or edit the keys in .env, then restart the server.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mcp-adapter-setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=str(ENV_FILE), help=f"path of the .env to write (default {ENV_FILE})")
    parser.add_argument("--show", action="store_true", help="print the current policy and exit")
    parser.add_argument("--non-interactive", action="store_true", help="take answers from the flags below")
    parser.add_argument("--mode", choices=["local", "network"], default=None)
    parser.add_argument("--allow-internet", choices=["yes", "no"], default=None)
    parser.add_argument("--allowed-hosts", default=None, help="comma-separated Host allow-list for network mode")
    parser.add_argument("--tavily-key", default=None)
    args = parser.parse_args(argv)

    env_path = Path(args.env_file)
    if args.show:
        print(current_policy_text())
        return

    if args.non_interactive:
        updates = {
            "MCP_ADAPTER_NETWORK_MODE": args.mode or "local",
            "MCP_ADAPTER_ALLOW_INTERNET": "true" if args.allow_internet == "yes" else "false",
            "MCP_ADAPTER_ALLOWED_HOSTS": args.allowed_hosts or "",
        }
        if not auth_token():
            updates["MCP_ADAPTER_AUTH_TOKEN"] = generate_token()
        if args.tavily_key:
            updates["TAVILY_API"] = args.tavily_key
    else:
        if not env_path.exists() and (REPO_ROOT / ".env.example").exists():
            env_path.write_text((REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        updates = run_interactive(env_path)

    write_env_updates(env_path, updates)
    print_next_steps(updates, env_path)


if __name__ == "__main__":
    sys.exit(main())
