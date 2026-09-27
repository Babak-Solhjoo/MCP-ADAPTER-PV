"""Base class shared by every software adapter.

An adapter knows how to locate the vendor executable and how to launch it headless
(batch mode) with a script, capturing stdout/stderr into a structured RunResult.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import (
    app_enabled,
    app_subfolders,
    default_output_dir,
    default_timeout,
    find_executable,
    output_dir,
)

MAX_CAPTURE = 200_000  # characters of stdout/stderr kept in the result
JSON_START = "<<MCP_JSON>>"
JSON_END = "<<END_MCP_JSON>>"


@dataclass
class RunResult:
    ok: bool
    software: str
    command: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "software": self.software,
            "command": self.command,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": self.artifacts,
        }
        if self.error:
            d["error"] = self.error
        if self.data is not None:
            d["data"] = self.data
        return d


def _trim(text: str) -> str:
    if len(text) <= MAX_CAPTURE:
        return text
    head = text[: MAX_CAPTURE // 2]
    tail = text[-MAX_CAPTURE // 2:]
    return f"{head}\n... [{len(text) - MAX_CAPTURE} characters truncated] ...\n{tail}"


def quote_command(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return " ".join(shlex.quote(a) for a in args)


def extract_json_payload(stdout: str) -> Any:
    """Find the last JSON block delimited by JSON_START/JSON_END markers in *stdout*."""
    matches = re.findall(re.escape(JSON_START) + r"(.*?)" + re.escape(JSON_END), stdout, flags=re.DOTALL)
    if not matches:
        return None
    try:
        return json.loads(matches[-1].strip())
    except json.JSONDecodeError:
        return {"raw": matches[-1].strip()}


def strip_json_payload(stdout: str) -> str:
    return re.sub(re.escape(JSON_START) + r".*?" + re.escape(JSON_END), "", stdout, flags=re.DOTALL).strip()


class BaseAdapter:
    """Subclasses set the class attributes and add high-level methods."""

    id: str = "base"
    name: str = "Base"
    env_var: str = "BASE_EXE"
    exe_names: list[str] = []
    exe_patterns: list[str] = []
    install_hint: str = ""

    # ---- discovery -----------------------------------------------------------------------------
    def executable(self) -> str | None:
        return find_executable(self.env_var, self.exe_names, self.exe_patterns)

    def is_available(self) -> bool:
        return self.executable() is not None

    def status(self) -> dict[str, Any]:
        exe = self.executable()
        return {
            "software": self.id,
            "name": self.name,
            "available": exe is not None,
            "enabled": app_enabled(self.id),
            "executable": exe,
            "env_var": self.env_var,
            "hint": self.install_hint if exe is None else "",
        }

    # ---- helpers -------------------------------------------------------------------------------
    def scripts_dir(self) -> Path:
        """Where this application's generated scripts and results go: <output folder>/<app id>, or the output folder
        itself when set_output_folder switched per-application subfolders off."""
        path = output_dir() / self.id if app_subfolders() else output_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cache_dir(self) -> Path:
        """Caches (e.g. the COMSOL example index) stay in the default output folder, whatever the task folder is."""
        path = default_output_dir() / self.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_temp_script(self, content: str, suffix: str, prefix: str = "script") -> Path:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = f"{prefix}_{stamp}_{uuid.uuid4().hex[:6]}{suffix}"
        path = self.scripts_dir() / name
        path.write_text(content, encoding="utf-8")
        return path

    def unavailable(self, extra: str = "") -> RunResult:
        msg = (
            f"{self.name} executable not found. Set {self.env_var} in .env or add it to PATH. "
            f"{self.install_hint} {extra}"
        ).strip()
        return RunResult(ok=False, software=self.id, command="", error=msg)

    def run_command(
        self,
        args: list[str],
        cwd: str | os.PathLike | None = None,
        timeout: int | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        artifacts: dict[str, Any] | None = None,
        parse_json: bool = False,
    ) -> RunResult:
        """Run *args* headless and capture the output. Never raises for process errors."""
        cmd = quote_command(args)
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        start = time.time()
        try:
            proc = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or default_timeout(),
                env=merged_env,
            )
        except FileNotFoundError as exc:
            return RunResult(ok=False, software=self.id, command=cmd, error=f"Executable not found: {exc}",
                             duration_s=time.time() - start, artifacts=artifacts or {})
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return RunResult(
                ok=False, software=self.id, command=cmd, returncode=None,
                stdout=_trim(out), stderr=_trim(err),
                error=f"Timed out after {exc.timeout} seconds", duration_s=time.time() - start,
                artifacts=artifacts or {},
            )
        except OSError as exc:
            return RunResult(ok=False, software=self.id, command=cmd, error=str(exc),
                             duration_s=time.time() - start, artifacts=artifacts or {})
        stdout = proc.stdout or ""
        data = None
        if parse_json:
            data = extract_json_payload(stdout)
            stdout = strip_json_payload(stdout)
        return RunResult(
            ok=proc.returncode == 0,
            software=self.id,
            command=cmd,
            returncode=proc.returncode,
            stdout=_trim(stdout),
            stderr=_trim(proc.stderr or ""),
            duration_s=time.time() - start,
            artifacts=artifacts or {},
            error=None if proc.returncode == 0 else f"Process exited with code {proc.returncode}",
            data=data,
        )

    def launch_detached(self, args: list[str], cwd: str | None = None) -> RunResult:
        """Start a GUI application without waiting for it (used for 'open project' actions)."""
        cmd = quote_command(args)
        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(args, cwd=cwd, creationflags=flags, close_fds=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, FileNotFoundError) as exc:
            return RunResult(ok=False, software=self.id, command=cmd, error=str(exc))
        return RunResult(ok=True, software=self.id, command=cmd, returncode=None,
                         stdout=f"Launched detached process pid={proc.pid}")


def read_text_if_exists(path: str | os.PathLike, limit: int = MAX_CAPTURE) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return _trim(p.read_text(encoding="utf-8", errors="replace")[:limit])
    except OSError:
        return ""


def resolve_path(path: str | os.PathLike) -> Path:
    """Expand ~ and environment variables and make the path absolute. Relative paths are resolved inside the
    current output folder (the task folder chosen with set_output_folder), not the server's start-up directory."""
    p = Path(os.path.expandvars(os.path.expanduser(str(path))))
    if not p.is_absolute():
        p = output_dir() / p
    return p.resolve()
