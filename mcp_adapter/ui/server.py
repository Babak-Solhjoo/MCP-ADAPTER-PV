"""Loopback-only workspace UI (``mcp-adapter-ui``): chats, settings, applications, MCP endpoint.

Serves a single-page app on http://127.0.0.1:<port>/ with
* a chat-first workspace: several parallel chats, each with its own working folder (optionally with full
  file/command access inside it), model, approvals and history;
* settings: model providers (keys + connectivity lights), security policy, adapter mode, the local MCP HTTP
  endpoint for other LLM apps;
* application switches and executable paths.

Hardening (the UI can change what the MCP server may run, so it must not be reachable by anyone else):
* binds 127.0.0.1 only, never another interface;
* every /api call must carry the per-session token that is embedded in the served page
  (a custom header, so cross-origin pages cannot send it without a CORS preflight, which is refused);
* the Host header must be localhost/127.0.0.1 and any Origin header must match the page's origin;
* no CORS headers are ever sent; secrets are write-only (never echoed back to the page).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values, load_dotenv

from agent_runner.chat import ChatManager, ChatSettings
from agent_runner.models import STATIC_MODELS, list_models
from agent_runner.openai_provider import DEFAULT_OPENAI_MODEL
from agent_runner.runner import (
    DEFAULT_MAX_TURNS,
    DEFAULT_MODEL,
    PROVIDERS,
    RunConfig,
    provider_key_present,
    provider_sdk_available,
)
from agent_runner.service import AgentService

from .. import __author__, __email__, __github__, __license__, __linkedin__, __project_url__, __version__
from ..adapters.altium import altium_adapter
from ..adapters.autocad import autocad_adapter
from ..adapters.comsol import comsol_adapter
from ..adapters.drawio import drawio_adapter
from ..adapters.eagle import eagle_adapter
from ..adapters.feko import feko_adapter
from ..adapters.hfss import hfss_adapter
from ..adapters.mathematica import mathematica_adapter
from ..adapters.matlab import matlab_adapter
from ..adapters.orcad import capture_adapter, pspice_adapter
from ..adapters.photoshop import photoshop_adapter
from ..adapters.proteus import proteus_adapter
from ..adapters.simulink import simulink_adapter
from ..adapters.vivado import vivado_adapter
from ..catalog import get_catalog
from ..config import ENV_FILE, REPO_ROOT, enable_var, find_executable
from ..http_auth import generate_token
from ..setup_wizard import write_env_updates
from .icons import icon_data_uri
from .mcp_service import DEFAULT_MCP_PORT, McpHttpService, pick_folder

STATIC_DIR = Path(__file__).resolve().parent / "static"
ICONS_DIR = STATIC_DIR / "icons"
_BUNDLED_ICONS: dict[str, str | None] = {}


def bundled_icon_uri(name: str) -> str | None:
    """data: URI of a bundled SVG icon (ui/static/icons/<name>.svg), used when no program icon can be read."""
    if name not in _BUNDLED_ICONS:
        try:
            data = (ICONS_DIR / f"{name}.svg").read_bytes()
            _BUNDLED_ICONS[name] = "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")
        except OSError:
            _BUNDLED_ICONS[name] = None
    return _BUNDLED_ICONS[name]
TOKEN_HEADER = "X-MCP-Adapter-Token"
MASK = "********"
EFFORT_CHOICES = ("", "low", "medium", "high", "xhigh", "max")
PROVIDER_LABEL = {"anthropic": "Anthropic", "openai": "OpenAI"}

VITIS_HLS_PATTERNS = [
    "Xilinx/Vitis_HLS/20*/bin/vitis_hls.bat", "AMD/Vitis_HLS/20*/bin/vitis_hls.bat",
    "/tools/Xilinx/Vitis_HLS/20*/bin/vitis_hls", "/opt/Xilinx/Vitis_HLS/20*/bin/vitis_hls",
]

# One card per application; each card has one or more executable paths.
APP_SPECS: list[dict[str, Any]] = [
    {"id": "matlab", "color": "#E16737", "name": "MATLAB", "vendor": "MathWorks", "icon": "M",
     "paths": [{"env": "MATLAB_EXE", "label": "MATLAB launcher (matlab.exe / matlab)", "adapter": matlab_adapter}]},
    {"id": "simulink", "color": "#0076A8", "name": "Simulink", "vendor": "MathWorks", "icon": "S",
     "note": "Runs through MATLAB; uses the MATLAB launcher path.",
     "paths": [{"env": "MATLAB_EXE", "label": "MATLAB launcher (shared with MATLAB)", "adapter": simulink_adapter,
                "shared": True}]},
    {"id": "mathematica", "color": "#DD1100", "name": "Wolfram Mathematica", "vendor": "Wolfram Research", "icon": "W",
     "paths": [{"env": "WOLFRAMSCRIPT_EXE", "label": "wolframscript", "adapter": mathematica_adapter}]},
    {"id": "comsol", "color": "#1A56C4", "name": "COMSOL Multiphysics", "vendor": "COMSOL AB", "icon": "C",
     "paths": [{"env": "COMSOL_EXE", "label": "comsolbatch / comsol launcher", "adapter": comsol_adapter}]},
    {"id": "photoshop", "color": "#1473E6", "name": "Adobe Photoshop", "vendor": "Adobe", "icon": "Ps",
     "paths": [{"env": "PHOTOSHOP_EXE", "label": "Photoshop.exe", "adapter": photoshop_adapter}]},
    {"id": "orcad", "color": "#C8102E", "name": "Cadence OrCAD", "vendor": "Cadence", "icon": "Or",
     "paths": [{"env": "PSPICE_EXE", "label": "PSpice (pspice.exe)", "adapter": pspice_adapter},
               {"env": "ORCAD_CAPTURE_EXE", "label": "Capture (capture.exe)", "adapter": capture_adapter}]},
    {"id": "altium", "color": "#3B4A5A", "name": "Altium Designer", "vendor": "Altium", "icon": "Ad",
     "paths": [{"env": "ALTIUM_EXE", "label": "X2.EXE", "adapter": altium_adapter}]},
    {"id": "eagle", "color": "#E8641B", "name": "Autodesk EAGLE", "vendor": "Autodesk", "icon": "Ea",
     "note": ("Retired by Autodesk on 7 June 2026: design reading, BOM, netlist and command-line CAM output work; "
              "the editors need a sign-in."),
     "paths": [{"env": "EAGLE_EXE", "label": "eaglecon.exe", "adapter": eagle_adapter}]},
    {"id": "proteus", "color": "#1F77B4", "name": "Proteus Design Suite", "vendor": "Labcenter", "icon": "Pr",
     "paths": [{"env": "PROTEUS_EXE", "label": "PDS.EXE", "adapter": proteus_adapter}]},
    {"id": "vivado", "color": "#ED1C24", "name": "AMD Vivado", "vendor": "AMD (Xilinx)", "icon": "V",
     "paths": [{"env": "VIVADO_EXE", "label": "vivado.bat / vivado", "adapter": vivado_adapter},
               {"env": "VITIS_HLS_EXE", "label": "vitis_hls (optional)", "adapter": None,
                "names": ["vitis_hls", "vitis_hls.bat"], "patterns": VITIS_HLS_PATTERNS}]},
    {"id": "autocad", "color": "#B5121B", "name": "Autodesk AutoCAD", "vendor": "Autodesk", "icon": "Ac",
     "paths": [{"env": "AUTOCAD_CORE_CONSOLE_EXE", "label": "accoreconsole.exe", "adapter": autocad_adapter}]},
    {"id": "hfss", "color": "#FFB71B", "name": "Ansys HFSS", "vendor": "Ansys", "icon": "H",
     "paths": [{"env": "ANSYS_EDT_EXE", "label": "ansysedt.exe", "adapter": hfss_adapter}]},
    {"id": "feko", "color": "#0B6FBF", "name": "Altair Feko", "vendor": "Altair", "icon": "Fk",
     "paths": [{"env": "FEKO_EXE", "label": "runfeko.exe", "adapter": feko_adapter}]},
    {"id": "drawio", "color": "#F08705", "name": "draw.io", "vendor": "JGraph", "icon": "D",
     "note": "Diagram generation works without the executable; it is only needed for PNG/SVG/PDF export.",
     "paths": [{"env": "DRAWIO_EXE", "label": "draw.io desktop executable", "adapter": drawio_adapter}]},
]

SETTINGS_KEYS = {
    "MCP_ADAPTER_NETWORK_MODE": "local",
    "MCP_ADAPTER_ALLOWED_HOSTS": "",
    "MCP_ADAPTER_ALLOW_INTERNET": "false",
    "MCP_ADAPTER_EXPOSE_UNAVAILABLE": "false",
    "MCP_ADAPTER_TIMEOUT": "600",
    "MCP_ADAPTER_OUTPUT_DIR": "",
    # chat defaults (each chat keeps its own copy; these seed new chats)
    "AGENT_PROVIDER": "anthropic",
    "AGENT_MODEL": DEFAULT_MODEL,
    "AGENT_OPENAI_MODEL": DEFAULT_OPENAI_MODEL,
    "OPENAI_BASE_URL": "",
    "AGENT_EFFORT": "",
    "AGENT_MAX_TURNS": str(DEFAULT_MAX_TURNS),
    "AGENT_APPROVE_TOOLS": "false",
    "AGENT_SHOW_THINKING": "false",
    "MCP_ADAPTER_WORK_DIR": "",
    "AGENT_WORK_DIR_ACCESS": "false",
    # MCP endpoint for other apps
    "MCP_HTTP_PORT": str(DEFAULT_MCP_PORT),
    "MCP_HTTP_AUTOSTART": "false",
    "MCP_ADAPTER_WORK_DIR_ACCESS": "false",
}
SECRET_KEYS = ("TAVILY_API", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
BOOL_KEYS = ("MCP_ADAPTER_ALLOW_INTERNET", "MCP_ADAPTER_EXPOSE_UNAVAILABLE", "AGENT_APPROVE_TOOLS",
             "AGENT_SHOW_THINKING", "AGENT_WORK_DIR_ACCESS", "MCP_HTTP_AUTOSTART", "MCP_ADAPTER_WORK_DIR_ACCESS")
PATH_KEYS = ("MCP_ADAPTER_OUTPUT_DIR", "MCP_ADAPTER_WORK_DIR")


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _bool_str(value: Any) -> str:
    return "true" if _truthy(str(value), False) else "false"


def _auto_detect(spec: dict[str, Any]) -> str | None:
    """Detect the executable ignoring any .env override (so the UI can show both)."""
    adapter = spec.get("adapter")
    if adapter is not None:
        return find_executable("MCP_ADAPTER_UI_NO_OVERRIDE", adapter.exe_names, adapter.exe_patterns)
    return find_executable("MCP_ADAPTER_UI_NO_OVERRIDE", spec.get("names", []), spec.get("patterns", []))


class ConfigStore:
    """Reads and writes the .env file that both the MCP server and the UI use."""

    def __init__(self, env_path: Path | None = None):
        self.env_path = Path(env_path) if env_path else ENV_FILE

    def values(self) -> dict[str, str]:
        if not self.env_path.exists():
            return {}
        return {k: (v or "") for k, v in dotenv_values(self.env_path, interpolate=False).items()}

    def reload_env(self) -> None:
        """Push the current .env into this process' environment (so a freshly saved API key is used)."""
        if self.env_path.exists():
            load_dotenv(self.env_path, override=True, interpolate=False)

    # ---- state -----------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        vals = self.values()
        try:
            counts = {s["id"]: s["tools"] for s in get_catalog().stats()}
        except Exception:  # catalogs are optional for the UI
            counts = {}
        apps = []
        for spec in APP_SPECS:
            paths = []
            for p in spec["paths"]:
                configured = vals.get(p["env"], "") or ""
                detected = _auto_detect(p)
                effective = configured or detected or ""
                paths.append({
                    "env": p["env"], "label": p["label"], "shared": bool(p.get("shared")),
                    "configured": configured, "detected": detected or "", "effective": effective,
                    "exists": bool(effective) and Path(effective).exists(),
                })
            program_icon = next((icon_data_uri(x["effective"]) for x in paths if x["exists"]), None)
            apps.append({
                "id": spec["id"], "name": spec["name"], "vendor": spec["vendor"], "icon": spec["icon"],
                "note": spec.get("note", ""), "catalog_entries": counts.get(spec["id"], 0),
                "enabled": _truthy(vals.get(enable_var(spec["id"])), True),
                "available": any(x["exists"] for x in paths),
                "paths": paths,
                "color": spec.get("color", "#7c8cff"),
                "icon_uri": program_icon or bundled_icon_uri(spec["id"]),
                "icon_source": "program" if program_icon else ("bundled" if bundled_icon_uri(spec["id"]) else "letter"),
            })
        settings = {k: (vals.get(k) or d) for k, d in SETTINGS_KEYS.items()}
        for key in SECRET_KEYS:
            settings[f"{key}_SET"] = bool(vals.get(key) or os.environ.get(key))
        return {"env_file": str(self.env_path), "env_exists": self.env_path.exists(), "apps": apps,
                "settings": settings, "providers": list(PROVIDERS),
                "model_choices": {p: list(STATIC_MODELS[p]) for p in PROVIDERS},
                "effort_choices": list(EFFORT_CHOICES),
                "sdk_available": {p: provider_sdk_available(p) for p in PROVIDERS},
                "python": sys.executable, "repo_root": str(REPO_ROOT), "restart_required": True,
                "about": {"name": "MCP Adapter", "version": __version__, "author": __author__, "email": __email__,
                          "github": __github__, "linkedin": __linkedin__, "project_url": __project_url__, "license": __license__,
                          "applications": len(APP_SPECS), "catalog_entries": sum(counts.values())}}

    # ---- save ------------------------------------------------------------------------------------
    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write the given apps/settings to .env. Only keys present in the payload are touched."""
        updates: dict[str, str] = {}
        known_envs = {x["env"] for s in APP_SPECS for x in s["paths"]}
        for app in payload.get("apps", []) or []:
            app_id = str(app.get("id", ""))
            if app_id not in {s["id"] for s in APP_SPECS}:
                continue
            if "enabled" in app:
                updates[enable_var(app_id)] = "true" if app.get("enabled", True) else "false"
            for p in app.get("paths", []) or []:
                env_name = str(p.get("env", ""))
                if env_name in known_envs:
                    updates[env_name] = str(p.get("configured", "")).strip()
        s = payload.get("settings", {}) or {}
        if "MCP_ADAPTER_NETWORK_MODE" in s:
            mode = str(s["MCP_ADAPTER_NETWORK_MODE"]).lower()
            updates["MCP_ADAPTER_NETWORK_MODE"] = mode if mode in ("local", "network") else "local"
            if updates["MCP_ADAPTER_NETWORK_MODE"] == "network" and not self.values().get("MCP_ADAPTER_AUTH_TOKEN"):
                updates["MCP_ADAPTER_AUTH_TOKEN"] = generate_token()  # required in network mode; stays in .env
        if "MCP_ADAPTER_ALLOWED_HOSTS" in s:
            updates["MCP_ADAPTER_ALLOWED_HOSTS"] = str(s["MCP_ADAPTER_ALLOWED_HOSTS"]).strip()
        for key in BOOL_KEYS:
            if key in s:
                updates[key] = _bool_str(s[key])
        for key, default in (("MCP_ADAPTER_TIMEOUT", "600"), ("AGENT_MAX_TURNS", str(DEFAULT_MAX_TURNS)),
                             ("MCP_HTTP_PORT", str(DEFAULT_MCP_PORT))):
            if key in s:
                val = str(s[key]).strip()
                updates[key] = val if val.isdigit() and int(val) > 0 else default
        if "AGENT_MODEL" in s:
            updates["AGENT_MODEL"] = str(s["AGENT_MODEL"]).strip() or DEFAULT_MODEL
        if "AGENT_OPENAI_MODEL" in s:
            updates["AGENT_OPENAI_MODEL"] = str(s["AGENT_OPENAI_MODEL"]).strip() or DEFAULT_OPENAI_MODEL
        if "AGENT_PROVIDER" in s:
            prov = str(s["AGENT_PROVIDER"]).strip().lower()
            updates["AGENT_PROVIDER"] = prov if prov in PROVIDERS else "anthropic"
        if "OPENAI_BASE_URL" in s:
            updates["OPENAI_BASE_URL"] = str(s["OPENAI_BASE_URL"]).strip()
        if "AGENT_EFFORT" in s:
            effort = str(s["AGENT_EFFORT"]).strip().lower()
            updates["AGENT_EFFORT"] = effort if effort in EFFORT_CHOICES else ""
        for key in PATH_KEYS:
            if key in s:
                updates[key] = str(s[key]).strip().strip('"')
        for key in SECRET_KEYS:  # write-only: only replaced when a real new value is typed
            val = str(s.get(key, "")).strip()
            if val and val != MASK:
                updates[key] = val
        if not self.env_path.exists() and (REPO_ROOT / ".env.example").exists():
            self.env_path.write_text((REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        if updates:
            write_env_updates(self.env_path, updates)
        return self.state()

    # ---- providers / models ----------------------------------------------------------------------
    def providers_status(self, refresh: bool = False) -> dict[str, Any]:
        """Per provider: SDK installed, key stored, connectivity (models endpoint reachable) and the model list."""
        self.reload_env()
        vals = self.values()
        out = {}
        for prov in PROVIDERS:
            key_set = provider_key_present(prov)
            sdk = provider_sdk_available(prov)
            info: dict[str, Any] = {"label": PROVIDER_LABEL[prov], "sdk": sdk, "key_set": key_set,
                                    "connected": False, "models": list(STATIC_MODELS[prov]), "source": "static", "error": None}
            if key_set and sdk:
                base = (vals.get("OPENAI_BASE_URL") or None) if prov == "openai" else None
                res = list_models(prov, base, refresh)
                info.update(models=res["models"], source=res["source"], connected=res["source"] == "live",
                            error=res.get("error"))
            elif not sdk:
                info["error"] = 'SDK not installed: python -m pip install -e ".[agent]"'
            else:
                info["error"] = "no API key stored"
            info["light"] = "green" if info["connected"] else ("orange" if key_set else "red")
            out[prov] = info
        return out

    def all_models(self, refresh: bool = False) -> list[dict[str, str]]:
        """Models from every provider that has a key (live list when reachable, else built-in)."""
        rows = []
        for prov, info in self.providers_status(refresh).items():
            if not info["key_set"]:
                continue
            for m in info["models"]:
                rows.append({"provider": prov, "model": m, "label": f"{m}  ({info['label']})",
                             "source": info["source"]})
        return rows

    # ---- chats -----------------------------------------------------------------------------------
    def chat_defaults(self) -> ChatSettings:
        vals = self.values()
        prov = (vals.get("AGENT_PROVIDER") or "anthropic").strip().lower()
        prov = prov if prov in PROVIDERS else "anthropic"
        model = (vals.get("AGENT_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL) if prov == "openai" else (vals.get("AGENT_MODEL") or DEFAULT_MODEL)
        return ChatSettings.from_dict({
            "provider": prov, "model": model, "base_url": vals.get("OPENAI_BASE_URL", ""),
            "effort": vals.get("AGENT_EFFORT", ""), "max_turns": vals.get("AGENT_MAX_TURNS") or DEFAULT_MAX_TURNS,
            "approve": vals.get("AGENT_APPROVE_TOOLS", "false"), "show_thinking": vals.get("AGENT_SHOW_THINKING", "false"),
            "work_dir": vals.get("AGENT_WORK_DIR", ""), "work_dir_access": vals.get("AGENT_WORK_DIR_ACCESS", "false"),
        })

    def remember_chat_defaults(self, settings: ChatSettings) -> None:
        """New chats start with the settings last used in any chat."""
        updates = {
            "AGENT_PROVIDER": settings.provider,
            ("AGENT_OPENAI_MODEL" if settings.provider == "openai" else "AGENT_MODEL"): settings.model,
            "AGENT_EFFORT": settings.effort, "AGENT_MAX_TURNS": str(settings.max_turns),
            "AGENT_APPROVE_TOOLS": _bool_str(settings.approve), "AGENT_SHOW_THINKING": _bool_str(settings.show_thinking),
            "AGENT_WORK_DIR": settings.work_dir, "AGENT_WORK_DIR_ACCESS": _bool_str(settings.work_dir_access),
        }
        if settings.base_url:
            updates["OPENAI_BASE_URL"] = settings.base_url
        if not self.env_path.exists() and (REPO_ROOT / ".env.example").exists():
            self.env_path.write_text((REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        write_env_updates(self.env_path, updates)

    def chat_store_dir(self) -> Path:
        raw = (self.values().get("MCP_ADAPTER_CHATS_DIR") or os.environ.get("MCP_ADAPTER_CHATS_DIR") or "").strip()
        return Path(raw) if raw else self.env_path.parent / "outputs" / "chats"

    # ---- MCP endpoint ----------------------------------------------------------------------------
    def mcp_env(self) -> dict[str, str]:
        """Environment for the HTTP MCP endpoint used by external apps (default folder + access switch)."""
        vals = self.values()
        folder = (vals.get("MCP_ADAPTER_WORK_DIR") or "").strip()
        token = (vals.get("MCP_ADAPTER_AUTH_TOKEN") or "").strip()
        return {**({"MCP_ADAPTER_AUTH_TOKEN": token} if token else {}),  # the child server enforces it
                "MCP_ADAPTER_WORK_DIR": folder,
                "MCP_ADAPTER_WORK_DIR_ACCESS": _bool_str(bool(folder) and _truthy(vals.get("MCP_ADAPTER_WORK_DIR_ACCESS"), False))}

    def ensure_auth_token(self) -> str:
        """The endpoint's bearer token, generated and stored in .env on first use (never shown in the page)."""
        token = (self.values().get("MCP_ADAPTER_AUTH_TOKEN") or "").strip()
        if len(token) < 24:
            token = generate_token()
            if not self.env_path.exists() and (REPO_ROOT / ".env.example").exists():
                self.env_path.write_text((REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
            write_env_updates(self.env_path, {"MCP_ADAPTER_AUTH_TOKEN": token})
        return token

    def mcp_port(self) -> int:
        raw = (self.values().get("MCP_HTTP_PORT") or "").strip()
        return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MCP_PORT

    # ---- legacy CLI defaults ---------------------------------------------------------------------
    def provider(self) -> str:
        return self.chat_defaults().provider

    def agent_config(self, task: str) -> RunConfig:
        s = self.chat_defaults()
        return RunConfig(task=task, provider=s.provider, model=s.model, base_url=s.base_url or None,
                         effort=s.effort or None, max_turns=s.max_turns, approve=s.approve,
                         show_thinking=s.show_thinking, work_dir=Path(s.work_dir) if s.work_dir else None,
                         work_dir_access=s.work_dir_access)

    @staticmethod
    def check_path(path: str) -> dict[str, Any]:
        p = Path(path.strip().strip('"')) if path else None
        return {"path": str(p) if p else "", "exists": bool(p) and p.exists(), "is_file": bool(p) and p.is_file(),
                "is_dir": bool(p) and p.is_dir()}


def _chat_report_dir(chat) -> Path:
    wd = chat.settings.work_dir
    return (Path(wd).expanduser() / "agent-reports") if wd else (REPO_ROOT / "outputs" / "agent")


def make_handler(store: ConfigStore, token: str, port: int, chats: ChatManager, mcp: McpHttpService):
    def _page(nonce: str) -> str:  # read on every request so page updates need no restart
        # The session token is NOT embedded: any local process could fetch this page. The browser gets the token
        # only through the launch link's fragment (#t=...), which is never sent to a server.
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__NONCE__", nonce)

    token_bytes = token.encode("ascii")

    loopback_names = {"127.0.0.1", "localhost", "::1"}

    def _hostname(value: str) -> str:
        """Host header or origin host without the port ('[::1]:8765' -> '::1')."""
        v = value.strip().lower()
        if v.startswith("["):
            return v[1:].split("]")[0]
        return v.split(":")[0]

    class Handler(BaseHTTPRequestHandler):
        server_version = "mcp-adapter-ui"
        timeout = 30  # idle connections are dropped instead of holding a thread forever

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            super().end_headers()

        def log_message(self, fmt, *args):  # quieter console: hide the polling of the chat panel
            line = fmt % args if args else fmt
            if "/events" not in line and "/api/chats HTTP" not in line and "/api/chats/" not in line:
                sys.stderr.write("[ui] " + line + "\n")

        def _reject(self, code: int, message: str) -> None:
            body = json.dumps({"ok": False, "error": message}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _guard(self, need_token: bool) -> bool:
            if _hostname(self.headers.get("Host") or "") not in loopback_names:
                self._reject(403, "Host header is not localhost")
                return False
            origin = self.headers.get("Origin")
            if origin:
                own_port = self.server.server_address[1]
                allowed = {f"http://127.0.0.1:{own_port}", f"http://localhost:{own_port}", f"http://[::1]:{own_port}"}
                if origin.strip().lower().rstrip("/") not in allowed:
                    self._reject(403, "cross-origin request refused")
                    return False
            if need_token:
                sent = (self.headers.get(TOKEN_HEADER) or "").encode("latin-1", "replace")
                if not secrets.compare_digest(sent, token_bytes):
                    self._reject(403, "missing or invalid session token")
                    return False
            return True

        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # CORS preflight from other origins is always refused
            self._reject(403, "cross-origin requests are not allowed")

        def do_HEAD(self):  # health probes from launchers/browsers
            if urlparse(self.path).path in ("/", "/index.html") and self._guard(need_token=False):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()

        # ---- GET -------------------------------------------------------------------------------
        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/favicon.ico", "/favicon.svg"):
                icon = ICONS_DIR / "mcp-adapter.svg"
                body = icon.read_bytes() if icon.exists() else b""
                self.send_response(200 if body else 204)
                if body:
                    self.send_header("Content-Type", "image/svg+xml")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if body:
                    self.wfile.write(body)
                return
            if path in ("/", "/index.html"):
                if not self._guard(need_token=False):
                    return
                nonce = secrets.token_urlsafe(16)
                body = _page(nonce).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy",
                                 f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                                 "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; "
                                 "form-action 'none'")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not self._guard(need_token=True):
                return
            q = parse_qs(parsed.query)
            refresh = q.get("refresh", ["0"])[0] in ("1", "true")
            parts = [p for p in path.split("/") if p]
            try:
                if path == "/api/state":
                    self._json(store.state())
                elif path == "/api/providers/status":
                    self._json({"ok": True, "providers": store.providers_status(refresh)})
                elif path == "/api/models/all":
                    self._json({"ok": True, "models": store.all_models(refresh)})
                elif path == "/api/chats":
                    self._json({"ok": True, "chats": chats.list(), "defaults": store.chat_defaults().to_dict()})
                elif len(parts) == 3 and parts[:2] == ["api", "chats"]:
                    self._json({"ok": True, "chat": chats.get(parts[2]).to_dict()})
                elif len(parts) == 4 and parts[:2] == ["api", "chats"] and parts[3] == "events":
                    try:
                        since = int(q.get("since", ["0"])[0])
                    except ValueError:
                        since = 0
                    chat = chats.get(parts[2])
                    self._json({"ok": True, "events": chats.events(chat.id, since), "status": chat.summary()["status"],
                                "running": chat.service.running, "pending": chat.service.pending,
                                "messages": len(chat.messages)})
                elif len(parts) == 4 and parts[:2] == ["api", "chats"] and parts[3] == "report":
                    chat = chats.get(parts[2])
                    name = q.get("name", [""])[0]
                    text = AgentService.read_report(_chat_report_dir(chat), name)
                    if text is None:
                        self._reject(404, "report not found")
                    else:
                        self._json({"ok": True, "name": name, "text": text})
                elif path == "/api/mcp/status":
                    self._json({"ok": True, **mcp.status(), "configured_port": store.mcp_port(),
                                "autostart": _truthy(store.values().get("MCP_HTTP_AUTOSTART"), False)})
                elif path == "/api/mcp/config":
                    self._json({"ok": True, **mcp.config_snippets(store.mcp_port())})
                else:
                    self._reject(404, "not found")
            except KeyError as exc:
                self._reject(404, str(exc))

        # ---- POST ------------------------------------------------------------------------------
        def do_POST(self):
            path = urlparse(self.path).path
            if not self._guard(need_token=True):
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._reject(400, "invalid Content-Length")
                return
            if length < 0 or length > 2_000_000:
                self._reject(413, "payload too large")
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._reject(400, "invalid JSON")
                return
            parts = [p for p in path.split("/") if p]
            try:
                if path == "/api/save":
                    self._json({"ok": True, "state": store.save(payload)})
                elif path == "/api/check-path":
                    self._json({"ok": True, **ConfigStore.check_path(str(payload.get("path", "")))})
                elif path == "/api/detect":
                    self._json({"ok": True, "state": store.state()})
                elif path == "/api/pick-folder":
                    self._json(pick_folder(str(payload.get("initial", ""))))
                elif path == "/api/chats":
                    chat = chats.create(payload.get("settings"), payload.get("title"))
                    self._json({"ok": True, "chat": chat.to_dict()})
                elif len(parts) == 4 and parts[:2] == ["api", "chats"]:
                    self._chat_action(parts[2], parts[3], payload)
                elif path == "/api/mcp/start":
                    port = payload.get("port") or store.mcp_port()
                    if str(port).isdigit() and int(port) > 0:
                        store.save({"settings": {"MCP_HTTP_PORT": str(port)}})
                    store.ensure_auth_token()
                    self._json({"ok": True, **mcp.start(int(port), store.mcp_env())})
                elif path == "/api/mcp/stop":
                    self._json({"ok": True, **mcp.stop()})
                else:
                    self._reject(404, "not found")
            except KeyError as exc:
                self._reject(404, str(exc))
            except (ValueError, RuntimeError) as exc:
                self._reject(409 if isinstance(exc, RuntimeError) else 400, str(exc))
            except OSError as exc:
                self._reject(500, f"file error: {exc}")

        def _chat_action(self, chat_id: str, action: str, payload: dict[str, Any]) -> None:
            if action == "send":
                chat = chats.get(chat_id)
                if payload.get("settings"):
                    chats.update_settings(chat_id, payload["settings"])
                prov = chat.settings.provider
                if not provider_sdk_available(prov):
                    self._reject(400, f'the {prov} SDK is not installed: run  python -m pip install -e ".[agent]"')
                    return
                store.reload_env()
                if not provider_key_present(prov):
                    self._reject(400, f"no {PROVIDER_LABEL[prov]} API key: add it in Settings > Model providers")
                    return
                chats.send(chat_id, str(payload.get("text", "")))
                store.remember_chat_defaults(chat.settings)
                self._json({"ok": True, "chat": chat.to_dict()})
            elif action == "settings":
                chat = chats.update_settings(chat_id, payload.get("settings") or payload)
                store.remember_chat_defaults(chat.settings)
                self._json({"ok": True, "chat": chat.to_dict()})
            elif action == "stop":
                self._json({"ok": True, "chat": chats.stop(chat_id).to_dict()})
            elif action == "decide":
                self._json({"ok": True, "chat": chats.decide(chat_id, bool(payload.get("approve")),
                                                              str(payload.get("id") or "")).to_dict()})
            elif action == "rename":
                self._json({"ok": True, "chat": chats.rename(chat_id, str(payload.get("title", ""))).to_dict()})
            elif action == "delete":
                chats.delete(chat_id)
                self._json({"ok": True, "chats": chats.list()})
            else:
                self._reject(404, "unknown chat action")

    return Handler


class _ExclusiveServer(ThreadingHTTPServer):
    """HTTP server that refuses to share its port with another instance.

    The stdlib default sets SO_REUSEADDR, which on Windows lets several processes listen on the same port at
    once; a stale instance would then keep answering with an old page. On Windows we bind exclusively instead.
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(port: int = 8765, open_browser: bool = True, env_path: Path | None = None,
          block: bool = True, chats: ChatManager | None = None, mcp: McpHttpService | None = None) -> ThreadingHTTPServer:
    store = ConfigStore(env_path)
    if store.env_path.exists():
        from ..fileperm import restrict_to_owner

        restrict_to_owner(store.env_path)  # tighten an .env created before this version
    chats = chats or ChatManager(store.chat_store_dir(), store.chat_defaults())
    mcp = mcp or McpHttpService()
    token = secrets.token_urlsafe(24)
    try:
        httpd = _ExclusiveServer(("127.0.0.1", port), make_handler(store, token, port, chats, mcp))
    except OSError as exc:
        print(f"[mcp-adapter-ui] cannot listen on 127.0.0.1:{port}: {exc}. Another instance of the UI is probably "
              "still running - stop it (Ctrl+C in its terminal) or start this one with --port <other>.",
              file=sys.stderr)
        raise
    httpd.session_token = token  # type: ignore[attr-defined]  # for in-process callers (tests); never served
    actual_port = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/#t={token}"
    print(f"[mcp-adapter-ui] workspace UI (loopback only; editing {store.env_path}; pid {os.getpid()})",
          file=sys.stderr)
    print(f"[mcp-adapter-ui] open this private link (it carries this session's key; do not share it): {url}",
          file=sys.stderr)
    print("[mcp-adapter-ui] press Ctrl+C to stop", file=sys.stderr)
    if _truthy(store.values().get("MCP_HTTP_AUTOSTART"), False):
        st = mcp.start(store.mcp_port(), store.mcp_env())
        print(f"[mcp-adapter-ui] MCP HTTP endpoint: {'running at ' + st['url'] if st['running'] else st.get('error')}",
              file=sys.stderr)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            mcp.stop()
    return httpd


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mcp-adapter-ui", description="Local workspace UI for mcp-adapter.")
    parser.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765; 0 = random)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    parser.add_argument("--env-file", default=None, help="path of the .env to edit (default: repository .env)")
    args = parser.parse_args(argv)
    serve(args.port, not args.no_browser, Path(args.env_file) if args.env_file else None)


if __name__ == "__main__":
    main()
