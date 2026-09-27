"""Owner-only permissions for files that hold secrets or private data (.env, the chat store).

Folders on a second Windows drive often inherit "Authenticated Users: Modify" from the drive root, so every other
account on the machine could read the API keys in .env or plant chat files. restrict_to_owner() removes inherited
access and grants full control only to the current user and SYSTEM (POSIX: mode 600/700). It is best effort: a
failure is reported by the return value, never raised.
"""
from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

SYSTEM_SID = "*S-1-5-18"


def _icacls() -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")


def _account() -> str:
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def restrict_to_owner(path: str | os.PathLike, recursive: bool = False) -> bool:
    """Give only the current user (and SYSTEM) access to *path*; folders pass the rule on to new content."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        if os.name != "nt":
            if p.is_dir():
                p.chmod(0o700)
                if recursive:
                    for child in p.rglob("*"):
                        child.chmod(0o700 if child.is_dir() else 0o600)
            else:
                p.chmod(0o600)
            return True
        from .config import child_env

        grant = "(OI)(CI)F" if p.is_dir() else "F"
        args = [_icacls(), str(p), "/inheritance:r", "/grant:r", f"{_account()}:{grant}", f"{SYSTEM_SID}:{grant}"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=60, env=child_env())
        if result.returncode != 0:
            return False
        if recursive and p.is_dir() and any(p.iterdir()):
            # Existing content: drop its own entries so it inherits the folder's owner-only rule. (Applying the
            # folder grant with /T would strip files' inheritance without granting anything: unreadable files.)
            reset = [_icacls(), str(p / "*"), "/reset", "/T", "/C", "/Q"]
            result = subprocess.run(reset, capture_output=True, text=True, timeout=120, env=child_env())
            return result.returncode == 0
        return True
    except (OSError, subprocess.SubprocessError):
        return False
