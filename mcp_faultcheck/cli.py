"""Command line: demo, run, proxy, toy-server.

- ``mcp-faultcheck demo`` — zero configuration: bundled toy server,
  bundled 6-task set, all five faults, report on stdout.
- ``mcp-faultcheck run --tasks T.jsonl --server "CMD" --config C.json
  [--out report.md] [--json report.json]``
- ``mcp-faultcheck proxy [--config C.json] [--trace T.jsonl] -- CMD...``
  — the stdio proxy itself; point your own runner at it.
- ``mcp-faultcheck toy-server`` — run the bundled toy MCP server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .harness import load_tasks, run_evaluation, split_server_command
from .report import render_json, render_markdown

_DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
TOY_SERVER_CMD = [sys.executable, "-m", "mcp_faultcheck.toy_server"]


def _emit(evaluation, out: str | None, json_out: str | None) -> None:
    markdown = render_markdown(evaluation)
    if out:
        Path(out).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if json_out:
        Path(json_out).write_text(render_json(evaluation), encoding="utf-8")


def _cmd_demo(args: argparse.Namespace) -> int:
    tasks = load_tasks(_DEMO_DIR / "tasks.jsonl")
    config = load_config(_DEMO_DIR / "faults.json")
    evaluation = run_evaluation(
        tasks, TOY_SERVER_CMD, config.faults, config.call_timeout_seconds
    )
    _emit(evaluation, args.out, args.json)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks)
    config = load_config(args.config)
    evaluation = run_evaluation(
        tasks,
        split_server_command(args.server),
        config.faults,
        config.call_timeout_seconds,
    )
    _emit(evaluation, args.out, args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "proxy":
        from .proxy import run_proxy_cli

        return run_proxy_cli(argv[1:])
    if argv and argv[0] == "toy-server":
        from .toy_server import main as toy_main

        return toy_main()

    parser = argparse.ArgumentParser(
        prog="mcp-faultcheck",
        description="Fault-injection harness for MCP servers (stdio).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the bundled zero-config demo")
    demo.add_argument("--out", help="write the markdown report here (default: stdout)")
    demo.add_argument("--json", help="also write the JSON report here")
    demo.set_defaults(func=_cmd_demo)

    run = sub.add_parser("run", help="evaluate your own server and task set")
    run.add_argument("--tasks", required=True, help="task set JSONL")
    run.add_argument("--server", required=True, help="MCP server command (stdio)")
    run.add_argument("--config", required=True, help="fault config JSON")
    run.add_argument("--out", help="write the markdown report here (default: stdout)")
    run.add_argument("--json", help="also write the JSON report here")
    run.set_defaults(func=_cmd_run)

    sub.add_parser("proxy", help="run the stdio proxy (see --help via proxy usage)")
    sub.add_parser("toy-server", help="run the bundled toy MCP server")

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
