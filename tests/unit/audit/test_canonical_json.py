"""Unit tests for tessera.audit.canonical_json."""

from __future__ import annotations

import math

import pytest

from tessera.audit.canonical_json import canonical_json, canonical_json_str


# ---------------------------------------------------------------------------
# Primitive value tests
# ---------------------------------------------------------------------------


def test_none_serialized() -> None:
    assert canonical_json(None) == b"null"


def test_bool_true_false() -> None:
    assert canonical_json(True) == b"true"
    assert canonical_json(False) == b"false"


def test_integer() -> None:
    assert canonical_json(42) == b"42"
    assert canonical_json(-7) == b"-7"
    assert canonical_json(0) == b"0"


def test_float_to_int_coercion() -> None:
    # JCS §3.2.2: 1.0 must serialize as 1 (integer), not 1.0
    assert canonical_json(1.0) == b"1"
    assert canonical_json(0.0) == b"0"
    assert canonical_json(-4.0) == b"-4"


def test_float_preserved_when_fractional() -> None:
    result = canonical_json(1.5)
    assert result == b"1.5"

    result2 = canonical_json(-3.14)
    assert result2 == b"-3.14"


def test_nan_raises_value_error() -> None:
    with pytest.raises(ValueError, match="NaN"):
        canonical_json(math.nan)


def test_infinity_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Infinity"):
        canonical_json(math.inf)


def test_neg_infinity_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Infinity"):
        canonical_json(-math.inf)


def test_string() -> None:
    assert canonical_json("hello") == b'"hello"'
    assert canonical_json("") == b'""'
    # Unicode preserved without escape
    assert canonical_json("café") == '"café"'.encode("utf-8")


# ---------------------------------------------------------------------------
# Collection tests
# ---------------------------------------------------------------------------


def test_list() -> None:
    assert canonical_json([1, 2, 3]) == b"[1,2,3]"
    assert canonical_json([]) == b"[]"
    # Mixed types
    assert canonical_json([None, True, 1]) == b"[null,true,1]"


def test_nested_dict() -> None:
    result = canonical_json({"a": {"b": 1}})
    assert result == b'{"a":{"b":1}}'


def test_keys_sorted_lexicographically() -> None:
    result = canonical_json({"z": 1, "a": 2, "m": 3})
    assert result == b'{"a":2,"m":3,"z":1}'


def test_non_string_key_raises() -> None:
    with pytest.raises((ValueError, TypeError)):
        canonical_json({1: "value"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_rfc8785_deterministic_across_orderings() -> None:
    """Same content, different insertion orders → identical bytes."""
    d1 = {"c": 3, "a": 1, "b": 2}
    d2 = {"b": 2, "c": 3, "a": 1}
    d3 = {"a": 1, "b": 2, "c": 3}
    assert canonical_json(d1) == canonical_json(d2) == canonical_json(d3)


# ---------------------------------------------------------------------------
# canonical_json_str
# ---------------------------------------------------------------------------


def test_canonical_json_str_returns_str() -> None:
    result = canonical_json_str({"key": "value"})
    assert isinstance(result, str)
    assert result == '{"key":"value"}'


# ---------------------------------------------------------------------------
# Pydantic integration
# ---------------------------------------------------------------------------


def test_pydantic_model_serialized() -> None:
    try:
        from pydantic import BaseModel
    except ImportError:
        pytest.skip("pydantic not installed")

    class MyModel(BaseModel):
        name: str
        count: int

    m = MyModel(name="test", count=3)
    result = canonical_json(m)
    # Keys sorted: count < name
    assert result == b'{"count":3,"name":"test"}'


# ---------------------------------------------------------------------------
# Unsupported types
# ---------------------------------------------------------------------------


def test_unsupported_type_raises_type_error() -> None:
    with pytest.raises(TypeError):
        canonical_json(object())

    with pytest.raises(TypeError):
        canonical_json(set())  # type: ignore[arg-type]
