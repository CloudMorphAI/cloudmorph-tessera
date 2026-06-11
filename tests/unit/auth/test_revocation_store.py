"""Unit tests for SQLite-backed RevocationStore."""

from __future__ import annotations

import asyncio

import pytest

from tessera.auth.oauth_rs import InMemoryRevocationStore, SqliteRevocationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine using a fresh event loop that does NOT close the loop.

    asyncio.run() always closes the loop it creates, which breaks subsequent
    tests that use the deprecated asyncio.get_event_loop() pattern.  We avoid
    that by creating and explicitly managing a loop without closing it.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# InMemoryRevocationStore (baseline)
# ---------------------------------------------------------------------------


def test_in_memory_revoke_and_check() -> None:
    store = InMemoryRevocationStore()
    _run(store.revoke("jti-abc"))
    assert _run(store.is_revoked("jti-abc"))


def test_in_memory_unknown_jti_not_revoked() -> None:
    store = InMemoryRevocationStore()
    assert not _run(store.is_revoked("unknown"))


# ---------------------------------------------------------------------------
# SqliteRevocationStore
# ---------------------------------------------------------------------------


@pytest.fixture()
def sqlite_store(tmp_path):
    return SqliteRevocationStore(tmp_path / "revocation.db")


def test_sqlite_revoke_persists_across_reopen(tmp_path) -> None:
    """Revoke a JTI, open a second store instance on the same file, confirm it's still revoked."""
    db_path = tmp_path / "rev.db"

    store1 = SqliteRevocationStore(db_path)
    _run(store1.revoke("jti-persistent"))

    # Re-open without going through module singleton.
    store2 = SqliteRevocationStore(db_path)
    assert _run(store2.is_revoked("jti-persistent"))


def test_sqlite_unknown_jti_not_revoked(sqlite_store) -> None:
    assert not _run(sqlite_store.is_revoked("never-seen"))


def test_sqlite_revoke_idempotent(sqlite_store) -> None:
    """Double-revoke must not raise (INSERT OR IGNORE)."""
    _run(sqlite_store.revoke("jti-dup"))
    _run(sqlite_store.revoke("jti-dup"))
    assert _run(sqlite_store.is_revoked("jti-dup"))


def test_sqlite_multiple_jtis(sqlite_store) -> None:
    _run(sqlite_store.revoke("a"))
    _run(sqlite_store.revoke("b"))
    assert _run(sqlite_store.is_revoked("a"))
    assert _run(sqlite_store.is_revoked("b"))
    assert not _run(sqlite_store.is_revoked("c"))
