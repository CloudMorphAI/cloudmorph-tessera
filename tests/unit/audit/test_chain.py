"""Unit tests for tessera.audit.chain.HashChain."""

from __future__ import annotations

import threading

import pytest

from tessera.audit.chain import HashChain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(tenant_id: str = "tenant-a", **kwargs: object) -> dict:
    return {"tenantId": tenant_id, "action": "test.action", **kwargs}


# ---------------------------------------------------------------------------
# Stamp / chain-building tests
# ---------------------------------------------------------------------------


def test_first_event_has_empty_prev_hash() -> None:
    chain = HashChain()
    stamped = chain.stamp(_make_event())
    assert stamped["prevEventHash"] == ""


def test_second_event_has_prev_hash() -> None:
    chain = HashChain()
    first = chain.stamp(_make_event())
    second = chain.stamp(_make_event())
    assert second["prevEventHash"] == first["eventHash"]
    assert second["prevEventHash"] != ""


def test_per_scope_head_isolation() -> None:
    """Two different tenantIds must not share chain state."""
    chain = HashChain()
    a1 = chain.stamp(_make_event("scope-a"))
    b1 = chain.stamp(_make_event("scope-b"))
    a2 = chain.stamp(_make_event("scope-a"))
    b2 = chain.stamp(_make_event("scope-b"))

    # Each scope's second event points back to its own first
    assert a2["prevEventHash"] == a1["eventHash"]
    assert b2["prevEventHash"] == b1["eventHash"]

    # The two scopes' hashes must differ (different chain histories)
    assert a1["eventHash"] != b1["eventHash"]


# ---------------------------------------------------------------------------
# restore_head tests
# ---------------------------------------------------------------------------


def test_restore_head_restores_chain() -> None:
    chain = HashChain()
    first = chain.stamp(_make_event())
    saved_hash = first["eventHash"]

    # New chain instance, restore the saved head
    chain2 = HashChain()
    chain2.restore_head("tenant-a", saved_hash)
    second = chain2.stamp(_make_event())
    assert second["prevEventHash"] == saved_hash


def test_restore_head_invalid_hex_raises() -> None:
    chain = HashChain()
    with pytest.raises(ValueError, match="invalid sha256"):
        chain.restore_head("tenant-a", "not-valid-hex")


def test_restore_head_empty_string_ok() -> None:
    chain = HashChain()
    # Seeding with empty string resets / initialises cleanly
    chain.restore_head("tenant-a", "")
    stamped = chain.stamp(_make_event())
    assert stamped["prevEventHash"] == ""


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------


def test_verify_pair_valid() -> None:
    chain = HashChain()
    first = chain.stamp(_make_event())
    second = chain.stamp(_make_event())
    assert HashChain.verify_pair(first, second) is True


def test_verify_pair_invalid() -> None:
    chain = HashChain()
    first = chain.stamp(_make_event())
    second = chain.stamp(_make_event())
    # Tamper with the second event's prevEventHash
    tampered = {**second, "prevEventHash": "00" * 32}
    assert HashChain.verify_pair(first, tampered) is False


def test_verify_event_hash_valid() -> None:
    chain = HashChain()
    stamped = chain.stamp(_make_event())
    assert HashChain.verify_event_hash(stamped) is True


def test_verify_event_hash_tampered() -> None:
    chain = HashChain()
    stamped = chain.stamp(_make_event())
    # Alter a field — hash no longer matches
    tampered = {**stamped, "action": "evil.action"}
    assert HashChain.verify_event_hash(tampered) is False


def test_verify_event_hash_no_hash() -> None:
    # Event missing eventHash altogether → False
    assert HashChain.verify_event_hash({"tenantId": "x", "action": "y"}) is False
    # Explicit empty string → False
    assert HashChain.verify_event_hash({"tenantId": "x", "eventHash": ""}) is False


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_head_returns_empty_for_new_scope() -> None:
    chain = HashChain()
    assert chain.head("unseen-scope") == ""


def test_head_returns_latest_hash() -> None:
    chain = HashChain()
    stamped = chain.stamp(_make_event())
    assert chain.head("tenant-a") == stamped["eventHash"]


def test_stamp_missing_tenant_id_raises() -> None:
    chain = HashChain()
    with pytest.raises(ValueError, match="tenantId"):
        chain.stamp({"action": "no-tenant"})


def test_stamp_empty_tenant_id_raises() -> None:
    chain = HashChain()
    with pytest.raises(ValueError, match="tenantId"):
        chain.stamp({"tenantId": "", "action": "x"})


def test_stamp_with_signature_preserves_it() -> None:
    chain = HashChain()
    stamped = chain.stamp(_make_event(signature="sig_abc"))
    assert stamped.get("signature") == "sig_abc"


def test_stamp_without_signature_omits_key() -> None:
    chain = HashChain()
    stamped = chain.stamp(_make_event())
    assert "signature" not in stamped


def test_thread_safety() -> None:
    """Two threads emit 50 events each to the same scope without data races."""
    chain = HashChain()
    errors: list[Exception] = []

    def emit(n: int) -> None:
        try:
            for i in range(n):
                stamped = chain.stamp(_make_event("shared-scope", seq=i))
                # Each stamped event must have a valid hash
                assert HashChain.verify_event_hash(stamped), f"hash invalid at seq={i}"
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=emit, args=(50,))
    t2 = threading.Thread(target=emit, args=(50,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Thread errors: {errors}"
