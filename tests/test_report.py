import json

from mcp_faultcheck.harness import Evaluation, ScenarioResult, TaskResult
from mcp_faultcheck.report import evaluation_to_dict, render_json, render_markdown


def _evaluation():
    baseline = ScenarioResult(
        name="baseline (no faults)",
        fault=None,
        results=[TaskResult(f"t{i}", True, 1) for i in range(6)],
    )
    error_scenario = ScenarioResult(
        name="fault: error",
        fault="error",
        results=[
            TaskResult(f"t{i}", False, 1, "unhandled-error", "boom") for i in range(6)
        ],
    )
    partial = ScenarioResult(
        name="fault: empty",
        fault="empty",
        results=[TaskResult(f"t{i}", True, 1) for i in range(3)]
        + [TaskResult(f"t{i}", False, 1, "empty-result-misread", "empty") for i in range(3, 6)],
    )
    return Evaluation(
        server_cmd=["python", "-m", "server"],
        call_timeout_seconds=1.0,
        baseline=baseline,
        scenarios=[error_scenario, partial],
    )


def test_markdown_headline_rows():
    md = render_markdown(_evaluation())
    assert "baseline (no faults) | 6/6 (100%)" in md
    assert "fault: error | 0/6 (0%) | -100 pts" in md
    assert "fault: empty | 3/6 (50%) | -50 pts" in md
    assert "unhandled-error (x6)" in md


def test_markdown_clean_scenario_has_no_failure_mode():
    md = render_markdown(_evaluation())
    baseline_row = next(l for l in md.splitlines() if l.startswith("| baseline"))
    assert baseline_row.rstrip().endswith("| — |")


def test_json_structure_and_deltas():
    data = json.loads(render_json(_evaluation()))
    assert data["baseline"]["passed"] == 6
    assert data["baseline"]["delta_points"] == 0.0
    error = data["scenarios"][0]
    assert error["fault"] == "error"
    assert error["success_rate"] == 0.0
    assert error["delta_points"] == -100.0
    assert error["failure_modes"] == {"unhandled-error": 6}
    assert error["tasks"][0]["failure_mode"] == "unhandled-error"


def test_evaluation_to_dict_avg_steps():
    data = evaluation_to_dict(_evaluation())
    assert data["scenarios"][1]["avg_steps"] == 1.0
    assert data["call_timeout_seconds"] == 1.0
    assert data["server"] == ["python", "-m", "server"]
