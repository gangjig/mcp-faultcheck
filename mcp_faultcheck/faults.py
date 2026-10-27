"""Fault definitions, triggering, and response rewriting.

Faults apply only to responses of ``tools/call`` requests, in the
server -> client direction. Everything else (the initialize handshake,
``tools/list``, notifications, ...) passes through untouched: the point
is to break *tool results*, not the protocol plumbing.

Triggering is deterministic by default — a fault fires on every k-th
call, on an explicit list of call numbers, or on the first n calls —
so a report can be reproduced exactly. A probability trigger exists
but is seeded, and is never the default.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any, Optional

FAULT_KINDS = ("timeout", "empty", "malformed", "error", "garbage-schema")


@dataclass
class Trigger:
    """When a fault fires, counted over tools/call requests (1-indexed).

    Exactly one mode must be set.
    """

    every: Optional[int] = None
    on_calls: Optional[list] = None
    first: Optional[int] = None
    probability: Optional[float] = None

    def __post_init__(self) -> None:
        modes = [
            self.every is not None,
            self.on_calls is not None,
            self.first is not None,
            self.probability is not None,
        ]
        if sum(modes) != 1:
            raise ValueError(
                "trigger needs exactly one of: every, on_calls, first, probability"
            )
        if self.every is not None and self.every < 1:
            raise ValueError("trigger.every must be >= 1")
        if self.first is not None and self.first < 1:
            raise ValueError("trigger.first must be >= 1")
        if self.on_calls is not None and (
            not self.on_calls or any(n < 1 for n in self.on_calls)
        ):
            raise ValueError("trigger.on_calls must be a non-empty list of call numbers >= 1")
        if self.probability is not None and not 0.0 < self.probability <= 1.0:
            raise ValueError("trigger.probability must be in (0, 1]")

    def fires(self, call_index: int, rng: random.Random) -> bool:
        if self.every is not None:
            return call_index % self.every == 0
        if self.on_calls is not None:
            return call_index in self.on_calls
        if self.first is not None:
            return call_index <= self.first
        return rng.random() < self.probability  # type: ignore[operator]


@dataclass
class FaultSpec:
    """One configured fault."""

    kind: str
    trigger: Trigger
    delay_seconds: float = 2.5  # timeout: hold the response this long
    error_code: int = -32001  # error: JSON-RPC error code to return
    error_message: str = "injected fault: tool call failed"  # error
    seed: int = 0  # only used when the trigger is probabilistic

    def __post_init__(self) -> None:
        if self.kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind: {self.kind!r} (known: {FAULT_KINDS})")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be >= 0")


class FaultEngine:
    """Counts tools/call requests and decides which fault, if any, fires.

    At most one fault fires per response: the first spec (in config
    order) whose trigger matches wins.
    """

    def __init__(self, specs: list[FaultSpec]):
        self._specs = list(specs)
        self._rngs = [random.Random(spec.seed) for spec in self._specs]
        self.call_count = 0

    @property
    def specs(self) -> list[FaultSpec]:
        return list(self._specs)

    def begin_call(self) -> None:
        self.call_count += 1

    def decide(self) -> Optional[FaultSpec]:
        """The fault firing for the current (most recently begun) call."""
        for spec, rng in zip(self._specs, self._rngs):
            if spec.trigger.fires(self.call_count, rng):
                return spec
        return None


def _garbage(value: Any) -> Any:
    """Same shape, wrong types: every leaf value changes JSON type."""
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        for conv in (int, float):
            try:
                return conv(value)
            except ValueError:
                continue
        return len(value)
    if value is None:
        return 0
    if isinstance(value, list):
        return [_garbage(item) for item in value]
    if isinstance(value, dict):
        return {key: _garbage(item) for key, item in value.items()}
    return value


def apply_fault(spec: FaultSpec, raw_line: str, message: dict) -> tuple[str, float]:
    """Rewrite one tools/call response line under ``spec``.

    Returns ``(outgoing_line, delay_seconds)``. Only the timeout fault
    delays; the caller sleeps before forwarding.
    """
    if spec.kind == "timeout":
        return raw_line, spec.delay_seconds

    if spec.kind == "empty":
        out = {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {"content": [], "isError": False},
        }
        return json.dumps(out), 0.0

    if spec.kind == "error":
        out = {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": spec.error_code, "message": spec.error_message},
        }
        return json.dumps(out), 0.0

    if spec.kind == "malformed":
        # Truncated JSON is never valid JSON: unbalanced by construction.
        cut = max(1, len(raw_line) // 2)
        return raw_line[:cut], 0.0

    if spec.kind == "garbage-schema":
        out = dict(message)
        if "result" in out:
            out["result"] = _garbage(out["result"])
        return json.dumps(out), 0.0

    raise ValueError(f"unhandled fault kind: {spec.kind!r}")  # pragma: no cover
