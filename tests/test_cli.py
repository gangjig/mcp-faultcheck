import json
from pathlib import Path

from mcp_faultcheck.cli import main

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


def test_demo_command_reports_baseline_and_faults(capsys):
    rc = main(["demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "baseline (no faults) | 6/6 (100%)" in out
    for kind in ("timeout", "empty", "malformed", "error", "garbage-schema"):
        assert f"fault: {kind}" in out


def test_run_command_writes_markdown_and_json(tmp_path):
    out_md = tmp_path / "report.md"
    out_json = tmp_path / "report.json"
    rc = main(
        [
            "run",
            "--tasks",
            str(DEMO_DIR / "tasks.jsonl"),
            "--server",
            "python3 -m mcp_faultcheck.toy_server",
            "--config",
            str(DEMO_DIR / "faults.json"),
            "--out",
            str(out_md),
            "--json",
            str(out_json),
        ]
    )
    assert rc == 0
    assert "mcp-faultcheck report" in out_md.read_text(encoding="utf-8")
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["baseline"]["passed"] == 6
    assert len(data["scenarios"]) == 5


def test_demo_json_file(capsys, tmp_path):
    out_json = tmp_path / "demo.json"
    rc = main(["demo", "--json", str(out_json)])
    assert rc == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["baseline"]["success_rate"] == 1.0
    timeout = next(s for s in data["scenarios"] if s["fault"] == "timeout")
    assert timeout["failure_modes"] == {"timeout-abandoned": 6}
