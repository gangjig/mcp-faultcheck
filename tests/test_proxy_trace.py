"""Trace redaction: --trace must never write raw argument payloads.

A task can pass a credential as a tool argument; the JSONL trace is a
debug artifact that gets saved and shared, so sensitive-named values
are redacted (at any nesting depth) and long strings summarized,
while ordinary arguments stay readable.
"""

import json

from mcp_faultcheck.proxy import _trace_arguments

SECRET = "sk-live-EXAMPLESECRETVALUE"


def test_sensitive_keys_redacted():
    traced = _trace_arguments({
        "path": "notes.txt",
        "api_key": SECRET,
        "Authorization": f"Bearer {SECRET}",
        "user_password": SECRET,
        "session_id": SECRET,
    })
    assert traced["path"] == "notes.txt"
    for key in ("api_key", "Authorization", "user_password", "session_id"):
        assert traced[key] == "<redacted>"
    assert SECRET not in json.dumps(traced)


def test_nested_sensitive_keys_redacted():
    traced = _trace_arguments({
        "headers": {"Authorization": f"Bearer {SECRET}", "Accept": "text/plain"},
        "calls": [{"tool": "login", "access_token": SECRET}],
    })
    assert traced["headers"]["Authorization"] == "<redacted>"
    assert traced["headers"]["Accept"] == "text/plain"
    assert traced["calls"][0]["access_token"] == "<redacted>"
    assert SECRET not in json.dumps(traced)


def test_long_strings_summarized_like_responses():
    long_value = "x" * 500
    traced = _trace_arguments({"content": long_value})
    assert len(traced["content"]) == 163  # 160 chars + "..."
    assert traced["content"].endswith("...")


def test_ordinary_arguments_unchanged():
    args = {"path": "notes.txt", "limit": 10, "recursive": True, "mode": None}
    assert _trace_arguments(args) == args


def test_trace_end_to_end_never_contains_secret(tmp_path):
    from helpers import call, initialize, proxy_process
    from mcp_faultcheck.harness import Client

    proc = proxy_process(tmp_path, {"faults": []}, trace_name="trace.jsonl")
    client = Client(proc)
    initialize(client)
    call(client, "tools/call", {
        "name": "read_file",
        "arguments": {"path": "notes.txt", "api_key": SECRET},
    })
    client.close()
    proc.wait(timeout=10)
    raw = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw
    entry = json.loads(raw.splitlines()[0])
    assert entry["arguments"]["api_key"] == "<redacted>"
    assert entry["arguments"]["path"] == "notes.txt"
