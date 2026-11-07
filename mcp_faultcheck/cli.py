"""Command line: demo, run, goals, proxy, toy-server, goal-agent.

- ``mcp-faultcheck demo`` — zero configuration: bundled toy server,
  bundled task sets, all five faults, plus the unachievable-goal
  check with the bundled scripted agent; report on stdout.
- ``mcp-faultcheck run --tasks T.jsonl --server "CMD" --config C.json
  [--out report.md] [--json report.json]``
- ``mcp-faultcheck goals --tasks G.jsonl --server "CMD" --agent "CMD"
  [--config C.json] [--fault KIND]`` — hand an agent tasks that
  cannot be completed and score whether it concedes.
- ``mcp-faultcheck proxy [--config C.json] [--trace T.jsonl] -- CMD...``
  — the stdio proxy itself; point your own runner at it.
- ``mcp-faultcheck toy-server`` — run the bundled toy MCP server.
- ``mcp-faultcheck goal-agent`` — run the bundled scripted demo agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .faults import FaultSpec, Trigger
from .goals import load_goal_tasks, run_goal_evaluation
from .harness import load_tasks, run_evaluation, split_server_command
from .report import (
    evaluation_to_dict,
    goals_to_dict,
    render_goals_markdown,
    render_json,
    render_markdown,
)

_DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
TOY_SERVER_CMD = [sys.executable, "-m", "mcp_faultcheck.toy_server"]
GOAL_AGENT_CMD = [sys.executable, "-m", "mcp_faultcheck.goal_agent"]


def _emit(evaluation, out: str | None, json_out: str | None, goals=None) -> None:
    markdown = render_markdown(evaluation)
    if goals is not None:
        markdown += "\n" + render_goals_markdown(goals)
    if out:
        Path(out).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if json_out:
        if goals is not None:
            data = evaluation_to_dict(evaluation)
            data["goals"] = goals_to_dict(goals)
            Path(json_out).write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
        else:
            Path(json_out).write_text(render_json(evaluation), encoding="utf-8")


def _cmd_demo(args: argparse.Namespace) -> int:
    tasks = load_tasks(_DEMO_DIR / "tasks.jsonl")
    config = load_config(_DEMO_DIR / "faults.json")
    evaluation = run_evaluation(
        tasks, TOY_SERVER_CMD, config.faults, config.call_timeout_seconds
    )
    goal_tasks = load_goal_tasks(_DEMO_DIR / "goals.jsonl")
    goals = run_goal_evaluation(
        goal_tasks,
        TOY_SERVER_CMD,
        GOAL_AGENT_CMD,
        config.faults,
        config.call_timeout_seconds,
    )
    _emit(evaluation, args.out, args.json, goals=goals)
    return 0


def _cmd_goals(args: argparse.Namespace) -> int:
    tasks = load_goal_tasks(args.tasks)
    faults = []
    call_timeout = 1.0
    if args.config:
        config = load_config(args.config)
        faults = config.faults
        call_timeout = config.call_timeout_seconds
    if args.fault:
        faults = [FaultSpec(kind=args.fault, trigger=Trigger(every=1))]
    if args.call_timeout is not None:
        call_timeout = args.call_timeout
    evaluation = run_goal_evaluation(
        tasks,
        split_server_command(args.server),
        split_server_command(args.agent),
        faults,
        call_timeout,
    )
    markdown = render_goals_markdown(evaluation)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if args.json:
        Path(args.json).write_text(
            json.dumps(goals_to_dict(evaluation), indent=2) + "\n",
            encoding="utf-8",
        )
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
    if argv and argv[0] == "goal-agent":
        from .goal_agent import main as agent_main

        return agent_main()

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

    goals = sub.add_parser(
        "goals", help="run the unachievable-goal check against an agent"
    )
    goals.add_argument("--tasks", required=True, help="goal task set JSONL")
    goals.add_argument("--server", required=True, help="MCP server command (stdio)")
    goals.add_argument(
        "--agent",
        required=True,
        help="agent command speaking the agent/run protocol (see goals module)",
    )
    goals.add_argument(
        "--config",
        help="fault config JSON: supplies the scenarios and call_timeout_seconds",
    )
    goals.add_argument(
        "--fault",
        help="run baseline + this one fault kind instead of the config's faults",
    )
    goals.add_argument(
        "--call-timeout",
        type=float,
        help="per-message wait in seconds (overrides the config value)",
    )
    goals.add_argument("--out", help="write the markdown report here (default: stdout)")
    goals.add_argument("--json", help="also write the JSON report here")
    goals.set_defaults(func=_cmd_goals)

    sub.add_parser("proxy", help="run the stdio proxy (see --help via proxy usage)")
    sub.add_parser("toy-server", help="run the bundled toy MCP server")
    sub.add_parser("goal-agent", help="run the bundled scripted demo agent")

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
