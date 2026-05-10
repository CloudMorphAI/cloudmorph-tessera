"""Chain-walk integrity verifier for tessera audit verify CLI command."""

from __future__ import annotations

from typing import Any

from tessera.audit.chain import HashChain
from tessera.audit.sinks.base import AuditSink


def verify_chain(sink: AuditSink, scope: str) -> dict[str, Any]:
    """Walk the audit chain for a scope and verify hash integrity.

    Returns:
        {
            "scope": scope,
            "events_checked": N,
            "first_event_at": str | None,
            "last_event_at": str | None,
            "ok": True/False,
            "first_failure": None | {
                "seq": int,
                "event_id": str,
                "kind": "hash_mismatch" | "chain_break",
                "expected_event_hash": str,
                "computed_event_hash": str,
            }
        }
    """
    events_checked = 0
    first_event_at: str | None = None
    last_event_at: str | None = None
    prev_event: dict[str, Any] | None = None

    for seq, event in enumerate(sink.iter_events(scope), start=1):
        events_checked += 1
        occurred_at = event.get("occurredAt")

        if first_event_at is None:
            first_event_at = occurred_at
        last_event_at = occurred_at

        # Check individual event hash integrity first
        if not HashChain.verify_event_hash(event):
            return {
                "scope": scope,
                "events_checked": events_checked,
                "first_event_at": first_event_at,
                "last_event_at": last_event_at,
                "ok": False,
                "first_failure": {
                    "seq": seq,
                    "event_id": event.get("eventId", ""),
                    "kind": "hash_mismatch",
                    "expected_event_hash": event.get("eventHash", ""),
                    "computed_event_hash": "",
                },
            }

        # Check chain linkage with previous event
        if prev_event is not None and not HashChain.verify_pair(prev_event, event):
            return {
                "scope": scope,
                "events_checked": events_checked,
                "first_event_at": first_event_at,
                "last_event_at": last_event_at,
                "ok": False,
                "first_failure": {
                    "seq": seq,
                    "event_id": event.get("eventId", ""),
                    "kind": "chain_break",
                    "expected_event_hash": prev_event.get("eventHash", ""),
                    "computed_event_hash": event.get("prevEventHash", ""),
                },
            }

        prev_event = event

    return {
        "scope": scope,
        "events_checked": events_checked,
        "first_event_at": first_event_at,
        "last_event_at": last_event_at,
        "ok": True,
        "first_failure": None,
    }
