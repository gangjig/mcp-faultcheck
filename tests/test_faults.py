import json
import random

import pytest

from mcp_faultcheck.faults import (
    FaultEngine,
    FaultSpec,
    Trigger,
    apply_fault,
)


def spec(kind, trigger=None, **kw):
    return FaultSpec(kind=kind, trigger=trigger or Trigger(every=1), **kw)


def sample_message():
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "content": [{"type": "text", "text": "hello"}],
            "structuredContent": {
                "size": 128,
                "ratio": 0.5,
                "name": "notes",
                "count_text": "42",
                "ok": True,
                "missing": None,
                "tags": ["a", "b"],
                "nested": {"depth": 2},
            },
            "isError": False,
        },
    }


# --- Trigger ---------------------------------------------------------------


def test_trigger_every():
    t = Trigger(every=3)
    rng = random.Random(0)
    assert [t.fires(i, rng) for i in range(1, 8)] == [
        False, False, True, False, False, True, False,
    ]


def test_trigger_on_calls():
    t = Trigger(on_calls=[2, 5])
    rng = random.Random(0)
    assert [t.fires(i, rng) for i in range(1, 7)] == [
        False, True, False, False, True, False,
    ]


def test_trigger_first():
    t = Trigger(first=2)
    rng = random.Random(0)
    assert [t.fires(i, rng) for i in range(1, 5)] == [True, True, False, False]


def test_trigger_probability_seeded_is_reproducible():
    t = Trigger(probability=0.5)
    rng_a, rng_b = random.Random(7), random.Random(7)
    seq_a = [t.fires(i, rng_a) for i in range(20)]
    seq_b = [t.fires(i, rng_b) for i in range(20)]
    assert seq_a == seq_b
    assert any(seq_a) and not all(seq_a)


def test_trigger_probability_one_always_fires():
    t = Trigger(probability=1.0)
    rng = random.Random(0)
    assert all(t.fires(i, rng) for i in range(10))


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"every": 1, "first": 1},
        {"every": 0},
        {"first": 0},
        {"on_calls": []},
        {"on_calls": [0]},
        {"probability": 0.0},
        {"probability": 1.5},
    ],
)
def test_trigger_invalid(kwargs):
    with pytest.raises(ValueError):
        Trigger(**kwargs)


def test_spec_unknown_kind_rejected():
    with pytest.raises(ValueError):
        spec("explode")


# --- FaultEngine -----------------------------------------------------------


def test_engine_counts_calls_and_first_match_wins():
    engine = FaultEngine(
        [spec("empty", Trigger(on_calls=[2])), spec("error", Trigger(every=1))]
    )
    engine.begin_call()
    assert engine.decide().kind == "error"  # call 1: only 'every' matches
    engine.begin_call()
    assert engine.decide().kind == "empty"  # call 2: first spec wins
    assert engine.call_count == 2


def test_engine_no_specs_never_fires():
    engine = FaultEngine([])
    engine.begin_call()
    assert engine.decide() is None


# --- apply_fault -----------------------------------------------------------


def test_apply_timeout_returns_original_with_delay():
    msg = sample_message()
    line = json.dumps(msg)
    out, delay = apply_fault(spec("timeout", delay_seconds=3.0), line, msg)
    assert out == line
    assert delay == 3.0


def test_apply_empty_keeps_id_and_empties_content():
    msg = sample_message()
    out, delay = apply_fault(spec("empty"), json.dumps(msg), msg)
    parsed = json.loads(out)
    assert parsed["id"] == 7
    assert parsed["result"] == {"content": [], "isError": False}
    assert delay == 0.0


def test_apply_error_shape_and_custom_code():
    msg = sample_message()
    out, _ = apply_fault(
        spec("error", error_code=-32403, error_message="nope"), json.dumps(msg), msg
    )
    parsed = json.loads(out)
    assert parsed["id"] == 7
    assert parsed["error"] == {"code": -32403, "message": "nope"}
    assert "result" not in parsed


def test_apply_malformed_is_invalid_json():
    msg = sample_message()
    out, _ = apply_fault(spec("malformed"), json.dumps(msg), msg)
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_apply_garbage_schema_flips_leaf_types():
    msg = sample_message()
    out, _ = apply_fault(spec("garbage-schema"), json.dumps(msg), msg)
    parsed = json.loads(out)  # still valid JSON, same shape
    sc = parsed["result"]["structuredContent"]
    assert sc["size"] == "128" and isinstance(sc["size"], str)
    assert sc["ratio"] == "0.5" and isinstance(sc["ratio"], str)
    assert sc["name"] == 5 and isinstance(sc["name"], int)  # len("notes")
    assert sc["count_text"] == 42 and isinstance(sc["count_text"], int)
    assert sc["ok"] == "true"
    assert sc["missing"] == 0
    assert sc["nested"] == {"depth": "2"}
    assert isinstance(sc["tags"], list) and sc["tags"] == [1, 1]
    assert parsed["result"]["isError"] == "false"
    assert parsed["id"] == 7
