"""Unit tests: audit_event.schema.json validates emitted events."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).parent.parent.parent / "schemas" / "audit_event.schema.json"


@pytest.fixture()
def audit_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_event() -> dict:
    return {
        "schemaVersion": "v0.1",
        "eventId": "evt_abc123def456ghi789jkl0123456789",
        "tenantId": "test-scope",
        "eventType": "decision",
        "occurredAt": "2026-05-10T23:00:00.000000Z",
        "prevEventHash": "",
        "eventHash": "a" * 64,
        "payload": {"mode": "enforcement", "decision": "allow"},
    }


def test_valid_event_passes(audit_schema: dict) -> None:
    jsonschema.validate(_valid_event(), audit_schema)


def test_missing_required_field_fails(audit_schema: dict) -> None:
    evt = _valid_event()
    del evt["eventHash"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(evt, audit_schema)


def test_invalid_event_hash_fails(audit_schema: dict) -> None:
    evt = _valid_event()
    evt["eventHash"] = "not-a-sha256"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(evt, audit_schema)


def test_event_with_session_id_passes(audit_schema: dict) -> None:
    evt = _valid_event()
    evt["sessionId"] = "sess_123"
    evt["actorId"] = "user_456"
    jsonschema.validate(evt, audit_schema)
