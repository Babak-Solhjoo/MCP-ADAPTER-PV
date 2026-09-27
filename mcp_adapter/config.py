"""Configuration helpers: environment variables, executable discovery, timeouts, security switches."""
from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a declared dependency
    def load_dotenv(*_a, **_k):  # type: ignore[misc]
        return False

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CATALOG_DIR = PACKAGE_DIR / "catalogs"
ENV_FILE = REPO_ROOT / ".env"

# Load .env from the repository root first, then from the current working directory.
load_dotenv(ENV_FILE)
load_dotenv()

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

TRUE_VALUES = ("1", "true", "yes", "on")
NETWORK_MODES = ("local", "network")


def env(name: str, default: str | None = None) -> str | None:
    """Return a non-empty environment variable or *default*."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in TRUE_VALUES


def network_mode() -> str:
    """'local' (default: loopback only) or 'network' (may listen on other interfaces)."""
    value = (env("MCP_ADAPTER_NETWORK_MODE", "local") or "local").lower()
    return value if value in NETWORK_MODES else "local"


def allow_internet() -> bool:
    """Whether tools that call external web APIs (Tavily, Wolfram|Alpha) are enabled. Default: off."""
    return env_bool("MCP_ADAPTER_ALLOW_INTERNET", False)


def expose_unavailable() -> bool:
    """Register automation tools even for applications that were not detected. Default: off (adapter mode)."""
    return env_bool("MCP_ADAPTER_EXPOSE_UNAVAILABLE", False)


def enable_var(app_id: str) -> str:
    """Name of the per-application on/off switch, e.g. MCP_ADAPTER_ENABLE_MATLAB."""
    return f"MCP_ADAPTER_ENABLE_{app_id.upper()}"


def app_enabled(app_id: str) -> bool:
    """Per-application switch set from the configuration UI / .env. Default: enabled."""
    return env_bool(enable_var(app_id), True)


def default_timeout() -> int:
    """Timeout (seconds) for launching external software. Override with MCP_ADAPTER_TIMEOUT."""
    try:
        return int(env("MCP_ADAPTER_TIMEOUT", "600") or 600)
    except ValueError:
        return 600


def auth_token() -> str | None:
    """Bearer token for the HTTP transports (MCP_ADAPTER_AUTH_TOKEN): required in network mode, optional locally."""
    return env("MCP_ADAPTER_AUTH_TOKEN")


def tavily_key() -> str | None:
    return env("TAVILY_API") or env("TAVILY_API_KEY")


# Output folder chosen at run time with the set_output_folder tool (process-wide; None = MCP_ADAPTER_OUTPUT_DIR).
# Validation lives in output_folder.py; only validated folders are stored here.
_RUNTIME_OUTPUT: dict[str, Any] = {"folder": None, "app_subfolders": True}


def set_runtime_output(folder: Path | None, app_subfolders: bool = True) -> None:
    _RUNTIME_OUTPUT["folder"] = folder
    _RUNTIME_OUTPUT["app_subfolders"] = bool(app_subfolders) if folder is not None else True


def runtime_output_folder() -> Path | None:
    return _RUNTIME_OUTPUT["folder"]


def app_subfolders() -> bool:
    """Whether each application writes into its own subfolder of the output folder (default: yes)."""
    return bool(_RUNTIME_OUTPUT["app_subfolders"])


def default_output_dir() -> Path:
    """MCP_ADAPTER_OUTPUT_DIR (default <repo>/outputs); also where caches such as the COMSOL example index live."""
    path = Path(env("MCP_ADAPTER_OUTPUT_DIR", str(REPO_ROOT / "outputs")) or "outputs")
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir() -> Path:
    """Directory where adapters write generated scripts/results: the folder chosen with set_output_folder, else
    MCP_ADAPTER_OUTPUT_DIR."""
    folder = _RUNTIME_OUTPUT["folder"]
    return folder if folder is not None else default_output_dir()


def _program_dirs() -> list[str]:
    dirs = []
    if IS_WINDOWS:
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
            val = os.environ.get(var)
            if val:
                dirs.append(val)
        dirs.extend(["C:\\", "D:\\"])
    elif IS_MAC:
        dirs.extend(["/Applications", os.path.expanduser("~/Applications")])
    else:
        dirs.extend(["/opt", "/usr/local", os.path.expanduser("~")])
    return dirs


def find_executable(env_var: str, names: list[str], patterns: list[str] | None = None) -> str | None:
    """Locate an executable.

    Resolution order:
    1. explicit path in the environment variable *env_var* (must exist);
    2. any of *names* on PATH (``shutil.which``);
    3. glob *patterns* (relative to the usual program folders, or absolute); newest version wins.
    """
    explicit = env(env_var)
    if explicit:
        if Path(explicit).exists():
            return str(Path(explicit))
        return explicit  # trust the user even if we cannot stat it (network drives, wrappers)

    for name in names:
        found = shutil.which(name)
        if found:
            return found

    matches: list[str] = []
    for pattern in patterns or []:
        if os.path.isabs(pattern):
            matches.extend(glob.glob(pattern))
        else:
            for base in _program_dirs():
                matches.extend(glob.glob(os.path.join(base, pattern)))
    if matches:
        # Sort so that higher version numbers (lexicographically) come first.
        matches.sort(reverse=True)
        return matches[0]
    return None
