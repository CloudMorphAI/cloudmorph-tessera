"""Tests for the audit hash chain bookkeeping."""

from __future__ import annotations

import hashlib

import pytest

from cloudmorph_common.audit.canonical_json import canonical_json
from cloudmorph_common.audit.chain import HashChain


def _make_event(tenant_id: str, payload: dict | None = None) -> dict:
    return {
        "schemaVersion": "v0.1",
        "eventId": "evt_test_xyz",
        "tenantId": tenant_id,
        "eventType": "decision.made",
        "payload": payload or {"outcome": "allow"},
        "occurredAt": "2026-04-23T10:00:00Z",
    }


class TestHashChainStamping:
    def test_first_event_has_empty_prev_hash(self):
        chain = HashChain()
        stamped = chain.stamp(_make_event("tnt_a"))
        assert stamped["prevEventHash"] == ""
        assert len(stamped["eventHash"]) == 64
        assert all(c in "0123456789abcdef" for c in stamped["eventHash"])

    def test_second_event_links_to_first(self):
        chain = HashChain()
        first = chain.stamp(_make_event("tnt_a"))
        second = chain.stamp(_make_event("tnt_a", {"outcome": "deny"}))
        assert second["prevEventHash"] == first["eventHash"]

    def test_event_hash_is_recoverable(self):
        chain = HashChain()
        stamped = chain.stamp(_make_event("tnt_a"))
        digest_input = {**stamped, "eventHash": "", "signature": ""}
        recomputed = hashlib.sha256(canonical_json(digest_input)).hexdigest()
        assert recomputed == stamped["eventHash"]

    def test_chain_isolated_per_tenant(self):
        chain = HashChain()
        first_a = chain.stamp(_make_event("tnt_a"))
        first_b = chain.stamp(_make_event("tnt_b"))
        # Both first-events; both should have empty prev hashes.
        assert first_a["prevEventHash"] == ""
        assert first_b["prevEventHash"] == ""
        # Adding to one tenant doesn't affect the other.
        second_a = chain.stamp(_make_event("tnt_a"))
        assert second_a["prevEventHash"] == first_a["eventHash"]
        # tnt_b's head is unaffected.
        second_b = chain.stamp(_make_event("tnt_b"))
        assert second_b["prevEventHash"] == first_b["eventHash"]

    def test_missing_tenant_id_raises(self):
        chain = HashChain()
        with pytest.raises(ValueError, match="tenantId"):
            chain.stamp({"eventType": "x", "occurredAt": "2026-04-23T00:00:00Z"})


class TestHashChainVerification:
    def test_verify_pair_matches(self):
        chain = HashChain()
        first = chain.stamp(_make_event("tnt_a"))
        second = chain.stamp(_make_event("tnt_a"))
        assert HashChain.verify_pair(first, second)

    def test_verify_pair_mismatch_detected(self):
        chain = HashChain()
        first = chain.stamp(_make_event("tnt_a"))
        second = chain.stamp(_make_event("tnt_a"))
        # Tamper: rewrite second's prevEventHash
        tampered = {**second, "prevEventHash": "0" * 64}
        assert not HashChain.verify_pair(first, tampered)

    def test_verify_event_hash(self):
        chain = HashChain()
        stamped = chain.stamp(_make_event("tnt_a"))
        assert HashChain.verify_event_hash(stamped)

    def test_verify_event_hash_detects_tampering(self):
        chain = HashChain()
        stamped = chain.stamp(_make_event("tnt_a"))
        tampered = {**stamped, "payload": {"outcome": "allow", "INJECTED": True}}
        assert not HashChain.verify_event_hash(tampered)

    def test_verify_event_hash_missing_returns_false(self):
        assert not HashChain.verify_event_hash({"eventHash": ""})


class TestHashChainRestore:
    def test_restore_head(self):
        chain = HashChain()
        h = "a" * 64
        chain.restore_head("tnt_a", h)
        assert chain.head("tnt_a") == h

    def test_restore_head_invalid_hex_raises(self):
        chain = HashChain()
        with pytest.raises(ValueError, match="invalid sha256"):
            chain.restore_head("tnt_a", "not-hex")

    def test_restore_head_empty_ok(self):
        chain = HashChain()
        chain.restore_head("tnt_a", "")
        assert chain.head("tnt_a") == ""
