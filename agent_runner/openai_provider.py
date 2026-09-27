"""OpenAI provider for the agent runner: Chat Completions with function calling, manual tool loop.

The MCP tools are converted to OpenAI function tools; every ``tool_calls`` block is executed against the
MCP session and returned as a ``role: tool`` message until the model answers without tool calls.
Works with any OpenAI-compatible endpoint through ``OPENAI_BASE_URL`` / ``RunConfig.base_url``.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .runner import ApprovalHandler, RunConfig, RunResult, Transcript

DEFAULT_OPENAI_MODEL = "gpt-5"
OPENAI_MODEL_CHOICES = ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "o4-mini"]
REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
MAX_TOOL_RESULT_CHARS = 100_000


def mcp_tools_to_openai(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert MCP tool definitions to OpenAI function-tool definitions."""
    out = []
    for t in tools:
        schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "")[:1024],
                "parameters": schema,
            },
        })
    return out


def mcp_result_to_text(result: Any) -> tuple[str, bool]:
    """Flatten an MCP CallToolResult to text plus an error flag."""
    parts = []
    for c in getattr(result, "content", None) or []:
        if getattr(c, "type", "") == "text":
            parts.append(c.text)
        else:
            dump = getattr(c, "model_dump", None)
            parts.append(json.dumps(dump() if dump else str(c), default=str))
    is_error = bool(getattr(result, "isError", None) or getattr(result, "is_error", False))
    return "\n".join(parts), is_error


OFFICIAL_OPENAI_HOST = "api.openai.com"


def client_kwargs(base_url: str | None) -> dict[str, Any]:
    """Client options for *base_url*. The OpenAI key is only ever sent to api.openai.com; any other
    OpenAI-compatible endpoint gets OPENAI_COMPAT_API_KEY (or a dummy key) and must use HTTPS unless it runs on
    this computer."""
    from urllib.parse import urlparse

    from mcp_adapter.security import is_loopback_host

    kwargs: dict[str, Any] = {}
    if not base_url:
        return kwargs
    u = urlparse(base_url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("https", "http") or not host:
        raise ValueError(f"invalid OpenAI-compatible base URL: {base_url!r}")
    if u.scheme == "http" and not is_loopback_host(host):
        raise ValueError("plain http:// endpoints are only allowed on this computer (localhost); use https://")
    kwargs["base_url"] = base_url
    if host != OFFICIAL_OPENAI_HOST:
        kwargs["api_key"] = os.environ.get("OPENAI_COMPAT_API_KEY") or "not-needed"
    return kwargs


def make_client(cfg: RunConfig):
    from openai import AsyncOpenAI

    return AsyncOpenAI(**client_kwargs(cfg.base_url))  # official endpoint: the key comes from OPENAI_API_KEY


def _usage_add(result: RunResult, usage: Any) -> None:
    if usage is None:
        return
    for src, dst in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        val = getattr(usage, src, None) or 0
        result.usage[dst] = result.usage.get(dst, 0) + int(val)


async def run_openai_loop(cfg: RunConfig, session: Any, tools: list[Any], transcript: Transcript,
                          result: RunResult, approval: ApprovalHandler | None = None, client: Any = None) -> None:
    """Drive the task with an OpenAI model until it stops calling tools, the turn cap or a stop request."""
    from .runner import build_system_prompt, conversation_messages

    client = client or make_client(cfg)
    oa_tools = mcp_tools_to_openai(tools)
    offered = {t.name for t in tools}  # a model must not call tools that were not given to it (--only)
    messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(cfg)}]
    messages += conversation_messages(cfg)
    for _ in range(cfg.max_turns):
        kwargs: dict[str, Any] = {"model": cfg.model, "messages": messages}
        if oa_tools:
            kwargs["tools"] = oa_tools
            kwargs["tool_choice"] = "auto"
        if cfg.effort and cfg.model.lower().startswith(REASONING_PREFIXES):
            kwargs["reasoning_effort"] = EFFORT_MAP.get(cfg.effort, "medium")
        response = await client.chat.completions.create(**kwargs)
        result.turns += 1
        _usage_add(result, getattr(response, "usage", None))
        choice = response.choices[0]
        msg = choice.message
        text = (msg.content or "").strip() if isinstance(msg.content, str) else ""
        if text:
            transcript.add("assistant", text=text)
            result.final_text = text
        tool_calls = list(msg.tool_calls or [])
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                               for tc in tool_calls],
            })
        else:
            messages.append({"role": "assistant", "content": msg.content or ""})
            result.stop_reason = choice.finish_reason or "stop"
            return
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            result.tool_calls += 1
            transcript.add("tool_call", name=name, input=args)
            if name not in offered:
                out, is_err = json.dumps({"ok": False, "error": f"Tool {name!r} is not available in this run."}), True
            elif approval is not None and not await approval(name, args):
                out, is_err = json.dumps({"ok": False, "error": "The user declined to run this tool call."}), True
            else:
                try:
                    out, is_err = mcp_result_to_text(await session.call_tool(name, args))
                except Exception as exc:  # tool failure must reach the model, not crash the loop
                    out, is_err = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), True
            transcript.add("tool_result", name=name, content=out, is_error=is_err)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out[:MAX_TOOL_RESULT_CHARS]})
        result.stop_reason = "tool_use"
        if cfg.stop_requested and cfg.stop_requested():
            transcript.add("note", text="Stopped by the user.")
            return
    transcript.add("note", text=f"Stopped after reaching max_turns={cfg.max_turns}.")
