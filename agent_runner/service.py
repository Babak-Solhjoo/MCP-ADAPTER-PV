"""Background execution of one agent run at a time, for the configuration UI's chat box.

The run happens in a worker thread with its own asyncio loop; the UI polls ``events_since`` and ``state``.
Tool-call approval (when enabled) blocks the worker until ``decide`` is called from the UI.
"""
from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .runner import RunConfig, RunResult, run_task


class AgentService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._decision_event = threading.Event()
        self._decision = False
        self.status = "idle"  # idle | running | finished | error | stopped
        self.events: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None
        self.result: RunResult | None = None
        self.error: str | None = None
        self.cfg: RunConfig | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.extra_sink: Callable[[dict[str, Any]], None] | None = None   # e.g. a chat's cumulative log
        self.on_finish: Callable[[AgentService], None] | None = None

    # ---- lifecycle -----------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, cfg: RunConfig) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("an agent run is already in progress")
            self._stop.clear()
            self._decision_event.clear()
            self.events = []
            self.pending = None
            self.result = None
            self.error = None
            self.cfg = cfg
            self.status = "running"
            self.started_at = time.time()
            self.finished_at = None
            cfg.on_event = self._on_event
            cfg.stop_requested = self._stop.is_set
            cfg.quiet = True
            cfg.approval_handler = self._approval  # asked only when approvals are on or for always-ask tools
            self._thread = threading.Thread(target=self._run, name="agent-run", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            self.result = asyncio.run(run_task(self.cfg))  # type: ignore[arg-type]
            self.status = "stopped" if self._stop.is_set() else "finished"
        except Exception as exc:  # surfaced to the UI, never crashes the server
            from .runner import redact

            self.error = redact(f"{type(exc).__name__}: {exc}")
            self.status = "error"
            self._on_event({"kind": "note", "t": 0, "text": f"Run failed: {self.error}"})
        finally:
            self.finished_at = time.time()
            self.pending = None
            if self.on_finish:
                try:
                    self.on_finish(self)
                except Exception:  # a broken hook must not hide the run's result
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self.pending:
            self._decision = False
            self._decision_event.set()

    # ---- approval --------------------------------------------------------------------------------
    async def _approval(self, name: str, tool_input: Any) -> bool:
        if self._stop.is_set():  # after Stop every further call is declined without asking
            return False
        self._decision_event.clear()
        self._decision = False
        self.pending = {"id": secrets.token_hex(8), "name": name, "input": tool_input, "since": time.time()}
        self._on_event({"kind": "approval", "t": 0, "name": name, "input": tool_input})
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._decision_event.wait)
        self.pending = None
        return self._decision and not self._stop.is_set()

    def decide(self, approved: bool, call_id: str) -> bool:
        """Answer the pending request; *call_id* must match it, so a stale click cannot approve a later call."""
        pending = self.pending
        if not pending or not call_id or not secrets.compare_digest(str(call_id), pending["id"]):
            return False
        self._decision = bool(approved)
        self._decision_event.set()
        return True

    # ---- reporting -----------------------------------------------------------------------------
    def _on_event(self, entry: dict[str, Any]) -> None:
        entry = dict(entry)
        entry["seq"] = len(self.events) + 1
        self.events.append(entry)
        if self.extra_sink:
            self.extra_sink(entry)

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        return [e for e in self.events if e["seq"] > seq]

    def state(self) -> dict[str, Any]:
        res = self.result
        return {
            "status": self.status,
            "running": self.running,
            "events": len(self.events),
            "pending_approval": self.pending,
            "task": self.cfg.task if self.cfg else "",
            "model": self.cfg.model if self.cfg else "",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": None if res is None else {
                "final_text": res.final_text, "turns": res.turns, "tool_calls": res.tool_calls,
                "stop_reason": res.stop_reason, "usage": res.usage,
                "report_path": str(res.report_path) if res.report_path else None,
            },
        }

    @staticmethod
    def list_reports(report_dir: Path, limit: int = 30) -> list[dict[str, Any]]:
        if not report_dir.exists():
            return []
        files = sorted(report_dir.glob("run_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        return [{"name": p.name, "path": str(p), "size": p.stat().st_size,
                 "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))} for p in files]

    @staticmethod
    def read_report(report_dir: Path, name: str) -> str | None:
        if not name.startswith("run_") or not name.endswith(".md") or "/" in name or "\\" in name:
            return None
        path = report_dir / name
        return path.read_text(encoding="utf-8") if path.exists() else None
