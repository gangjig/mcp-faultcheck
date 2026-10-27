import json
import time

import pytest

from helpers import call, initialize, proxy_process
from mcp_faultcheck.harness import Client

READ_NOTES = {"name": "read_file", "arguments": {"path": "notes.txt"}}


def run_client(tmp_path, config, trace_name=None):
    proc = proxy_process(tmp_path, config, trace_name=trace_name)
    client = Client(proc)
    initialize(client)
    return proc, client


def test_passthrough_matches_direct_result(tmp_path):
    _, client = run_client(tmp_path, {"faults": []})
    msg = call(client, "tools/call", READ_NOTES)
    client.close()
    assert "quick brown fox" in msg["result"]["content"][0]["text"]
    assert msg["result"]["structuredContent"]["lines"] == 3


def test_non_tool_responses_untouched_under_all_faults(tmp_path):
    config = {
        "faults": [
            {"kind": "empty", "trigger": {"every": 1}},
            {"kind": "garbage-schema", "trigger": {"every": 1}},
        ]
    }
    _, client = run_client(tmp_path, config)
    msg = call(client, "tools/list", {})
    client.close()
    names = [t["name"] for t in msg["result"]["tools"]]
    assert names == ["list_files", "read_file", "count_words"]


def test_empty_fault_empties_result(tmp_path):
    config = {"faults": [{"kind": "empty", "trigger": {"every": 1}}]}
    _, client = run_client(tmp_path, config)
    msg = call(client, "tools/call", READ_NOTES)
    client.close()
    assert msg["result"] == {"content": [], "isError": False}


def test_error_fault_replaces_result_with_error(tmp_path):
    config = {
        "faults": [
            {
                "kind": "error",
                "error_code": -32403,
                "error_message": "injected fault: permission denied",
                "trigger": {"every": 1},
            }
        ]
    }
    _, client = run_client(tmp_path, config)
    msg = call(client, "tools/call", READ_NOTES)
    client.close()
    assert msg["error"]["code"] == -32403
    assert "permission denied" in msg["error"]["message"]


def test_malformed_fault_breaks_json(tmp_path):
    config = {"faults": [{"kind": "malformed", "trigger": {"every": 1}}]}
    _, client = run_client(tmp_path, config)
    outcome, _ = client.request("tools/call", READ_NOTES, timeout=5.0)
    client.close()
    assert outcome == "parse_error"


def test_garbage_schema_fault_flips_types(tmp_path):
    config = {"faults": [{"kind": "garbage-schema", "trigger": {"every": 1}}]}
    _, client = run_client(tmp_path, config)
    msg = call(client, "tools/call", READ_NOTES)
    client.close()
    sc = msg["result"]["structuredContent"]
    assert isinstance(sc["size"], str)
    assert isinstance(sc["lines"], str)


def test_timeout_fault_delays_response(tmp_path):
    config = {
        "faults": [
            {"kind": "timeout", "delay_seconds": 0.4, "trigger": {"every": 1}}
        ]
    }
    _, client = run_client(tmp_path, config)
    started = time.monotonic()
    msg = call(client, "tools/call", READ_NOTES, timeout=5.0)
    elapsed = time.monotonic() - started
    client.close()
    assert elapsed >= 0.4
    assert "quick brown fox" in msg["result"]["content"][0]["text"]


def test_fault_fires_only_on_configured_calls(tmp_path):
    config = {"faults": [{"kind": "empty", "trigger": {"on_calls": [2]}}]}
    _, client = run_client(tmp_path, config)
    first = call(client, "tools/call", READ_NOTES)
    second = call(client, "tools/call", READ_NOTES)
    client.close()
    assert first["result"]["content"] != []
    assert second["result"]["content"] == []


def test_trace_records_fault_and_tool(tmp_path):
    config = {"faults": [{"kind": "empty", "trigger": {"every": 1}}]}
    proc, client = run_client(tmp_path, config, trace_name="trace.jsonl")
    call(client, "tools/call", READ_NOTES)
    client.close()
    proc.wait(timeout=10)
    entries = [
        json.loads(line)
        for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["tool"] == "read_file"
    assert entries[0]["fault"] == "empty"
    assert entries[0]["rewritten"] is not None
