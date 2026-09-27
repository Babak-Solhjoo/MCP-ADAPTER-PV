"""Core of the agent runner: MCP session + model provider loop + transcript/report.

Two providers are supported and chosen with ``RunConfig.provider``:
* ``anthropic`` – the Anthropic SDK's tool runner drives the loop (MCP tools wrapped with async_mcp_tool);
* ``openai``    – Chat Completions with function calling and a manual loop (``openai_provider.py``).

The same function powers the CLI (``python -m agent_runner``) and the configuration UI's chat box:
``RunConfig.on_event`` receives every step as a dict, ``approval_handler`` can pause before a tool call,
and ``stop_requested`` lets a caller abort between turns.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")
load_dotenv()

DEFAULT_MODEL = "claude-opus-5"
MODEL_CHOICES = ["claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5"]
PROVIDERS = ("anthropic", "openai")
PROVIDER_KEY_ENV = {"anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), "openai": ("OPENAI_API_KEY",)}
DEFAULT_MAX_TOKENS = 16000
DEFAULT_MAX_TURNS = 40
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")

# Catalog/status/time tools are always handed to the agent, whatever --only says.
CORE_TOOL_PREFIXES = ("list_", "recommend_application", "compare_applications", "describe_tool", "search_tools",
                      "software_overview", "security_policy", "adapter_status", "time_")

SYSTEM_PROMPT = """You are an engineering assistant operating a local workstation through MCP tools that wrap
engineering and design applications (MATLAB, Simulink, Mathematica, COMSOL, Photoshop, OrCAD, Altium, Proteus,
Vivado, AutoCAD, HFSS, Feko, EAGLE, draw.io) plus a time tool.

Working method:
1. Start by calling list_software (or adapter_status) to learn which applications are installed here. Automation
   tools exist only for installed applications; if a tool you expected is not in your tool list, that
   application is not available on this machine - say so instead of trying to work around it.
2. Before driving an application, look up how its commands work with search_tools / describe_tool; use the
   documented syntax and examples rather than guessing.
3. Prefer small, verifiable steps: run a short command, check the result (ok, stdout, data, artifacts), then
   continue. Every automation tool returns a JSON object; when ok is false, read error and adapt.
4. Save generated files (figures, exports, reports) in the working directory given below (or the tools' output
   folder when none is given), and report the absolute paths of everything you produced.
5. Finish with a concise summary: what was done, the key results (numbers, files), and anything that failed or
   could not be done, with the reason.
Do not ask the user questions during the run; make reasonable engineering assumptions and state them.

