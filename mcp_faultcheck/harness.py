"""Task harness: run a fixed task set against a server, via the proxy.

The runner is deliberately a *scripted* placeholder — no LLM. Each
task is a fixed sequence of tools/call steps plus assertions on the
results. It models an agent with no recovery smarts: a timed-out,
unparseable, or errored response ends the task. That makes the numbers
a floor — a smarter agent may do better; the report shows how much
room the tool layer leaves it.

Each scenario (baseline + one per configured fault) runs in a fresh
proxy + server process pair, so call counting starts at zero and
deterministic triggers line up across scenarios.
"""

from __future__ import annotations

import json
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import spec_to_dict
from .faults import FaultSpec

# Failure modes, classified from the trace of outcomes (report vocabulary).
MODE_TIMEOUT = "timeout-abandoned"  # runner gave up waiting
MODE_PARSE = "parse-crash"  # response was not valid JSON
MODE_ERROR = "unhandled-error"  # JSON-RPC error reached the runner
MODE_TOOL_ERROR = "tool-error"  # tool itself reported an error result
MODE_EMPTY = "empty-result-misread"  # empty content taken at face value
MODE_SCHEMA = "schema-mismatch"  # fields present but wrongly typed
MODE_ASSERT = "assertion-mismatch"  # anything else

_FAULT_TO_MODE = {
    "timeout": MODE_TIMEOUT,
    "malformed": MODE_PARSE,
    "error": MODE_ERROR,
    "empty": MODE_EMPTY,
    "garbage-schema": MODE_SCHEMA,
}


@dataclass
class Task:
    id: str
    instruction: str
    steps: list[dict]
    assertions: list[dict] = field(default_factory=list)


@dataclass
class TaskResult:
    task_id: str
    success: bool
    steps_used: int
    failure_mode: Optional[str] = None
    detail: str = ""


