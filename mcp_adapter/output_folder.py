"""Where the adapters write their files, chosen at run time with the set_output_folder tool.

A long-running server (Claude Desktop, the local HTTP endpoint) starts once, before any task exists, so its default
output folder (MCP_ADAPTER_OUTPUT_DIR, default <repo>/outputs) cannot be the folder the user gives for a task.
set_output_folder points every adapter at that folder instead: generated scripts, netlists, logs and results are
written there, and relative paths in tool arguments are resolved there.

The folder is checked in code before it is used. It must be an absolute path to an existing, writable folder on a
local disk. Drive roots, the home folder itself, hidden folders, system/program/application-data folders and the
adapter's own source folder are refused, and so are network locations (results stay on this machine).
MCP_ADAPTER_OUTPUT_ROOTS in .env restricts the choice further to the listed folders, and a server started for one
task working directory (MCP_ADAPTER_WORK_DIR) only accepts folders inside it.

These checks prevent mistakes and keep results on local disks. They are not a sandbox: scripting tools such as
matlab_run_code run code with the user's rights anyway.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from .config import (
    IS_WINDOWS,
    PACKAGE_DIR,
    REPO_ROOT,
    app_subfolders,
    default_output_dir,
    env,
    output_dir,
    runtime_output_folder,
    set_runtime_output,
)
from .workspace import workspace_root


class OutputFolderError(ValueError):
    """The requested output folder is not allowed."""


def _norm(p: Path) -> str:
    return os.path.normcase(str(p)).rstrip("\\/") or os.sep


def _inside(p: Path, root: Path) -> bool:
    a, b = _norm(p), _norm(root)
    return a == b or a.startswith(b if b.endswith(os.sep) else b + os.sep)


def protected_locations() -> list[Path]:
    """Folders (and everything below them) that are never accepted as an output folder."""
    home = Path.home()
    out: list[Path] = [PACKAGE_DIR]
    if IS_WINDOWS:
        for var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
                    "APPDATA", "LOCALAPPDATA"):
            val = os.environ.get(var)
            if val:
                out.append(Path(val))
        out.append(home / "AppData")
    else:
        out += [Path(p) for p in ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys",
                                  "/usr", "/var/lib", "/var/log", "/System", "/Library", "/Applications")]
        out.append(home / "Library")
    return [p.resolve() for p in out]


def allowed_roots() -> list[Path]:
    """Folders listed in MCP_ADAPTER_OUTPUT_ROOTS (separated by ';' or the OS path separator); empty = no limit."""
    raw = env("MCP_ADAPTER_OUTPUT_ROOTS")
    if not raw:
        return []
    parts = [x.strip().strip('"') for x in raw.replace(os.pathsep, ";").split(";")]
    return [Path(os.path.expandvars(os.path.expanduser(x))).resolve() for x in parts if x]


def is_network_path(p: Path) -> bool:
    """UNC paths and, on Windows, mapped network drives."""
    s = str(p)
    if s.startswith(("\\\\", "//")):
        return True
    if IS_WINDOWS and len(p.drive) == 2:
        try:
            import ctypes

            return ctypes.windll.kernel32.GetDriveTypeW(p.drive + "\\") == 4  # DRIVE_REMOTE
        except Exception:  # noqa: BLE001 - detection is best effort
            return False
    return False


def check_output_folder(folder: str) -> Path:
    """Validate *folder* and return its resolved path, or raise OutputFolderError saying why it is refused."""
    raw = (folder or "").strip().strip('"')
    if not raw:
        raise OutputFolderError("No folder given.")
    p = Path(os.path.expanduser(raw))  # no %VAR% expansion: it could echo a variable's value in errors
    if is_network_path(p):
        raise OutputFolderError(f"{p} is a network location; results are only written to local disks.")
    if not p.is_absolute():
        raise OutputFolderError(f"{folder!r} is not an absolute path; give the full path of the folder "
                                "the user chose (for example D:\\Projects\\PCB).")
    p = p.resolve()
    if is_network_path(p):
        raise OutputFolderError(f"{p} is a network location; results are only written to local disks.")
    if not p.is_dir():
        raise OutputFolderError(f"{p} does not exist or is not a folder. Ask the user to create it (or use an "
                                "existing folder); the adapter does not create task folders itself.")
    if p.parent == p:
        raise OutputFolderError(f"{p} is a drive or filesystem root; choose a folder inside it.")
    if _norm(p) == _norm(Path.home().resolve()):
        raise OutputFolderError(f"{p} is the home folder itself; choose a folder inside it.")
    hidden = next((part for part in p.parts[1:] if part.startswith(".")), None)
    if hidden:
        raise OutputFolderError(f"{p} is inside a hidden folder ({hidden}); choose a visible folder.")
    for loc in protected_locations():
        if _inside(p, loc):
            raise OutputFolderError(f"{p} is inside a protected location ({loc}); system, program, "
                                    "application-data and adapter source folders are refused.")
    if _inside(p, REPO_ROOT) and not _inside(p, REPO_ROOT / "outputs"):
        raise OutputFolderError(f"{p} is inside the adapter's own folder (it holds .env and the code); use its "
                                "outputs folder or a folder of your own.")
    ws = workspace_root()
    if ws is not None and not _inside(p, ws):
        raise OutputFolderError(f"This server is confined to the task's working directory {ws}; the output "
                                "folder must be inside it.")
    roots = allowed_roots()
    if roots and not any(_inside(p, r) for r in roots):
        raise OutputFolderError(f"{p} is not inside the folders allowed by MCP_ADAPTER_OUTPUT_ROOTS "
                                f"({'; '.join(str(r) for r in roots)}).")
    probe = p / f".mcp_adapter_write_test_{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise OutputFolderError(f"{p} is not writable: {exc}") from None
    return p


def set_output_folder(folder: str, per_application_subfolders: bool = True) -> dict[str, Any]:
    """Point every adapter at *folder*; an empty value goes back to the default. Raises OutputFolderError."""
    previous = str(output_dir())
    if not (folder or "").strip():
        set_runtime_output(None)
        return {"ok": True, "output_folder": str(output_dir()), "previous": previous, "default": True,
                "note": "Back to the default output folder (MCP_ADAPTER_OUTPUT_DIR)."}
    p = check_output_folder(folder)
    set_runtime_output(p, per_application_subfolders)
    return {"ok": True, "output_folder": str(p), "previous": previous, "default": False,
            "per_application_subfolders": per_application_subfolders,
            "files_go_to": str(p / "<application>") if per_application_subfolders else str(p),
            "note": "Every tool now writes its generated scripts and results into this folder, and relative paths "
                    "in tool arguments are resolved inside it. The setting applies to this server process (all "
                    "chats of this client) until it is changed, reset with an empty folder, or the server restarts."}


def describe() -> dict[str, Any]:
    folder = runtime_output_folder()
    return {
        "current": str(output_dir()),
        "source": "set_output_folder" if folder is not None else "MCP_ADAPTER_OUTPUT_DIR (default)",
        "default": str(default_output_dir()),
        "per_application_subfolders": app_subfolders(),
        "allowed_roots": [str(r) for r in allowed_roots()] or "any local folder that passes the checks",
        "rules": "absolute path to an existing, writable folder on a local disk; drive roots, the home folder "
                 "itself, hidden, system, program, application-data, adapter source and network folders are refused",
    }
