"""Fault configuration: JSON in, validated FaultSpec objects out.

Example config file::

    {
      "call_timeout_seconds": 1.0,
      "faults": [
        {"kind": "timeout", "delay_seconds": 2.5, "trigger": {"every": 1}},
        {"kind": "error", "error_code": -32403,
         "error_message": "injected fault: permission denied",
         "trigger": {"on_calls": [2]}}
      ]
    }

``call_timeout_seconds`` is the harness-side per-call deadline: how long
the runner waits for a tools/call response before abandoning the step.
It is what makes the timeout fault observable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .faults import FaultSpec, Trigger


class ConfigError(ValueError):
    """Raised for any malformed fault configuration."""


@dataclass
class HarnessConfig:
    faults: list[FaultSpec] = field(default_factory=list)
    call_timeout_seconds: float = 1.0


def _trigger_from_dict(data: dict) -> Trigger:
    known = {"every", "on_calls", "first", "probability"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown trigger keys: {sorted(unknown)}")
    try:
        return Trigger(
            every=data.get("every"),
            on_calls=data.get("on_calls"),
            first=data.get("first"),
            probability=data.get("probability"),
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _spec_from_dict(data: dict) -> FaultSpec:
    if "kind" not in data:
        raise ConfigError("fault entry is missing 'kind'")
    if "trigger" not in data:
        raise ConfigError(f"fault {data.get('kind')!r} is missing 'trigger'")
    try:
        return FaultSpec(
            kind=data["kind"],
            trigger=_trigger_from_dict(data["trigger"]),
            delay_seconds=data.get("delay_seconds", 2.5),
            error_code=data.get("error_code", -32001),
            error_message=data.get("error_message", "injected fault: tool call failed"),
            seed=data.get("seed", 0),
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def config_from_dict(data: dict) -> HarnessConfig:
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    faults = [_spec_from_dict(entry) for entry in data.get("faults", [])]
    timeout = data.get("call_timeout_seconds", 1.0)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("call_timeout_seconds must be a positive number")
    return HarnessConfig(faults=faults, call_timeout_seconds=float(timeout))


def load_config(path: str | Path) -> HarnessConfig:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    return config_from_dict(data)


def spec_to_dict(spec: FaultSpec) -> dict:
    """Serialize one spec back to config-file shape (used by the harness
    to hand a single-fault config to the proxy subprocess)."""
    trigger = spec.trigger
    if trigger.every is not None:
        trig = {"every": trigger.every}
    elif trigger.on_calls is not None:
        trig = {"on_calls": list(trigger.on_calls)}
    elif trigger.first is not None:
        trig = {"first": trigger.first}
    else:
        trig = {"probability": trigger.probability}
    return {
        "kind": spec.kind,
        "trigger": trig,
        "delay_seconds": spec.delay_seconds,
        "error_code": spec.error_code,
        "error_message": spec.error_message,
        "seed": spec.seed,
    }
