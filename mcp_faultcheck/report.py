"""Report rendering: markdown for humans, JSON for machines.

The headline table is one row per scenario: success rate, the drop
versus baseline (Δ, in percentage points), average steps, and the
most common failure mode classified from the run.
"""

from __future__ import annotations

import json
from collections import Counter

from .harness import Evaluation, ScenarioResult


def _failure_modes(scenario: ScenarioResult) -> Counter:
    return Counter(
        r.failure_mode for r in scenario.results if not r.success and r.failure_mode
    )


def _top_failure(scenario: ScenarioResult) -> str:
    modes = _failure_modes(scenario)
    if not modes:
        return "—"
    mode, count = modes.most_common(1)[0]
    return f"{mode} (x{count})"


def _pct(rate: float) -> str:
    return f"{rate * 100:.0f}%"


def _delta_points(scenario: ScenarioResult, baseline: ScenarioResult) -> float:
    return (scenario.success_rate - baseline.success_rate) * 100


def render_markdown(evaluation: Evaluation) -> str:
    baseline = evaluation.baseline
    lines = [
        "# mcp-faultcheck report",
        "",
        f"Server: `{' '.join(evaluation.server_cmd)}`  ",
        f"Tasks: {baseline.total} · runner call timeout: "
        f"{evaluation.call_timeout_seconds:g}s · runner: scripted (no LLM)",
        "",
        "| Scenario | Success | Δ vs baseline | Avg steps | Most common failure |",
        "|---|---|---|---|---|",
        f"| baseline (no faults) | {baseline.passed}/{baseline.total} "
        f"({_pct(baseline.success_rate)}) | — | {baseline.avg_steps:.1f} | "
        f"{_top_failure(baseline)} |",
    ]
    for scenario in evaluation.scenarios:
        delta = _delta_points(scenario, baseline)
        lines.append(
            f"| fault: {scenario.fault} | {scenario.passed}/{scenario.total} "
            f"({_pct(scenario.success_rate)}) | {delta:+.0f} pts | "
            f"{scenario.avg_steps:.1f} | {_top_failure(scenario)} |"
        )
    lines += [
        "",
        "Δ is the change in task success rate versus the no-fault baseline, "
        "in percentage points. Failure modes are classified from how the "
        "runner died: timeout-abandoned, parse-crash, unhandled-error, "
        "tool-error, empty-result-misread, schema-mismatch, "
        "assertion-mismatch.",
        "",
    ]
    return "\n".join(lines)


def _scenario_dict(scenario: ScenarioResult, baseline: ScenarioResult) -> dict:
    return {
        "name": scenario.name,
        "fault": scenario.fault,
        "total": scenario.total,
        "passed": scenario.passed,
        "success_rate": round(scenario.success_rate, 4),
        "delta_points": round(_delta_points(scenario, baseline), 1),
        "avg_steps": round(scenario.avg_steps, 2),
        "failure_modes": dict(_failure_modes(scenario)),
        "tasks": [
            {
                "id": r.task_id,
                "success": r.success,
                "steps": r.steps_used,
                "failure_mode": r.failure_mode,
                "detail": r.detail,
            }
            for r in scenario.results
        ],
    }


def evaluation_to_dict(evaluation: Evaluation) -> dict:
    return {
        "server": evaluation.server_cmd,
        "call_timeout_seconds": evaluation.call_timeout_seconds,
        "baseline": _scenario_dict(evaluation.baseline, evaluation.baseline),
        "scenarios": [
            _scenario_dict(s, evaluation.baseline) for s in evaluation.scenarios
        ],
    }


def render_json(evaluation: Evaluation) -> str:
    return json.dumps(evaluation_to_dict(evaluation), indent=2) + "\n"
