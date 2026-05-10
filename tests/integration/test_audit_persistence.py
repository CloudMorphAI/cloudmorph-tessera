"""Integration test: hash chain head restored on sink restart."""

from __future__ import annotations

from pathlib import Path

from tessera.audit.chain import HashChain
from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.sqlite import SqliteSink


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
