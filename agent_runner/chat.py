"""Multiple parallel chats, each with its own working folder, settings, message history and background run.

A chat is a conversation with the agent. Every "send" starts one agent run (with the earlier turns of the
chat as context) in its own thread and its own MCP server process, so several chats can work at once.
Chats are persisted as JSON files so they survive restarts of the UI.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runner import DEFAULT_MAX_TURNS, DEFAULT_MODEL, PROVIDERS, REPO_ROOT, RunConfig
from .service import AgentService

MAX_STORED_EVENTS = 3000
DEFAULT_STORE_DIR = REPO_ROOT / "outputs" / "chats"


def _truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ChatSettings:
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    base_url: str = ""
    effort: str = ""
    max_turns: int = DEFAULT_MAX_TURNS
    approve: bool = False
    show_thinking: bool = False
    work_dir: str = ""
    work_dir_access: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, base: ChatSettings | None = None) -> ChatSettings:
        s = ChatSettings(**asdict(base)) if base else cls()
        data = data or {}
        if "provider" in data:
            prov = str(data["provider"]).strip().lower()
            s.provider = prov if prov in PROVIDERS else s.provider
        if "model" in data and str(data["model"]).strip():
            model = str(data["model"]).strip()
            if MODEL_ID_RE.fullmatch(model):  # model ids go into .env and API requests: no spaces or control chars
                s.model = model
        if "base_url" in data:
            s.base_url = str(data["base_url"] or "").strip()
        if "effort" in data:
            eff = str(data["effort"] or "").strip().lower()
            s.effort = eff if eff in ("", "low", "medium", "high", "xhigh", "max") else ""
        if "max_turns" in data:
            try:
                s.max_turns = max(1, min(int(data["max_turns"]), 500))
            except (TypeError, ValueError):
                pass
        for key in ("approve", "show_thinking", "work_dir_access"):
            if key in data:
                setattr(s, key, _truthy(data[key]))
        if "work_dir" in data:
            s.work_dir = str(data["work_dir"] or "").strip().strip('"')
        return s

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Chat:
    def __init__(self, chat_id: str, title: str, settings: ChatSettings, created: float | None = None):
        self.id = chat_id
        self.title = title
        self.settings = settings
        self.created = created or time.time()
        self.updated = self.created
        self.messages: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.status = "idle"      # idle | running | finished | error | stopped | interrupted
        self.error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.runs = 0
        self.service = AgentService()

    # ---- serialisation ---------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "created": self.created, "updated": self.updated,
            "status": "running" if self.service.running else self.status, "running": self.service.running,
            "pending_approval": bool(self.service.pending), "provider": self.settings.provider,
            "model": self.settings.model, "work_dir": self.settings.work_dir,
            "work_dir_access": self.settings.work_dir_access, "messages": len(self.messages), "runs": self.runs,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "settings": self.settings.to_dict(), "messages": self.messages,
                "events": len(self.events), "error": self.error, "last_result": self.last_result,
                "pending": self.service.pending}

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "created": self.created, "updated": self.updated,
                "settings": self.settings.to_dict(), "messages": self.messages,
                "events": self.events[-MAX_STORED_EVENTS:], "status": self.status, "error": self.error,
                "last_result": self.last_result, "runs": self.runs}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Chat:
        chat_id = str(data.get("id", ""))
        if not CHAT_ID_RE.fullmatch(chat_id):  # ids are also file names: never accept anything else
            raise ValueError(f"invalid chat id {chat_id!r}")
        chat = cls(chat_id, str(data.get("title") or "Chat")[:200], ChatSettings.from_dict(data.get("settings")),
                   data.get("created"))
        chat.updated = data.get("updated", chat.created)
        chat.messages = [_clean_message(m) for m in data.get("messages", []) if isinstance(m, dict)]
        chat.events = [_clean_event(e) for e in data.get("events", []) if isinstance(e, dict)]
        chat.status = "interrupted" if data.get("status") == "running" else data.get("status", "idle")
        chat.error = data.get("error")
        chat.last_result = data.get("last_result")
        chat.runs = int(data.get("runs", 0))
        return chat

    def append_event(self, entry: dict[str, Any]) -> None:
        e = dict(entry)
        e["seq"] = len(self.events) + 1
        e["run"] = self.runs
        self.events.append(e)
        if len(self.events) > MAX_STORED_EVENTS * 2:
            del self.events[: len(self.events) - MAX_STORED_EVENTS]

    def history(self) -> list[dict[str, str]]:
        return [{"role": m["role"], "content": m["text"]} for m in self.messages if m.get("role") in ("user", "assistant")]


CHAT_ID_RE = re.compile(r"[0-9a-f]{12}")
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")


def _num(value: Any, kind: type = int) -> Any:
    try:
        return kind(value)
    except (TypeError, ValueError):
        return 0


def _clean_message(m: dict[str, Any]) -> dict[str, Any]:
    """Messages loaded from disk: numeric fields really are numbers (they are shown in the page)."""
    m = dict(m)
    for key in ("turns", "tool_calls"):
        if key in m:
            m[key] = _num(m[key])
    return m


def _clean_event(e: dict[str, Any]) -> dict[str, Any]:
    e = dict(e)
    for key in ("t", "seq", "run"):
        if key in e:
            e[key] = _num(e[key], float if key == "t" else int)
    return e


class ChatManager:
    def __init__(self, store_dir: Path | None = None, defaults: ChatSettings | None = None):
        self.store_dir = Path(store_dir) if store_dir else DEFAULT_STORE_DIR
        self.defaults = defaults or ChatSettings()
        self.chats: dict[str, Chat] = {}
        self._lock = threading.RLock()
        self.load()

    # ---- persistence -----------------------------------------------------------------------------
    def load(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        from mcp_adapter.fileperm import restrict_to_owner

        restrict_to_owner(self.store_dir, recursive=True)  # chats are private; new files inherit the rule
        for path in sorted(self.store_dir.glob("chat_*.json")):
            try:
                chat = Chat.from_json(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError):
                continue
            self.chats[chat.id] = chat

    def _path(self, chat: Chat) -> Path:
        return self.store_dir / f"chat_{chat.id}.json"

    def persist(self, chat: Chat) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(chat).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(chat.to_json(), indent=1, default=str), encoding="utf-8")
        os.replace(tmp, self._path(chat))

    # ---- CRUD ------------------------------------------------------------------------------------
    def create(self, settings: dict[str, Any] | None = None, title: str | None = None) -> Chat:
        with self._lock:
            chat = Chat(uuid.uuid4().hex[:12], title or "New chat", ChatSettings.from_dict(settings, self.defaults))
            self.chats[chat.id] = chat
            self.persist(chat)
            return chat

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.summary() for c in sorted(self.chats.values(), key=lambda c: c.updated, reverse=True)]

    def get(self, chat_id: str) -> Chat:
        chat = self.chats.get(chat_id)
        if chat is None:
            raise KeyError(f"unknown chat {chat_id!r}")
        return chat

    def delete(self, chat_id: str) -> None:
        with self._lock:
            chat = self.get(chat_id)
            if chat.service.running:
                chat.service.stop()
            self.chats.pop(chat_id, None)
            try:
                self._path(chat).unlink()
            except OSError:
                pass

    def rename(self, chat_id: str, title: str) -> Chat:
        chat = self.get(chat_id)
        chat.title = title.strip()[:120] or chat.title
        chat.updated = time.time()
        self.persist(chat)
        return chat

    def update_settings(self, chat_id: str, patch: dict[str, Any]) -> Chat:
        chat = self.get(chat_id)
        chat.settings = ChatSettings.from_dict(patch, chat.settings)
        chat.updated = time.time()
        self.persist(chat)
        return chat

    # ---- running ---------------------------------------------------------------------------------
    def send(self, chat_id: str, text: str) -> Chat:
        chat = self.get(chat_id)
        text = text.strip()
        if not text:
            raise ValueError("the message is empty")
        if chat.service.running:
            raise RuntimeError("this chat is still working; wait for it to finish or stop it")
        s = chat.settings
        cfg = RunConfig(
            task=text, provider=s.provider, model=s.model, base_url=s.base_url or None,
            effort=s.effort or None, max_turns=s.max_turns, approve=s.approve, show_thinking=s.show_thinking,
            work_dir=Path(s.work_dir).expanduser() if s.work_dir else None, work_dir_access=s.work_dir_access,
            history=chat.history(),
        )
        if chat.title == "New chat" and not chat.messages:
            chat.title = (text[:60] + ("..." if len(text) > 60 else "")).replace("\n", " ")
        chat.messages.append({"role": "user", "text": text, "ts": time.time()})
        chat.runs += 1
        chat.status = "running"
        chat.error = None
        chat.updated = time.time()
        service = chat.service
        service.extra_sink = chat.append_event
        service.on_finish = lambda svc, c=chat: self._finished(c, svc)
        service.start(cfg)
        self.persist(chat)
        return chat

    def _finished(self, chat: Chat, service: AgentService) -> None:
        res = service.result
        if res is not None:
            chat.messages.append({"role": "assistant", "text": res.final_text or "(the agent finished without a final message)",
                                  "ts": time.time(), "report": str(res.report_path) if res.report_path else None,
                                  "usage": res.usage, "turns": res.turns, "tool_calls": res.tool_calls})
            chat.last_result = {"turns": res.turns, "tool_calls": res.tool_calls, "stop_reason": res.stop_reason,
                                "usage": res.usage, "report_path": str(res.report_path) if res.report_path else None}
        if service.error:
            chat.messages.append({"role": "note", "text": f"Run failed: {service.error}", "ts": time.time()})
            chat.error = service.error
        chat.status = service.status
        chat.updated = time.time()
        self.persist(chat)

    def stop(self, chat_id: str) -> Chat:
        chat = self.get(chat_id)
        chat.service.stop()
        return chat

    def decide(self, chat_id: str, approve: bool, call_id: str = "") -> Chat:
        chat = self.get(chat_id)
        if not chat.service.pending:
            raise RuntimeError("no tool call is waiting for approval")
        if not chat.service.decide(approve, call_id):
            raise RuntimeError("that approval request is no longer pending (reload the chat)")
        return chat

    def events(self, chat_id: str, since: int = 0) -> list[dict[str, Any]]:
        chat = self.get(chat_id)
        return [e for e in chat.events if e["seq"] > since]

    def any_running(self) -> bool:
        return any(c.service.running for c in self.chats.values())
