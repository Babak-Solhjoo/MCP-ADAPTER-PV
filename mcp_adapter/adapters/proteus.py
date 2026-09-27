"""Labcenter Proteus adapter.

Proteus has no documented headless simulation CLI; the adapter can open projects in the GUI,
pass through custom command-line arguments (``PROTEUS_ARGS``) and inspect ``.pdsprj`` files, which
are ZIP containers holding the ISIS schematic, ARES layout and project metadata.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from .base import BaseAdapter, RunResult, resolve_path


class ProteusAdapter(BaseAdapter):
    id = "proteus"
    name = "Proteus Design Suite"
    env_var = "PROTEUS_EXE"
    exe_names = ["PDS.EXE", "PDS.exe"]
    exe_patterns = [
        "Labcenter Electronics/Proteus * Professional/BIN/PDS.EXE",
        "Labcenter Electronics/Proteus */BIN/PDS.EXE",
    ]
    install_hint = "Install Proteus and set PROTEUS_EXE to BIN/PDS.EXE."

    def open_project(self, project_file: str, extra_args: list[str] | None = None) -> RunResult:
        """Open a .pdsprj project in Proteus (detached GUI launch)."""
        if not self.is_available():
            return self.unavailable()
        p = resolve_path(project_file)
        return self.launch_detached([self.executable() or "PDS.EXE", *(extra_args or []), str(p)], cwd=str(p.parent))

    def run_cli(self, args: list[str], wait: bool = True, timeout: int | None = None) -> RunResult:
        """Run PDS.EXE with arbitrary arguments (for release-specific switches)."""
        if not self.is_available():
            return self.unavailable()
        cmd = [self.executable() or "PDS.EXE", *args]
        return self.run_command(cmd, timeout=timeout) if wait else self.launch_detached(cmd)

    def project_info(self, project_file: str) -> RunResult:
        """Inspect a .pdsprj container: embedded files, design/layout presence, firmware references."""
        p = resolve_path(project_file)
        if not p.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {p}")
        info: dict[str, Any] = {"file": str(p), "size_bytes": p.stat().st_size}
        try:
            with zipfile.ZipFile(p) as zf:
                names = zf.namelist()
                info["entries"] = names
                info["has_schematic"] = any(n.lower().endswith((".dsn", "isis")) or "schematic" in n.lower() for n in names)
                info["has_layout"] = any(n.lower().endswith((".lyt", "ares")) or "layout" in n.lower() for n in names)
                texts = []
                budget = 20_000_000  # total bytes read from the container (guards against ZIP bombs)
                for n in names[:5000]:
                    if n.lower().endswith((".xml", ".txt", ".ini", ".json")) or "." not in Path(n).name:
                        try:
                            with zf.open(n) as fh:  # stream a bounded prefix instead of inflating the whole entry
                                chunk = fh.read(min(200_000, budget))
                        except Exception:
                            continue
                        budget -= len(chunk)
                        texts.append(chunk.decode("utf-8", "replace"))
                        if budget <= 0:
                            break
                blob = "\n".join(texts)
                # A match starts at a token boundary and is length-bounded: the unanchored pattern backtracked
                # quadratically over long runs of spaces in a crafted project (tens of seconds per 200 KB).
                info["firmware_files"] = sorted(set(re.findall(
                    r"(?<![\w\-. \\/:])[\w\-.\\/:][\w\-. \\/:]{0,259}\.(?:hex|elf|cof|bin)", blob,
                    flags=re.IGNORECASE)))[:50]
                info["microcontrollers"] = sorted(set(re.findall(r"\b(?:PIC\w+|ATMEGA\w+|ATTINY\w+|STM32\w+|ARDUINO\w*|8051|LPC\w+|MSP430\w*|ESP\w+)\b", blob, flags=re.IGNORECASE)))[:50]
                info["version_hints"] = sorted(set(re.findall(r"Proteus\s*[\d.]+", blob)))[:10]
        except zipfile.BadZipFile:
            info["note"] = "Not a ZIP container; legacy Proteus 7 .DSN/.LYT files are binary."
        return RunResult(ok=True, software=self.id, command="", returncode=0, data=info)


proteus_adapter = ProteusAdapter()
