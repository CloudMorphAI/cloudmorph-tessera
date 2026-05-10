"""Hash chain bookkeeping for the audit log.

Each AuditEvent has:
- prevEventHash: SHA-256 of the previous event for the same tenantId (or empty
  string for the first event). In OSS multi-token mode, tenantId is the
  per-token scope from AuthContext.scope — each scope maintains its own
  independent hash chain stream.
- eventHash: SHA-256 of canonical_json({...event with prevEventHash set,
  eventHash="", signature=""})

A verifier walks the chain and asserts event[i].prevEventHash == event[i-1].eventHash
for all i. Tampering breaks the chain.

This class manages the in-process bookkeeping (the "current head" per tenantId).
It does NOT persist anything — the AuditEmitter is responsible for sending the
event to sinks; persistence + restart-survivability is the sink's concern.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from tessera.audit.canonical_json import canonical_json


class HashChain:
    """Per-tenant rolling hash bookkeeping. Thread-safe."""

    def __init__(self) -> None:
        self._heads: dict[str, str] = {}
        self._lock = threading.RLock()

    def head(self, tenant_id: str) -> str:
        """Return the most recent eventHash for a tenant. Empty string if no events yet."""
        with self._lock:
            return self._heads.get(tenant_id, "")

    def restore_head(self, tenant_id: str, head_hash: str) -> None:
        """Restore the head hash for a tenant (e.g., on process restart from persisted state).

        Validates the hash is a 64-char lowercase hex string.
        """
        if head_hash and (len(head_hash) != 64 or not all(c in "0123456789abcdef" for c in head_hash)):
            raise ValueError(f"restore_head: invalid sha256 hex digest: {head_hash!r}")
        with self._lock:
            self._heads[tenant_id] = head_hash

    def stamp(self, event: dict[str, Any]) -> dict[str, Any]:
        """Stamp prevEventHash + eventHash onto an event, advance the chain head, return the stamped event.

        The event must have a `tenantId` field. The eventHash is computed over the
        canonical-json representation of the event with `eventHash` and `signature`
        blanked, after `prevEventHash` has been set.
        """
        tenant_id = event.get("tenantId")
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("HashChain.stamp: event must have a non-empty string tenantId")

        with self._lock:
            prev = self._heads.get(tenant_id, "")
            stamped = {
                **event,
                "prevEventHash": prev,
                "eventHash": "",
                "signature": event.get("signature", ""),
            }
            digest_input = {**stamped, "eventHash": "", "signature": ""}
            event_hash = hashlib.sha256(canonical_json(digest_input)).hexdigest()
            stamped["eventHash"] = event_hash
            self._heads[tenant_id] = event_hash
            # Drop the empty signature key if it was synthesised — only emit if signed.
            if not stamped.get("signature"):
                stamped.pop("signature", None)
            return stamped

    @staticmethod
    def verify_pair(prev_event: dict[str, Any], next_event: dict[str, Any]) -> bool:
        """Check that next_event.prevEventHash == prev_event.eventHash."""
        return next_event.get("prevEventHash", "") == prev_event.get("eventHash", "")

    @staticmethod
    def verify_event_hash(event: dict[str, Any]) -> bool:
        """Recompute the event's hash and compare to the stored eventHash. Returns True on match."""
        stored = event.get("eventHash", "")
        if not stored:
            return False
        digest_input = {**event, "eventHash": "", "signature": ""}
        recomputed = hashlib.sha256(canonical_json(digest_input)).hexdigest()
        return stored == recomputed