@dataclass
class ScenarioResult:
    name: str
    fault: Optional[str]
    results: list[TaskResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def success_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_steps(self) -> float:
        return sum(r.steps_used for r in self.results) / self.total if self.total else 0.0


@dataclass
class Evaluation:
    server_cmd: list[str]
    call_timeout_seconds: float
    baseline: ScenarioResult
    scenarios: list[ScenarioResult]


def load_tasks(path: str | Path) -> list[Task]:
    tasks = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if "id" not in data or "steps" not in data:
            raise ValueError(f"{path}:{lineno}: task needs 'id' and 'steps'")
        tasks.append(
            Task(
                id=data["id"],
                instruction=data.get("instruction", ""),
                steps=data["steps"],
                assertions=data.get("assertions", []),
            )
        )
    if not tasks:
        raise ValueError(f"{path}: no tasks found")
    return tasks


class Client:
    """Minimal JSON-RPC client with a per-request deadline."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._queue: queue.Queue = queue.Queue()
        self._next_id = 0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def request(self, method: str, params: dict, timeout: float) -> tuple[str, object]:
        """Returns (outcome, payload): outcome is one of
        "ok" (parsed message), "timeout", "parse_error" (raw line),
        "closed" (stream ended)."""
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        try:
            line = self._queue.get(timeout=timeout)
        except queue.Empty:
            return "timeout", None
        if line is None:
            return "closed", None
        try:
            return "ok", json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return "parse_error", line

    def close(self, force: bool = False) -> None:
        if force:
            # The session is done and its result is already decided; do
            # not wait out a fault-delayed proxy that may be mid-sleep.
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _spawn_proxy(server_cmd: list[str], fault: Optional[FaultSpec], call_timeout: float):
    """Spawn proxy + server as one subprocess; returns (proc, tmpdir handle)."""
    config = {"call_timeout_seconds": call_timeout, "faults": []}
    if fault is not None:
        config["faults"] = [spec_to_dict(fault)]
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="mcp-faultcheck-", delete=False
    )
    json.dump(config, tmp)
    tmp.close()
    cmd = [
        sys.executable,
        "-m",
        "mcp_faultcheck",
        "proxy",
        "--config",
        tmp.name,
        "--",
        *server_cmd,
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
    )
    return proc, tmp.name


def _json_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "unknown"


def _check_assertions(
    task: Task, outcomes: list[dict], tools_used: list[str]
) -> Optional[str]:
    """Returns a failure detail string, or None when all assertions pass."""
    all_text = "\n".join(o["text"] for o in outcomes)
    for assertion in task.assertions:
        kind = assertion.get("type")
        if kind == "result_contains":
            if assertion["value"] not in all_text:
                return f"result does not contain {assertion['value']!r}"
        elif kind == "tool_called":
            if assertion["tool"] not in tools_used:
                return f"tool {assertion['tool']!r} was never called"
        elif kind == "field_type":
            step_idx = assertion.get("step", len(outcomes) - 1)
            structured = outcomes[step_idx]["structured"] if outcomes else {}
            field_name = assertion["field"]
            if field_name not in structured:
                return f"structured field {field_name!r} missing"
            actual = _json_type(structured[field_name])
            if actual != assertion["field_type"]:
                return (
                    f"field {field_name!r} is {actual}, "
                    f"expected {assertion['field_type']}"
                )
        elif kind == "max_steps":
            if len(outcomes) > assertion["value"]:
                return f"used {len(outcomes)} steps, max {assertion['value']}"
        else:
            return f"unknown assertion type: {kind!r}"
    return None


def run_task(
    task: Task, server_cmd: list[str], fault: Optional[FaultSpec], call_timeout: float
) -> TaskResult:
    proc, config_path = _spawn_proxy(server_cmd, fault, call_timeout)
    client = Client(proc)
    steps_used = 0
    try:
        outcome, _ = client.request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mcp-faultcheck", "version": "0.1.0"}},
            timeout=call_timeout,
        )
        if outcome != "ok":
            return TaskResult(task.id, False, 0, MODE_PARSE if outcome == "parse_error" else MODE_TIMEOUT,
                              f"initialize handshake failed: {outcome}")
        outcomes: list[dict] = []
        tools_used: list[str] = []
        for step in task.steps:
            steps_used += 1
            outcome, payload = client.request(
                "tools/call",
                {"name": step["tool"], "arguments": step.get("arguments", {})},
                timeout=call_timeout,
            )
            if outcome == "timeout":
                return TaskResult(task.id, False, steps_used, MODE_TIMEOUT,
                                  "runner deadline exceeded waiting for tools/call response")
            if outcome == "parse_error":
                return TaskResult(task.id, False, steps_used, MODE_PARSE,
                                  "response line was not valid JSON")
            if outcome == "closed":
                return TaskResult(task.id, False, steps_used, MODE_PARSE,
                                  "response stream closed mid-task")
            message = payload  # type: ignore[assignment]
            if "error" in message:
                return TaskResult(task.id, False, steps_used, MODE_ERROR,
                                  f"JSON-RPC error: {message['error'].get('message')}")
            result = message.get("result") or {}
            # `is True`, not truthiness: under garbage-schema the flag
            # itself arrives flipped to the string "false".
            if result.get("isError") is True:
                return TaskResult(task.id, False, steps_used, MODE_TOOL_ERROR,
                                  "tool returned an error result")
            text = "\n".join(
                item.get("text", "")
                for item in result.get("content", [])
                if item.get("type") == "text" and isinstance(item.get("text"), str)
            )
            outcomes.append(
                {"text": text, "structured": result.get("structuredContent") or {}}
            )
            tools_used.append(step["tool"])
        detail = _check_assertions(task, outcomes, tools_used)
        if detail is not None:
            mode = _FAULT_TO_MODE.get(fault.kind, MODE_ASSERT) if fault else MODE_ASSERT
            return TaskResult(task.id, False, steps_used, mode, detail)
        return TaskResult(task.id, True, steps_used)
    finally:
        client.close(force=True)
        Path(config_path).unlink(missing_ok=True)


def run_scenario(
    tasks: list[Task],
    server_cmd: list[str],
    fault: Optional[FaultSpec],
    call_timeout: float,
) -> ScenarioResult:
    results = [run_task(task, server_cmd, fault, call_timeout) for task in tasks]
    name = f"fault: {fault.kind}" if fault else "baseline (no faults)"
    return ScenarioResult(name=name, fault=fault.kind if fault else None, results=results)


def run_evaluation(
    tasks: list[Task],
    server_cmd: list[str],
    faults: list[FaultSpec],
    call_timeout: float = 1.0,
) -> Evaluation:
    baseline = run_scenario(tasks, server_cmd, None, call_timeout)
    scenarios = [run_scenario(tasks, server_cmd, fault, call_timeout) for fault in faults]
    return Evaluation(
        server_cmd=list(server_cmd),
        call_timeout_seconds=call_timeout,
        baseline=baseline,
        scenarios=scenarios,
    )


def split_server_command(command: str) -> list[str]:
    return shlex.split(command)
