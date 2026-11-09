"""stdio JSON-RPC proxy between a runner and a real MCP server.

The proxy spawns the server command, relays line-delimited JSON-RPC in
both directions, and rewrites *responses to tools/call requests only*,
according to the configured FaultEngine. With no faults configured it
is a pure passthrough.

Every tools/call round trip is traced: sequence number, tool name,
fault applied (if any), latency, and a summary of the original vs.
rewritten response. The trace can be dumped as JSONL with ``--trace``.

Trace argument values are *not* dumped verbatim: a value whose key
name looks sensitive (token, password, credential, ...) is replaced
with ``"<redacted>"``, and long strings are truncated to the same
160-character summary as the response fields — recursively, so a
secret nested inside a dict or list argument is covered too. A task
that passes a credential as a tool argument does not write it to
disk in the clear.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Optional

from .faults import FaultEngine, apply_fault

_SUMMARY_LEN = 160

#: Substrings that mark an argument key as carrying a secret. Matched
#: against the lowercased key name, at any nesting depth.
_SENSITIVE_KEY_HINTS = (
    "key", "token", "secret", "password", "passwd", "credential",
    "auth", "cookie", "session", "bearer", "private",
)

REDACTED = "<redacted>"


def _summary(line: str) -> str:
    line = line.strip()
    return line if len(line) <= _SUMMARY_LEN else line[:_SUMMARY_LEN] + "..."


def _is_sensitive_key(key: object) -> bool:
    name = str(key).lower()
    return any(hint in name for hint in _SENSITIVE_KEY_HINTS)


def _trace_value(key: object, value):
    """One argument value as it may appear in the trace."""
    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: _trace_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trace_value(None, item) for item in value]
    if isinstance(value, str):
        return _summary(value)
    return value


def _trace_arguments(args: dict) -> dict:
    """The trace-safe form of a tools/call arguments dict.

    Same shape as the input, but sensitive-named values (at any
    depth) are redacted and long strings are summarized, so the
    JSONL trace never holds a full argument payload in the clear.
    """
    if not isinstance(args, dict):
        return {}
    return {k: _trace_value(k, v) for k, v in args.items()}


def _parse(line: str) -> Optional[dict]:
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return msg if isinstance(msg, dict) else None


class Proxy:
    """Relays between binary streams and a spawned server process."""

    def __init__(
        self,
        server_cmd: list[str],
        engine: FaultEngine,
        trace: Optional[list] = None,
    ):
        self.server_cmd = server_cmd
        self.engine = engine
        self.trace: list[dict] = trace if trace is not None else []
        self._pending: dict = {}  # request id -> (tool, args, monotonic start)
        self._seq = 0
        self._lock = threading.Lock()

    def run(self, client_in: BinaryIO, client_out: BinaryIO) -> int:
        proc = subprocess.Popen(
            self.server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        assert proc.stdin is not None and proc.stdout is not None
        t_up = threading.Thread(
            target=self._pump_requests, args=(client_in, proc.stdin), daemon=True
        )
        t_up.start()
        try:
            self._pump_responses(proc.stdout, client_out)
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return proc.returncode or 0

    # client -> server: forward unchanged, note pending tools/call ids.
    def _pump_requests(self, client_in: BinaryIO, server_in: BinaryIO) -> None:
        try:
            for raw in client_in:
                line = raw.decode("utf-8", errors="replace")
                msg = _parse(line)
                if msg is not None and msg.get("method") == "tools/call" and "id" in msg:
                    params = msg.get("params") or {}
                    with self._lock:
                        self.engine.begin_call()
                        self._pending[msg["id"]] = (
                            params.get("name", "?"),
                            params.get("arguments") or {},
                            time.monotonic(),
                        )
                try:
                    server_in.write(raw)
                    server_in.flush()
                except (BrokenPipeError, OSError):
                    return
        except (BrokenPipeError, OSError):
            return
        finally:
            # Client went away: closing the server's stdin is what lets
            # the server exit, which in turn ends the response pump.
            try:
                server_in.close()
            except (BrokenPipeError, OSError):
                pass

    # server -> client: rewrite tools/call responses, pass the rest.
    def _pump_responses(self, server_out: BinaryIO, client_out: BinaryIO) -> None:
        try:
            for raw in server_out:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                out_line = line
                msg = _parse(line)
                if msg is not None and "id" in msg:
                    with self._lock:
                        pending = self._pending.pop(msg["id"], None)
                    if pending is not None:
                        tool, args, started = pending
                        spec = self.engine.decide()
                        delay = 0.0
                        if spec is not None:
                            out_line, delay = apply_fault(spec, line, msg)
                        if delay > 0:
                            time.sleep(delay)
                        self._seq += 1
                        self.trace.append(
                            {
                                "seq": self._seq,
                                "tool": tool,
                                "arguments": _trace_arguments(args),
                                "fault": spec.kind if spec is not None else None,
                                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                                "original": _summary(line),
                                "rewritten": _summary(out_line)
                                if spec is not None
                                else None,
                            }
                        )
                try:
                    client_out.write(out_line.encode("utf-8") + b"\n")
                    client_out.flush()
                except (BrokenPipeError, OSError):
                    return
        except (BrokenPipeError, OSError):
            return


def run_proxy_cli(argv: list[str]) -> int:
    """``mcp-faultcheck proxy [--config PATH] [--trace PATH] -- SERVER CMD...``"""
    from .config import load_config

    args = list(argv)
    if "--" not in args:
        print("usage: mcp-faultcheck proxy [--config PATH] [--trace PATH] -- CMD...",
              file=sys.stderr)
        return 2
    sep = args.index("--")
    opts, server_cmd = args[:sep], args[sep + 1 :]
    if not server_cmd:
        print("proxy: missing server command after --", file=sys.stderr)
        return 2

    config_path: Optional[str] = None
    trace_path: Optional[str] = None
    i = 0
    while i < len(opts):
        if opts[i] == "--config" and i + 1 < len(opts):
            config_path = opts[i + 1]
            i += 2
        elif opts[i] == "--trace" and i + 1 < len(opts):
            trace_path = opts[i + 1]
            i += 2
        else:
            print(f"proxy: unknown option {opts[i]!r}", file=sys.stderr)
            return 2

    specs = load_config(config_path).faults if config_path else []
    trace: list[dict] = []
    proxy = Proxy(server_cmd, FaultEngine(specs), trace)
    rc = proxy.run(sys.stdin.buffer, sys.stdout.buffer)
    if trace_path:
        with Path(trace_path).open("w", encoding="utf-8") as fh:
            for entry in trace:
                fh.write(json.dumps(entry) + "\n")
    return rc
