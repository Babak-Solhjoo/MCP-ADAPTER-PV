"""Workspace tools: file and command access confined to the task's working directory.

Enabled only when both are set in the environment of the MCP server process:
    MCP_ADAPTER_WORK_DIR=<folder>          the working directory of the task
    MCP_ADAPTER_WORK_DIR_ACCESS=true       the user switched "full access inside the folder" on

Every path argument is resolved inside the working directory; anything that escapes it (``..``, absolute
paths elsewhere, symlinks pointing outside) is refused. ``workspace_run`` executes a shell command with the
folder as current directory - that is the "full access" the switch grants, so keep it off for untrusted tasks.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import env, env_bool

MAX_READ_CHARS = 200_000
MAX_LIST_ENTRIES = 2000
MAX_SEARCH_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


class WorkspaceError(ValueError):
    """A path or operation is not allowed inside the workspace."""


def workspace_root() -> Path | None:
    raw = env("MCP_ADAPTER_WORK_DIR")
    return Path(raw).expanduser().resolve() if raw else None


def workspace_enabled() -> bool:
    return workspace_root() is not None and env_bool("MCP_ADAPTER_WORK_DIR_ACCESS", False)


def resolve_inside(root: Path, rel: str | os.PathLike = ".") -> Path:
    """Resolve *rel* (relative to the root, or absolute) and make sure it stays inside the root."""
    candidate = Path(rel)
    p = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise WorkspaceError(f"{rel!r} is outside the working directory {root}") from None
    return p


def _entry(root: Path, p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"path": str(p.relative_to(root)).replace("\\", "/"), "type": "dir" if p.is_dir() else "file",
            "size": st.st_size if p.is_file() else None,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))}


def ws_info(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    files = dirs = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dirs += len(dirnames)
        files += len(filenames)
        if files + dirs > 50_000:
            break
    return {"root": str(root), "exists": True, "files": files, "directories": dirs,
            "note": "All paths given to workspace_* tools are relative to this root (absolute paths inside it are accepted)."}


def ws_list(root: Path, path: str = ".", recursive: bool = False, pattern: str | None = None,
            max_entries: int = 500) -> dict[str, Any]:
    base = resolve_inside(root, path)
    if not base.exists():
        raise WorkspaceError(f"{path!r} does not exist")
    limit = max(1, min(max_entries, MAX_LIST_ENTRIES))
    entries: list[dict[str, Any]] = []
    truncated = False
    if base.is_file():
        return {"path": path, "entries": [_entry(root, base)], "truncated": False}
    if recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(dirnames + filenames):
                p = Path(dirpath) / name
                if pattern and not fnmatch.fnmatch(name, pattern):
                    continue
                entries.append(_entry(root, p))
                if len(entries) >= limit:
                    truncated = True
                    break
            if truncated:
                break
    else:
        for p in sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if pattern and not fnmatch.fnmatch(p.name, pattern):
                continue
            entries.append(_entry(root, p))
            if len(entries) >= limit:
                truncated = True
                break
    return {"path": str(base.relative_to(root)).replace("\\", "/") or ".", "entries": entries, "truncated": truncated}


def ws_read(root: Path, path: str, max_chars: int = MAX_READ_CHARS, offset: int = 0) -> dict[str, Any]:
    p = resolve_inside(root, path)
    if not p.is_file():
        raise WorkspaceError(f"{path!r} is not a file")
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        binary = b"\x00" in data[:4096]
    limit = max(1, min(max_chars, MAX_READ_CHARS))
    chunk = text[offset: offset + limit]
    return {"path": path, "size": len(data), "binary": binary, "offset": offset, "content": chunk,
            "truncated": offset + limit < len(text)}


def ws_write(root: Path, path: str, content: str, append: bool = False, create_dirs: bool = True) -> dict[str, Any]:
    p = resolve_inside(root, path)
    if p.is_dir():
        raise WorkspaceError(f"{path!r} is a directory")
    if create_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return {"path": path, "bytes": p.stat().st_size, "appended": append}


def ws_mkdir(root: Path, path: str) -> dict[str, Any]:
    p = resolve_inside(root, path)
    p.mkdir(parents=True, exist_ok=True)
    return {"path": path, "created": True}


def ws_delete(root: Path, path: str, recursive: bool = False) -> dict[str, Any]:
    p = resolve_inside(root, path)
    if p == root:
        raise WorkspaceError("refusing to delete the working directory itself")
    if not p.exists():
        raise WorkspaceError(f"{path!r} does not exist")
    if p.is_dir():
        if recursive:
            shutil.rmtree(p)
        else:
            try:
                p.rmdir()
            except OSError:
                raise WorkspaceError(f"{path!r} is not empty; pass recursive=true to delete it with its contents") from None
    else:
        p.unlink()
    return {"path": path, "deleted": True}


def ws_move(root: Path, source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
    src = resolve_inside(root, source)
    dst = resolve_inside(root, destination)
    if not src.exists():
        raise WorkspaceError(f"{source!r} does not exist")
    if dst.exists() and not overwrite:
        raise WorkspaceError(f"{destination!r} already exists; pass overwrite=true")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"source": source, "destination": destination, "moved": True}


def ws_search(root: Path, text: str, glob: str = "*", path: str = ".", max_results: int = 200,
              case_insensitive: bool = True) -> dict[str, Any]:
    base = resolve_inside(root, path)
    needle = text.lower() if case_insensitive else text
    hits: list[dict[str, Any]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob):
                continue
            p = Path(dirpath) / name
            try:
                if p.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            scanned += 1
            for lineno, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                hay = line.lower() if case_insensitive else line
                if needle in hay:
                    hits.append({"path": str(p.relative_to(root)).replace("\\", "/"), "line": lineno,
                                 "text": line.strip()[:300]})
                    if len(hits) >= max_results:
                        return {"hits": hits, "files_scanned": scanned, "truncated": True}
    return {"hits": hits, "files_scanned": scanned, "truncated": False}


def ws_run(root: Path, command: str, timeout: int = 300) -> dict[str, Any]:
    start = time.time()
    try:
        proc = subprocess.run(command, shell=True, cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=max(1, min(timeout, 3600)))
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "cwd": str(root), "timed_out": True, "returncode": None,
                "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "",
                "duration_s": round(time.time() - start, 2)}
    return {"command": command, "cwd": str(root), "timed_out": False, "returncode": proc.returncode,
            "stdout": proc.stdout[-50000:], "stderr": proc.stderr[-20000:], "duration_s": round(time.time() - start, 2)}


def register_workspace_tools(server, root: Path) -> list[str]:
    """Register the workspace_* tools on an MCP server for *root*. Returns the tool names."""

    def _wrap(fn):
        def inner(*args, **kwargs):
            try:
                return {"ok": True, **fn(root, *args, **kwargs)}
            except WorkspaceError as exc:
                return {"ok": False, "error": str(exc)}
            except OSError as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return inner

    @server.tool()
    def workspace_info() -> dict[str, Any]:
        """Where the task's working directory is and how big it is. All workspace_* paths are relative to it."""
        return _wrap(ws_info)()

    @server.tool()
    def workspace_list(path: str = ".", recursive: bool = False, pattern: str | None = None,
                       max_entries: int = 500) -> dict[str, Any]:
        """List files and folders inside the working directory (optionally recursive, filtered by a glob pattern)."""
        return _wrap(ws_list)(path, recursive, pattern, max_entries)

    @server.tool()
    def workspace_read(path: str, max_chars: int = 200000, offset: int = 0) -> dict[str, Any]:
        """Read a text file inside the working directory (use offset/max_chars to page through large files)."""
        return _wrap(ws_read)(path, max_chars, offset)

    @server.tool()
    def workspace_write(path: str, content: str, append: bool = False) -> dict[str, Any]:
        """Create or overwrite (or append to) a text file inside the working directory; parent folders are created."""
        return _wrap(ws_write)(path, content, append)

    @server.tool()
    def workspace_mkdir(path: str) -> dict[str, Any]:
        """Create a folder (and parents) inside the working directory."""
        return _wrap(ws_mkdir)(path)

    @server.tool()
    def workspace_delete(path: str, recursive: bool = False) -> dict[str, Any]:
        """Delete a file or folder inside the working directory (recursive=true for non-empty folders)."""
        return _wrap(ws_delete)(path, recursive)

    @server.tool()
    def workspace_move(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        """Move or rename a file or folder inside the working directory."""
        return _wrap(ws_move)(source, destination, overwrite)

    @server.tool()
    def workspace_search(text: str, glob: str = "*", path: str = ".", max_results: int = 200,
                         case_insensitive: bool = True) -> dict[str, Any]:
        """Search text files inside the working directory for a string; returns file, line number and the line."""
        return _wrap(ws_search)(text, glob, path, max_results, case_insensitive)

    @server.tool()
    def workspace_run(command: str, timeout_seconds: int = 300) -> dict[str, Any]:
        """Run a shell command with the working directory as current folder and return stdout/stderr/return code.

        Use it for build/convert/analysis steps on the task's files (e.g. python scripts, git, converters).
        """
        return _wrap(ws_run)(command, timeout_seconds)

    return ["workspace_info", "workspace_list", "workspace_read", "workspace_write", "workspace_mkdir",
            "workspace_delete", "workspace_move", "workspace_search", "workspace_run"]
