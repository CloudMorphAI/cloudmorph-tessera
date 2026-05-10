"""Unit tests for tessera.intent.extract_intent."""
from __future__ import annotations

import pytest

from tessera.errors import PolicyError
from tessera.intent import extract_intent


def test_intent_not_present_returns_none() -> None:
    result = extract_intent({"other_key": "value"})
    assert result is None


def test_intent_not_present_meta_none_returns_none() -> None:
    result = extract_intent(None)
    assert result is None


def test_intent_required_missing_raises() -> None:
    with pytest.raises(PolicyError) as exc_info:
        extract_intent(None, intent_required=True)
    assert exc_info.value.reason == "intent_required"


def test_intent_required_missing_key_raises() -> None:
    with pytest.raises(PolicyError) as exc_info:
        extract_intent({"other_key": "value"}, intent_required=True)
    assert exc_info.value.reason == "intent_required"


def test_intent_valid_verbs_list() -> None:
    meta = {"tessera_intent": {"verbs": ["read.list", "analyze"]}}
    result = extract_intent(meta)
    assert result is not None
    assert result["verbs"] == ["read.list", "analyze"]


def test_intent_unknown_verb_raises() -> None:
    known = frozenset({"read.list", "analyze"})
    meta = {"tessera_intent": {"verbs": ["write.delete"]}}
    with pytest.raises(PolicyError, match="unknown intent verb"):
        extract_intent(meta, known_verbs=known)


def test_intent_purpose_within_limit() -> None:
    purpose = "a" * 1024
    meta = {"tessera_intent": {"verbs": ["read.list"], "purpose": purpose}}
    result = extract_intent(meta)
    assert result is not None
    assert result["purpose"] == purpose


def test_intent_purpose_too_long_raises() -> None:
    purpose = "a" * 1025
    meta = {"tessera_intent": {"verbs": ["read.list"], "purpose": purpose}}
    with pytest.raises(PolicyError, match="1024 characters"):
        extract_intent(meta)


def test_intent_not_a_dict_raises() -> None:
    meta = {"tessera_intent": "not-a-dict"}
    with pytest.raises(PolicyError, match="must be a dict"):
        extract_intent(meta)


def test_intent_verbs_not_a_list_raises() -> None:
    meta = {"tessera_intent": {"verbs": "read.list"}}
    with pytest.raises(PolicyError, match="'verbs' must be a list"):
        extract_intent(meta)


def test_intent_no_purpose_ok() -> None:
    meta = {"tessera_intent": {"verbs": ["read.list"]}}
    result = extract_intent(meta)
    assert result is not None
    assert "purpose" not in result


def test_custom_meta_key() -> None:
    meta = {"custom_intent": {"verbs": ["read.list"]}}
    result = extract_intent(meta, meta_key="custom_intent")
    assert result is not None
    assert result["verbs"] == ["read.list"]


def test_custom_meta_key_not_found_returns_none() -> None:
    meta = {"tessera_intent": {"verbs": ["read.list"]}}
    result = extract_intent(meta, meta_key="custom_intent")
    assert result is None
