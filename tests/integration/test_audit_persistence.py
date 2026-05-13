"""Integration test: hash chain head restored on sink restart."""

from __future__ import annotations

from pathlib import Path

from tessera.audit.chain import HashChain
from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.sqlite import SqliteSink
from tessera.audit.verifier import verify_chain


def test_restart_restores_chain_head(tmp_path: Path) -> None:
    """Emit events, close sink, reopen sink, emit more events.

    The chain head of the new sink must match the last event of the old sink,
    and all events form a valid chain.
    """
    db_path = tmp_path / "audit_persistence.db"
    scope = "test-scope"

    # --- First run ---
    sink1 = SqliteSink(path=db_path)
    chain1 = HashChain()
    emitter1 = AuditEmitter(tenant_id=scope, sinks=[sink1], hash_chain=chain1)

    emitter1.emit("decision", payload={"run": 1, "seq": 1})
    emitter1.emit("decision", payload={"run": 1, "seq": 2})
    evt3 = emitter1.emit("decision", payload={"run": 1, "seq": 3})

    last_hash_run1 = evt3["eventHash"]
    sink1_head = sink1.head_hash(scope)

    # Sanity: sink head matches last emitted event
    assert sink1_head == last_hash_run1

    sink1.close()

    # --- Second run (simulates restart) ---
    sink2 = SqliteSink(path=db_path)
    chain2 = HashChain()

    # Restore head from persisted state
    persisted_head = sink2.head_hash(scope)
    assert persisted_head == last_hash_run1, "head_hash after reopen must match last run's final hash"

    chain2.restore_head(scope, persisted_head)

    emitter2 = AuditEmitter(tenant_id=scope, sinks=[sink2], hash_chain=chain2)
    evt4 = emitter2.emit("decision", payload={"run": 2, "seq": 4})
    evt5 = emitter2.emit("decision", payload={"run": 2, "seq": 5})

    # The fourth event's prevEventHash must equal the third event's eventHash
    assert evt4["prevEventHash"] == last_hash_run1, (
        "Run-2 first event prevEventHash must chain to run-1 last event"
    )
    # The fifth event must chain from the fourth
    assert evt5["prevEventHash"] == evt4["eventHash"]

    sink2.close()

    # --- Verify full chain by walking events ---
    sink3 = SqliteSink(path=db_path)
    all_events = list(sink3.iter_events(scope))
    assert len(all_events) == 5

    # Verify each event's hash is self-consistent
    for evt in all_events:
        assert HashChain.verify_event_hash(evt), f"event hash mismatch for {evt['eventId']}"

    # Verify chain linkage
    for i in range(1, len(all_events)):
        assert HashChain.verify_pair(all_events[i - 1], all_events[i]), (
            f"chain broken between events {i - 1} and {i}"
        )

    sink3.close()


def test_restart_preserves_chain(tmp_path: Path) -> None:
    """A-4-6: iter_scopes() + restore_head() code path verifier.

    1. Creates SqliteSink + HashChain + emits 3 events
    2. Closes sink
    3. Re-opens sink + creates fresh HashChain
    4. Uses iter_scopes() to restore head for every scope
    5. Emits 4th event
    6. Runs verify_chain over all 4 events and asserts ok: True
    """
    db_path = tmp_path / "restart_preserves.db"
    scope = "persist-scope"

    # --- Phase 1: emit 3 events ---
    sink1 = SqliteSink(path=db_path)
    chain1 = HashChain()
    emitter1 = AuditEmitter(tenant_id=scope, sinks=[sink1], hash_chain=chain1)

    emitter1.emit("startup", payload={"phase": 1, "n": 1})
    emitter1.emit("decision", payload={"phase": 1, "n": 2})
    evt3 = emitter1.emit("passthrough", payload={"phase": 1, "n": 3})
    final_hash_phase1 = evt3["eventHash"]
    sink1.close()

    # --- Phase 2: simulate process restart ---
    sink2 = SqliteSink(path=db_path)
    chain2 = HashChain()

    # This is the lifespan code path: iterate scopes, restore heads.
    for scope_name in sink2.iter_scopes():
        head = sink2.head_hash(scope_name)
        if head:
            chain2.restore_head(scope_name, head)

    # Confirm the head was correctly restored
    assert chain2.head(scope) == final_hash_phase1

    # Emit 4th event
    emitter2 = AuditEmitter(tenant_id=scope, sinks=[sink2], hash_chain=chain2)
    evt4 = emitter2.emit("decision", payload={"phase": 2, "n": 4})

    # 4th event must chain from 3rd
    assert evt4["prevEventHash"] == final_hash_phase1

    sink2.close()

    # --- Verify all 4 events form a valid chain ---
    sink3 = SqliteSink(path=db_path)
    result = verify_chain(sink3, scope)
    assert result["ok"] is True, f"verify_chain failed: {result}"
    assert result["events_checked"] == 4
    sink3.close()
