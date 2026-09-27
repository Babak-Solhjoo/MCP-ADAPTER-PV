"""Command line for the agent runner: ``python -m agent_runner "task"`` or ``mcp-adapter-agent``."""
from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import sys
from pathlib import Path

from . import __version__
from .openai_provider import DEFAULT_OPENAI_MODEL
from .runner import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
    DEFAULT_MODEL,
    PROVIDER_KEY_ENV,
    RunConfig,
    list_available_tools,
    provider_key_present,
    provider_sdk_available,
    run,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-adapter-agent",
        description="Give a task to an AI agent (Anthropic or OpenAI model) that works through the local MCP-ADAPTER tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python -m agent_runner --dry-run
  python -m agent_runner "List installed tools, then build a draw.io flowchart of a design review and export PNG"
  python -m agent_runner --provider openai --model gpt-5 "..."
  python -m agent_runner --task-file task.md --approve --only drawio,matlab --work-dir D:\\work\\task1
""",
    )
    p.add_argument("task", nargs="?", help="the task or problem statement")
    p.add_argument("--task-file", help="read the task from a text/markdown file instead")
    p.add_argument("--provider", choices=["anthropic", "openai"], default=os.environ.get("AGENT_PROVIDER", "anthropic"),
                   help="which API to use for the model (default from AGENT_PROVIDER, else anthropic)")
    p.add_argument("--model", default=None, help="model id (default: claude-opus-5 for anthropic, gpt-5 for openai)")
    p.add_argument("--base-url", default=None, help="OpenAI-compatible endpoint (default OPENAI_BASE_URL)")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="safety cap on model turns")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--only", default="", help="comma-separated tool prefixes to allow, e.g. drawio,matlab")
    p.add_argument("--approve", action="store_true", help="ask before every tool call (y/n/a)")
    p.add_argument("--show-thinking", action="store_true", help="print the model's reasoning summaries (anthropic)")
    p.add_argument("--no-fallbacks", action="store_true", help="disable server-side refusal fallbacks (anthropic)")
    p.add_argument("--report", help="where to write the Markdown report (default <work dir>/agent-reports or outputs/agent)")
    p.add_argument("--work-dir", help="working directory for the task (generated files and the report go there)")
    p.add_argument("--server-cmd", help="command that starts the MCP server (default: this Python -m mcp_adapter.server)")
    p.add_argument("--dry-run", action="store_true", help="only list the tools the agent would receive")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"mcp-adapter-agent {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = args.task or ""
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8")
    env_model = os.environ.get("AGENT_OPENAI_MODEL") if args.provider == "openai" else os.environ.get("AGENT_MODEL")
    default_model = DEFAULT_OPENAI_MODEL if args.provider == "openai" else DEFAULT_MODEL
    work_dir = args.work_dir or os.environ.get("MCP_ADAPTER_WORK_DIR") or ""
    cfg = RunConfig(
        task=task.strip(), provider=args.provider, model=args.model or env_model or default_model,
        base_url=args.base_url or os.environ.get("OPENAI_BASE_URL") or None,
        effort=args.effort, max_turns=args.max_turns,
        max_tokens=args.max_tokens, only=[s for s in args.only.split(",") if s.strip()],
        approve=args.approve, show_thinking=args.show_thinking, fallbacks=not args.no_fallbacks,
        report_path=Path(args.report) if args.report else None,
        server_cmd=shlex.split(args.server_cmd) if args.server_cmd else [], quiet=args.quiet,
        work_dir=Path(work_dir) if work_dir else None,
    )
    if args.dry_run:
        tools = asyncio.run(list_available_tools(cfg))
        print(f"{len(tools)} tools would be given to the agent ({cfg.provider} / {cfg.model}):")
        for t in tools:
            print(f"  {t['name']:36} {t['description']}")
        return 0
    if not cfg.task:
        print("error: give a task as an argument or with --task-file (or use --dry-run)", file=sys.stderr)
        return 2
    if not provider_sdk_available(cfg.provider):
        print(f'error: the {cfg.provider} SDK is not installed. Run: python -m pip install -e ".[agent]"', file=sys.stderr)
        return 2
    if not provider_key_present(cfg.provider):
        keys = " or ".join(PROVIDER_KEY_ENV[cfg.provider])
        if cfg.provider == "openai":
            print(f"error: {keys} is not set. Put it in .env (or the environment) and retry.", file=sys.stderr)
            return 2
        print(f"note: {keys} is not set; the SDK will try other credential sources (an `ant auth login` profile). "
              "Put ANTHROPIC_API_KEY=... in .env if the run fails with an authentication error.", file=sys.stderr)
    try:
        result = run(cfg)
    except KeyboardInterrupt:
        print("\n[agent] interrupted", file=sys.stderr)
        return 130
    print(f"\n[agent] done: turns={result.turns} tool_calls={result.tool_calls} stop={result.stop_reason} "
          f"usage={result.usage}\n[agent] report: {result.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
