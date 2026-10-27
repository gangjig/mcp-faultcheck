"""stdio JSON-RPC proxy between a runner and a real MCP server.

The proxy spawns the server command, relays line-delimited JSON-RPC in
both directions, and rewrites *responses to tools/call requests only*,
according to the configured FaultEngine. With no faults configured it
is a pure passthrough.

Every tools/call round trip is traced: sequence number, tool name,
fault applied (if any), latency, and a summary of the original vs.
rewritten response. The trace can be dumped as JSONL with ``--trace``.
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


def _summary(line: str) -> str:
    line = line.strip()
    return line if len(line) <= _SUMMARY_LEN else line[:_SUMMARY_LEN] + "..."


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
                                "arguments": args,
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
