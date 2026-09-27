"""Run the MCP server as a local HTTP endpoint for other LLM apps, plus config snippets and a folder picker.

The endpoint is the same ``mcp_adapter.server`` started with ``--transport streamable-http``; the server's
own security policy applies (loopback only in local mode), so it is reachable by apps on this computer such as
Claude Desktop, Claude Code, Cursor or VS Code, but not from the network.
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MCP_PORT = 8766


class McpHttpService:
    def __init__(self, python: str | None = None, repo_root: Path | None = None):
        self.python = python or sys.executable
        self.repo_root = repo_root or REPO_ROOT
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.started_at: float | None = None
        self.log: deque[str] = deque(maxlen=300)
        self._lock = threading.Lock()
        atexit.register(self.stop)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def url(self, port: int | None = None) -> str:
        return f"http://127.0.0.1:{port or self.port or DEFAULT_MCP_PORT}/mcp"

    def start(self, port: int = DEFAULT_MCP_PORT, extra_env: dict[str, str] | None = None,
              command: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {**self.status(), "note": "already running"}
            cmd = command or [self.python, "-m", "mcp_adapter.server", "--transport", "streamable-http",
                              "--port", str(port)]
            from ..config import child_env

            env = child_env({"MCP_ADAPTER_WORK_DIR": "", "MCP_ADAPTER_WORK_DIR_ACCESS": "false", **(extra_env or {})})
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
            self.log.clear()
            try:
                self.proc = subprocess.Popen(cmd, cwd=str(self.repo_root), env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                             creationflags=flags)
            except OSError as exc:
                return {**self.status(), "error": f"could not start the MCP server: {exc}"}
            self.port = port
            self.started_at = time.time()
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        deadline = time.time() + 2.5
        while time.time() < deadline and self.running:
            time.sleep(0.1)
        if not self.running:
            return {**self.status(), "error": "the MCP server exited right after start; see the log"}
        return self.status()

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log.append(line.rstrip())

    def stop(self) -> dict[str, Any]:
        with self._lock:
            proc = self.proc
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self.proc = None
            self.started_at = None
        return self.status()

    def status(self) -> dict[str, Any]:
        running = self.running
        return {
            "running": running,
            "pid": self.proc.pid if running and self.proc else None,
            "port": self.port or DEFAULT_MCP_PORT,
            "url": self.url(),
            "uptime_s": round(time.time() - self.started_at, 1) if running and self.started_at else 0,
            "exit_code": (self.proc.poll() if self.proc else None),
            "log_tail": list(self.log)[-40:],
        }

    def config_snippets(self, port: int | None = None) -> dict[str, Any]:
        """Ready-to-paste configuration for common MCP clients."""
        url = self.url(port)
        python = self.python
        stdio = {"mcpServers": {"mcp-adapter": {"command": python, "args": ["-m", "mcp_adapter.server"],
                                                "cwd": str(self.repo_root)}}}
        from ..config import auth_token

        http_server: dict[str, Any] = {"type": "http", "url": url}
        header_flag = ""
        if auth_token():  # the token itself is never sent to the page: clients copy it from .env
            http_server["headers"] = {"Authorization": "Bearer <MCP_ADAPTER_AUTH_TOKEN from .env>"}
            header_flag = ' --header "Authorization: Bearer <MCP_ADAPTER_AUTH_TOKEN from .env>"'
        http_cfg = {"mcpServers": {"mcp-adapter": http_server}}
        return {
            "http_url": url,
            "auth_token_required": bool(auth_token()),
            "claude_desktop_stdio_json": json.dumps(stdio, indent=2),
            "claude_code_stdio_cmd": f'claude mcp add mcp-adapter -- "{python}" -m mcp_adapter.server',
            "claude_code_http_cmd": f"claude mcp add --transport http mcp-adapter {url}{header_flag}",
            "generic_http_json": json.dumps(http_cfg, indent=2),
            "notes": [
                "stdio: the client starts the server itself as a child process (no port, nothing to keep running).",
                "http: start the endpoint here (or with `mcp-adapter --transport streamable-http --port <port>`) "
                "and give the URL to a client that supports remote/HTTP MCP servers on localhost, e.g. Claude Code, "
                "Cursor, VS Code Copilot, Windsurf.",
                "ChatGPT's MCP connectors require a server reachable on the public internet over HTTPS; a "
                "localhost endpoint is not visible to them unless you publish it through a tunnel, which is outside "
                "the local-only policy of this tool.",
                "The security policy (local mode, loopback only) applies to the HTTP endpoint as well.",
                "When MCP_ADAPTER_AUTH_TOKEN is set (always in network mode), HTTP clients must send "
                "'Authorization: Bearer <token>'.",
            ],
        }


PICKER_CODE = r"""
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:
    print("__NO_TK__:" + str(exc)); sys.exit(0)
root = tk.Tk(); root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
initial = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
path = filedialog.askdirectory(title="Choose the working folder for this chat", initialdir=initial, mustexist=False)
print(path or "")
"""


_PICKER_LOCK = threading.Lock()


def pick_folder(initial: str = "", timeout: int = 300) -> dict[str, Any]:
    """Open the native folder dialog (Tk) in a helper process and return the chosen path ("" if cancelled).
    Only one dialog at a time; a second request is refused instead of stacking dialogs and threads."""
    if not _PICKER_LOCK.acquire(blocking=False):
        return {"ok": False, "path": "", "error": "a folder dialog is already open"}
    try:
        return _pick_folder(initial, timeout)
    finally:
        _PICKER_LOCK.release()


def _pick_folder(initial: str, timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run([sys.executable, "-c", PICKER_CODE, initial or ""], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": "", "error": "the folder dialog timed out"}
    except OSError as exc:
        return {"ok": False, "path": "", "error": str(exc)}
    out = (proc.stdout or "").strip()
    if out.startswith("__NO_TK__:"):
        return {"ok": False, "path": "", "error": "tkinter is not available in this Python; type the folder path instead"}
    return {"ok": True, "path": out.replace("/", os.sep) if os.name == "nt" else out, "cancelled": out == ""}
