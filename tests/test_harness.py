from pathlib import Path

import pytest

from helpers import TOY_SERVER
from mcp_faultcheck.config import load_config
from mcp_faultcheck.harness import (
    MODE_EMPTY,
    MODE_ERROR,
    MODE_PARSE,
    MODE_SCHEMA,
    MODE_TIMEOUT,
    load_tasks,
    run_evaluation,
    run_scenario,
)

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


@pytest.fixture(scope="module")
def demo_tasks():
    return load_tasks(DEMO_DIR / "tasks.jsonl")


@pytest.fixture(scope="module")
def demo_config():
    return load_config(DEMO_DIR / "faults.json")


def test_load_demo_tasks(demo_tasks):
    assert len(demo_tasks) == 6
    assert demo_tasks[0].id == "read-notes"
    assert demo_tasks[4].id == "read-then-count"
    assert len(demo_tasks[4].steps) == 2


def test_load_tasks_rejects_bad_json(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("{nope\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tasks(path)


def test_load_tasks_rejects_missing_steps(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_tasks(path)


def test_baseline_all_tasks_pass(demo_tasks):
    scenario = run_scenario(demo_tasks, TOY_SERVER, None, call_timeout=5.0)
    assert scenario.passed == 6
    assert scenario.success_rate == 1.0
    assert scenario.fault is None


@pytest.mark.parametrize(
    "kind, mode",
    [
        ("timeout", MODE_TIMEOUT),
        ("empty", MODE_EMPTY),
        ("malformed", MODE_PARSE),
        ("error", MODE_ERROR),
        ("garbage-schema", MODE_SCHEMA),
    ],
)
def test_each_fault_zeroes_the_demo(demo_tasks, demo_config, kind, mode):
    fault = next(f for f in demo_config.faults if f.kind == kind)
    scenario = run_scenario(demo_tasks, TOY_SERVER, fault, demo_config.call_timeout_seconds)
    assert scenario.passed == 0, [r.detail for r in scenario.results]
    assert all(r.failure_mode == mode for r in scenario.results)


def test_full_evaluation_shape(demo_tasks, demo_config):
    evaluation = run_evaluation(
        demo_tasks,
        TOY_SERVER,
        demo_config.faults,
        demo_config.call_timeout_seconds,
    )
    assert evaluation.baseline.success_rate == 1.0
    assert [s.fault for s in evaluation.scenarios] == [
        "timeout",
        "empty",
        "malformed",
        "error",
        "garbage-schema",
    ]
    assert all(s.success_rate == 0.0 for s in evaluation.scenarios)
