import json

import pytest

from mcp_faultcheck.config import (
    ConfigError,
    config_from_dict,
    load_config,
    spec_to_dict,
)
from mcp_faultcheck.faults import FaultSpec, Trigger


def test_config_valid_two_faults():
    cfg = config_from_dict(
        {
            "call_timeout_seconds": 2.0,
            "faults": [
                {"kind": "timeout", "delay_seconds": 3.0, "trigger": {"every": 2}},
                {"kind": "error", "error_code": -32403, "trigger": {"on_calls": [1, 3]}},
            ],
        }
    )
    assert cfg.call_timeout_seconds == 2.0
    assert [f.kind for f in cfg.faults] == ["timeout", "error"]
    assert cfg.faults[0].trigger.every == 2
    assert cfg.faults[1].error_code == -32403


def test_config_defaults():
    cfg = config_from_dict({})
    assert cfg.faults == []
    assert cfg.call_timeout_seconds == 1.0


def test_config_unknown_fault_kind():
    with pytest.raises(ConfigError):
        config_from_dict({"faults": [{"kind": "explode", "trigger": {"every": 1}}]})


def test_config_unknown_trigger_key():
    with pytest.raises(ConfigError):
        config_from_dict(
            {"faults": [{"kind": "empty", "trigger": {"sometimes": 1}}]}
        )


def test_config_trigger_two_modes():
    with pytest.raises(ConfigError):
        config_from_dict(
            {"faults": [{"kind": "empty", "trigger": {"every": 1, "first": 1}}]}
        )


def test_config_missing_kind():
    with pytest.raises(ConfigError):
        config_from_dict({"faults": [{"trigger": {"every": 1}}]})


def test_config_missing_trigger():
    with pytest.raises(ConfigError):
        config_from_dict({"faults": [{"kind": "empty"}]})


@pytest.mark.parametrize("bad", [0, -1, "soon"])
def test_config_bad_call_timeout(bad):
    with pytest.raises(ConfigError):
        config_from_dict({"call_timeout_seconds": bad})


def test_load_config_roundtrip(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({"faults": [{"kind": "empty", "trigger": {"first": 2}}]}),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.faults[0].kind == "empty"
    assert cfg.faults[0].trigger.first == 2


def test_load_config_invalid_json(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_spec_to_dict_roundtrip():
    original = FaultSpec(
        kind="timeout",
        trigger=Trigger(on_calls=[1, 4]),
        delay_seconds=3.5,
        error_code=-32001,
        seed=9,
    )
    cfg = config_from_dict({"faults": [spec_to_dict(original)]})
    restored = cfg.faults[0]
    assert restored.kind == "timeout"
    assert restored.trigger.on_calls == [1, 4]
    assert restored.delay_seconds == 3.5
    assert restored.seed == 9
