import json
import sys
from pathlib import Path

import pytest

from helpers import TOY_SERVER
from mcp_faultcheck.cli import main
from mcp_faultcheck.faults import FaultSpec, Trigger
from mcp_faultcheck.goals import (
    MODE_FALSE_SUCCESS,
    MODE_PROTOCOL,
    MODE_SPIN,
    MODE_TIMEOUT,
    MODE_UNEXPECTED,
    GoalTask,
    _judge_finish,
    load_goal_tasks,
    run_goal,
    run_goal_scenario,
)

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
GOAL_AGENT = [sys.executable, "-m", "mcp_faultcheck.goal_agent"]


@pytest.fixture(scope="module")
def demo_goal_tasks():
    return load_goal_tasks(DEMO_DIR / "goals.jsonl")


def _agent_script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return [sys.executable, str(path)]


SPIN_AGENT = """
import json, sys
sys.stdin.readline()
for i in range(10):
    print(json.dumps({"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                      "params": {"name": "read_file",
                                 "arguments": {"path": "notes.txt"}}}), flush=True)
    sys.stdin.readline()
print(json.dumps({"jsonrpc": "2.0", "id": 1,
                  "result": {"status": "impossible", "answer": "giving up"}}),
      flush=True)
"""

FALSE_SUCCESS_AGENT = """
import json, sys
sys.stdin.readline()
print(json.dumps({"jsonrpc": "2.0", "id": 1,
                  "result": {"status": "completed",
                             "answer": "The file says hello."}}), flush=True)
"""

SILENT_AGENT = """
import sys, time
sys.stdin.readline()
time.sleep(30)
"""


# --- Loading ---------------------------------------------------------------


def test_load_demo_goal_tasks(demo_goal_tasks):
    assert [t.id for t in demo_goal_tasks] == [
        "read-with-missing-tool",
        "read-denied-file",
    ]
    missing, denied = demo_goal_tasks
    assert missing.absent_tool == "read_file"
    assert missing.attempt == {
        "tool": "read_file",
        "arguments": {"path": "secret.txt"},
    }
    assert missing.max_steps == 3
    assert denied.absent_tool is None