Choosing the application: for any engineering task call recommend_application(task) first. Its ranking is a
suggestion, not a rule: normally use the suggested INSTALLED specialised application: HFSS or Feko for
antennas/RF/S-parameters (Feko for antenna placement on platforms, RCS, EMC cables), COMSOL for any distributed or coupled physics (heat, structural, fluid, acoustics,
electromagnetics and motors, electrochemistry and batteries, plasma, semiconductors, optics, particles), PSpice for
circuits with real components, Altium for PCB (EAGLE only for existing EAGLE files: reading, BOM, CAM output), Proteus for firmware with its circuit, Vivado for FPGA, Simulink for
block-diagram systems, Mathematica for symbolic math. Use MATLAB for numerics, data analysis, plotting and
post-processing, not as a substitute for a specialised simulator. If the first choice is not installed, or the
user named a different application, use an installed alternative from the comparison (compare_applications lists
each candidate's limits) and state that trade-off in your summary. If no suitable application is installed, say so
explicitly before doing anything else.

COMSOL working method: search its installed example library first (comsol_search_examples), start from the closest
documented example (comsol_run_example, or adapt its Java script from comsol_example_info and run it with
comsol_build_from_java), then read results with comsol_inspect_model and comsol_evaluate."""

EventSink = Callable[[dict[str, Any]], None]
ApprovalHandler = Callable[[str, Any], Awaitable[bool]]


def provider_key_present(provider: str) -> bool:
    """Whether the environment holds credentials for *provider*."""
    return any(os.environ.get(var) for var in PROVIDER_KEY_ENV.get(provider, ()))


def provider_sdk_available(provider: str) -> bool:
    try:
        if provider == "openai":
            import openai  # noqa: F401
        else:
            import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class RunConfig:
    task: str = ""
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    effort: str | None = None
    max_turns: int = DEFAULT_MAX_TURNS
    max_tokens: int = DEFAULT_MAX_TOKENS
    only: list[str] = field(default_factory=list)
    approve: bool = False
    show_thinking: bool = False
    fallbacks: bool = True
    report_path: Path | None = None
    server_cmd: list[str] = field(default_factory=list)
    work_dir: Path | None = None
    work_dir_access: bool = False
    history: list[dict[str, str]] = field(default_factory=list)  # earlier turns: {"role": "user"|"assistant", "content": text}
    quiet: bool = False
    on_event: EventSink | None = None
    approval_handler: ApprovalHandler | None = None
    stop_requested: Callable[[], bool] | None = None

    def report_dir(self) -> Path:
        if self.report_path:
            return self.report_path.parent
        if self.work_dir:
            return Path(self.work_dir) / "agent-reports"
        return REPO_ROOT / "outputs" / "agent"


@dataclass
class RunResult:
    final_text: str = ""
    turns: int = 0
    tool_calls: int = 0
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    report_path: Path | None = None


class Transcript:
    """Collects everything that happened so it can be printed live and saved as Markdown."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.entries: list[dict[str, Any]] = []
        self.started = time.time()

    def add(self, kind: str, **data: Any) -> dict[str, Any]:
        entry = {"kind": kind, "t": round(time.time() - self.started, 1), **data}
        self.entries.append(entry)
        if self.cfg.on_event:
            self.cfg.on_event(entry)
        return entry

    def to_markdown(self, result: RunResult) -> str:
        lines = ["# Agent run report", "",
                 f"* **Task:** {self.cfg.task}", f"* **Provider / model:** {self.cfg.provider} / {self.cfg.model}",
                 f"* **Working directory:** {self.cfg.work_dir or '(default outputs folder)'}",
                 f"* **Started:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started))}",
                 f"* **Turns:** {result.turns}  **Tool calls:** {result.tool_calls}  **Stop reason:** {result.stop_reason}",
                 f"* **Usage:** {json.dumps(result.usage)}", "", "## Transcript", ""]
        for e in self.entries:
            k = e["kind"]
            if k == "assistant":
                lines += [f"**Assistant** ({e['t']}s):", "", e["text"], ""]
            elif k == "thinking":
                lines += [f"<details><summary>Thinking ({e['t']}s)</summary>", "", e["text"], "", "</details>", ""]
            elif k == "tool_call":
                lines += [f"**Tool call** `{e['name']}` ({e['t']}s):", "", "```json", json.dumps(e["input"], indent=2)[:4000], "```", ""]
            elif k == "tool_result":
                flag = " (error)" if e.get("is_error") else ""
                lines += [f"**Result** `{e['name']}`{flag}:", "", "```", str(e["content"])[:4000], "```", ""]
            elif k == "note":
                lines += [f"> {e['text']}", ""]
        lines += ["## Final answer", "", result.final_text or "(none)", ""]
        return "\n".join(lines)

    def save(self, result: RunResult) -> Path:
        path = self.cfg.report_path or (self.cfg.report_dir() / f"run_{time.strftime('%Y%m%d_%H%M%S')}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(result), encoding="utf-8")
        return path


def server_params(cmd: list[str] | None = None, work_dir: Path | None = None, work_dir_access: bool = False):
    """StdioServerParameters for the MCP server (default: this interpreter running mcp_adapter.server).

    When a working directory is given, the server's generated scripts and results go there too
    (MCP_ADAPTER_OUTPUT_DIR), so everything a task produces lands in one place. With *work_dir_access* the
    server also registers the workspace_* file/command tools confined to that folder.
    """
    from mcp.client.stdio import StdioServerParameters

    command = list(cmd) if cmd else [sys.executable, "-m", "mcp_adapter.server"]
    env = dict(os.environ)
    env.pop("MCP_ADAPTER_WORK_DIR_ACCESS", None)
    if work_dir:
        env["MCP_ADAPTER_OUTPUT_DIR"] = str(work_dir)
        env["MCP_ADAPTER_WORK_DIR"] = str(work_dir)
        if work_dir_access:
            env["MCP_ADAPTER_WORK_DIR_ACCESS"] = "true"
    else:
        env.pop("MCP_ADAPTER_WORK_DIR", None)
    return StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=str(REPO_ROOT))


def conversation_messages(cfg: RunConfig) -> list[dict[str, str]]:
    """Earlier turns of the chat (text only) followed by the new task."""
    history = [{"role": h["role"], "content": h["content"]} for h in cfg.history
               if h.get("role") in ("user", "assistant") and str(h.get("content", "")).strip()]
    return history + [{"role": "user", "content": cfg.task}]


def select_tools(tools: list[Any], only: list[str] | None) -> list[Any]:
    """Keep core and workspace tools always; restrict automation tools to the given prefixes when *only* is set."""
    if not only:
        return list(tools)
    prefixes = tuple(p.strip().lower().rstrip("_") + "_" for p in only if p.strip())
    keep = []
    for t in tools:
        name = t.name.lower()
        if name.startswith(CORE_TOOL_PREFIXES) or name.startswith("workspace_") or name.startswith(prefixes):
            keep.append(t)
    return keep


def build_system_prompt(cfg: RunConfig) -> str:
    prompt = SYSTEM_PROMPT
    if cfg.work_dir:
        prompt += (f"\n\nWorking directory for this task: {cfg.work_dir}\n"
                   "Save every generated file inside it (use absolute paths built from it) and mention the paths "
                   "in your summary.")
    return prompt


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(json.dumps(block, default=str)[:2000])
        return "\n".join(parts)
    return json.dumps(content, default=str)


def _cli_approval_factory() -> ApprovalHandler:
    state = {"all": False}

    async def ask(name: str, tool_input: Any) -> bool:
        if state["all"]:
            return True
        print(f"\n[approve] {name} {json.dumps(tool_input, default=str)[:600]}")
        try:
            answer = input("[approve] run this tool? [y/N/a=all] ").strip().lower()
        except EOFError:
            answer = ""
        if answer == "a":
            state["all"] = True
            return True
        return answer in ("y", "yes")

    return ask


def _gate(tool, name: str, handler: ApprovalHandler):
    original = tool.call

    async def gated(tool_input):
        if not await handler(name, tool_input):
            return json.dumps({"ok": False, "error": "The user declined to run this tool call."})
        return await original(tool_input)

    tool.call = gated
    return tool


async def list_available_tools(cfg: RunConfig) -> list[dict[str, str]]:
    """Dry run: start the MCP server, list the tools the agent would receive, stop."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params(cfg.server_cmd, cfg.work_dir, cfg.work_dir_access)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            selected = select_tools(tools, cfg.only)
            return [{"name": t.name, "description": (t.description or "").split("\n")[0][:120]} for t in selected]


def _print(cfg: RunConfig, text: str) -> None:
    if not cfg.quiet:
        print(text, flush=True)


async def run_task(cfg: RunConfig) -> RunResult:
    """Run one task to completion with the configured provider (anthropic or openai).

    Raises the provider SDK's errors and ImportError for a missing SDK.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    if cfg.provider not in PROVIDERS:
        raise ValueError(f"unknown provider {cfg.provider!r}; choose one of {PROVIDERS}")
    if cfg.work_dir:
        Path(cfg.work_dir).mkdir(parents=True, exist_ok=True)
    transcript = Transcript(cfg)
    result = RunResult()
    approval = cfg.approval_handler or (_cli_approval_factory() if cfg.approve else None)

    async with stdio_client(server_params(cfg.server_cmd, cfg.work_dir, cfg.work_dir_access)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = select_tools((await session.list_tools()).tools, cfg.only)
            banner = (f"{len(tools)} tools available; provider={cfg.provider}; model={cfg.model}; "
                      f"max_turns={cfg.max_turns}")
            transcript.add("status", text=banner)
            _print(cfg, f"[agent] {banner}")
            if cfg.provider == "openai":
                from .openai_provider import run_openai_loop

                await run_openai_loop(cfg, session, tools, transcript, result, approval)
            else:
                await _run_anthropic(cfg, session, tools, transcript, result, approval)

    result.report_path = transcript.save(result)
    transcript.add("status", text=f"finished: turns={result.turns} tool_calls={result.tool_calls} stop={result.stop_reason}",
                   report=str(result.report_path))
    return result


async def _run_anthropic(cfg: RunConfig, session: Any, tools: list[Any], transcript: Transcript,
                         result: RunResult, approval: ApprovalHandler | None) -> None:
    """Anthropic path: the SDK's tool runner drives the loop; MCP tools are wrapped with async_mcp_tool."""
    import anthropic
    from anthropic.lib.tools.mcp import async_mcp_tool

    client = anthropic.AsyncAnthropic()
    runnable = []
    for t in tools:
        rt = async_mcp_tool(t, session)
        runnable.append(_gate(rt, t.name, approval) if approval else rt)

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "system": build_system_prompt(cfg),
        "messages": conversation_messages(cfg),
        "tools": runnable,
        "max_iterations": cfg.max_turns,
    }
    if cfg.effort:
        kwargs["output_config"] = {"effort": cfg.effort}
    if cfg.show_thinking:
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
    if cfg.fallbacks and cfg.model.startswith(FALLBACK_MODELS):
        # Server-side refusal fallback: a policy decline re-runs the request on a fallback model.
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"

    runner = client.beta.messages.tool_runner(**kwargs)
    pending: dict[str, str] = {}
    async for message in runner:
        result.turns += 1
        result.stop_reason = message.stop_reason or ""
        usage = getattr(message, "usage", None)
        if usage is not None:
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                val = getattr(usage, key, None) or 0
                result.usage[key] = result.usage.get(key, 0) + int(val)
        for block in message.content:
            if block.type == "thinking" and getattr(block, "thinking", ""):
                transcript.add("thinking", text=block.thinking)
                if cfg.show_thinking:
                    _print(cfg, f"[thinking] {block.thinking[:800]}")
            elif block.type == "text" and block.text.strip():
                transcript.add("assistant", text=block.text)
                result.final_text = block.text
                _print(cfg, f"\n[assistant] {block.text}")
            elif block.type == "tool_use":
                result.tool_calls += 1
                pending[block.id] = block.name
                transcript.add("tool_call", name=block.name, input=block.input)
                _print(cfg, f"[tool] {block.name} {json.dumps(block.input, default=str)[:400]}")
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            note = f"The model declined to continue ({getattr(details, 'category', None)}: {getattr(details, 'explanation', '')})."
            transcript.add("note", text=note)
            _print(cfg, f"[agent] {note}")
            break
        response = await runner.generate_tool_call_response()
        if response:
            for item in response.get("content", []):
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    name = pending.pop(item.get("tool_use_id", ""), "?")
                    text = _tool_result_text(item.get("content"))
                    transcript.add("tool_result", name=name, content=text, is_error=bool(item.get("is_error")))
                    _print(cfg, f"[result] {name}: {text[:500]}{'...' if len(text) > 500 else ''}")
        if cfg.stop_requested and cfg.stop_requested():
            transcript.add("note", text="Stopped by the user.")
            _print(cfg, "[agent] stopped by the user")
            break
        if result.turns >= cfg.max_turns:
            transcript.add("note", text=f"Stopped after reaching max_turns={cfg.max_turns}.")
            _print(cfg, f"[agent] stopped: max_turns={cfg.max_turns} reached")
            break


def run(cfg: RunConfig) -> RunResult:
    return asyncio.run(run_task(cfg))
