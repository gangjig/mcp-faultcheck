"""Shared helpers for subprocess-level tests."""

import json
import subprocess
import sys

from mcp_faultcheck.harness import Client

TOY_SERVER = [sys.executable, "-m", "mcp_faultcheck.toy_server"]


def spawn(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
    )


def proxy_process(tmp_path, config: dict, server_cmd=TOY_SERVER, trace_name=None):
    """Spawn the proxy in front of a server with the given config dict."""
    config_path = tmp_path / "faults.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "mcp_faultcheck",
        "proxy",
        "--config",
        str(config_path),
    ]
    if trace_name:
        cmd += ["--trace", str(tmp_path / trace_name)]
    cmd += ["--", *server_cmd]
    return spawn(cmd)


def call(client: Client, method: str, params: dict, timeout: float = 5.0):
    outcome, payload = client.request(method, params, timeout)
    assert outcome == "ok", f"{method}: outcome {outcome}"
    return payload


def initialize(client: Client):
    return call(
        client,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "tests", "version": "0"},
        },
    )