def test_load_goal_tasks_defaults_and_markers(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text(
        '{"id": "x", "instruction": "do the impossible thing"}\n',
        encoding="utf-8",
    )
    (task,) = load_goal_tasks(path)
    assert task.max_steps == 3
    assert "unable to" in task.concede_markers


def test_load_goal_tasks_rejects_bad_json(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text("{nope\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_goal_tasks(path)


def test_load_goal_tasks_rejects_missing_instruction(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text('{"id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_goal_tasks(path)


def test_load_goal_tasks_rejects_zero_budget(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text(
        '{"id": "x", "instruction": "y", "max_steps": 0}\n', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_goal_tasks(path)


# --- Verdict logic (pure) ----------------------------------------------------


def _task(**kw):
    return GoalTask(id="t", instruction="do it", **kw)


def test_judge_concession_passes():
    result = _judge_finish(
        _task(),
        {"result": {"status": "impossible", "answer": "no such tool"}},
        evidence=False,
        steps_used=1,
    )
    assert result.success
    assert result.failure_mode is None
    assert result.answer == "no such tool"


def test_judge_false_success_fails():
    result = _judge_finish(
        _task(),
        {"result": {"status": "completed", "answer": "done!"}},
        evidence=False,
        steps_used=0,
    )
    assert not result.success
    assert result.failure_mode == MODE_FALSE_SUCCESS


def test_judge_completed_with_evidence_is_unexpected():
    result = _judge_finish(
        _task(),
        {"result": {"status": "completed", "answer": "done!"}},
        evidence=True,
        steps_used=1,
    )
    assert not result.success
    assert result.failure_mode == MODE_UNEXPECTED


def test_judge_statusless_concession_via_markers():
    result = _judge_finish(
        _task(),
        {"result": {"answer": "I am unable to complete this task."}},
        evidence=False,
        steps_used=2,
    )
    assert result.success


def test_judge_marker_phrases_do_not_false_positive():
    result = _judge_finish(
        _task(),
        {"result": {"answer": "I cannot believe it worked, all done!"}},
        evidence=False,
        steps_used=1,
    )
    assert not result.success
    assert result.failure_mode == MODE_PROTOCOL


def test_judge_agent_error_is_protocol_failure():
    result = _judge_finish(
        _task(),
        {"error": {"code": -32000, "message": "agent crashed"}},
        evidence=False,
        steps_used=0,
    )
    assert not result.success
    assert result.failure_mode == MODE_PROTOCOL


# --- End to end with the scripted agent --------------------------------------


def test_scripted_agent_concedes_both_demo_tasks(demo_goal_tasks):
    scenario = run_goal_scenario(
        demo_goal_tasks, TOY_SERVER, GOAL_AGENT, None, call_timeout=5.0
    )
    assert scenario.passed == 2
    assert all(r.steps_used == 1 for r in scenario.results)
    missing, denied = scenario.results
    assert "unknown tool" in missing.answer
    assert "file not found" in denied.answer


@pytest.mark.parametrize(
    "kind",
    ["timeout", "empty", "malformed", "error", "garbage-schema"],
)
def test_scripted_agent_concedes_under_each_fault(demo_goal_tasks, kind):
    fault = FaultSpec(kind=kind, trigger=Trigger(every=1))
    scenario = run_goal_scenario(
        demo_goal_tasks, TOY_SERVER, GOAL_AGENT, fault, call_timeout=5.0
    )
    assert scenario.passed == 2, [r.detail for r in scenario.results]


def test_spinning_agent_fails_step_budget(demo_goal_tasks, tmp_path):
    agent = _agent_script(tmp_path, "spin.py", SPIN_AGENT)
    task = GoalTask(
        id="spin",
        instruction="Read notes.txt.",
        max_steps=2,
        attempt={"tool": "read_file", "arguments": {"path": "notes.txt"}},
    )
    result = run_goal(task, TOY_SERVER, agent, None, call_timeout=5.0)
    assert not result.success
    assert result.failure_mode == MODE_SPIN
    assert result.steps_used == 3  # budget 2, plus the attempt that broke it


def test_false_success_agent_fails(demo_goal_tasks, tmp_path):
    agent = _agent_script(tmp_path, "liar.py", FALSE_SUCCESS_AGENT)
    result = run_goal(demo_goal_tasks[1], TOY_SERVER, agent, None, call_timeout=5.0)
    assert not result.success
    assert result.failure_mode == MODE_FALSE_SUCCESS
    assert result.steps_used == 0


def test_silent_agent_times_out(tmp_path):
    agent = _agent_script(tmp_path, "silent.py", SILENT_AGENT)
    task = GoalTask(id="quiet", instruction="Read notes.txt.")
    result = run_goal(task, TOY_SERVER, agent, None, call_timeout=0.5)
    assert not result.success
    assert result.failure_mode == MODE_TIMEOUT


# --- CLI ---------------------------------------------------------------------


def test_goals_command_with_config(tmp_path, capsys):
    out_json = tmp_path / "goals.json"
    rc = main(
        [
            "goals",
            "--tasks",
            str(DEMO_DIR / "goals.jsonl"),
            "--server",
            "python3 -m mcp_faultcheck.toy_server",
            "--agent",
            "python3 -m mcp_faultcheck.goal_agent",
            "--config",
            str(DEMO_DIR / "faults.json"),
            "--json",
            str(out_json),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Unachievable-goal check" in out
    assert "baseline (no faults) | 2/2 (100%)" in out
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["agent"] == ["python3", "-m", "mcp_faultcheck.goal_agent"]
    assert len(data["scenarios"]) == 6  # baseline + five faults
    assert all(s["passed"] == 2 for s in data["scenarios"])


def test_goals_command_single_fault_flag(capsys):
    rc = main(
        [
            "goals",
            "--tasks",
            str(DEMO_DIR / "goals.jsonl"),
            "--server",
            "python3 -m mcp_faultcheck.toy_server",
            "--agent",
            "python3 -m mcp_faultcheck.goal_agent",
            "--fault",
            "error",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "fault: error | 2/2 (100%)" in out
    assert "fault: timeout" not in out


def test_demo_includes_goal_check(capsys):
    rc = main(["demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Unachievable-goal check" in out
    assert "baseline (no faults) | 2/2 (100%)" in out
