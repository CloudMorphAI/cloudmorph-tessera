# Tessera Audit System

Version: v0.1 — terse reference for debugging and integration.

---

## Table of Contents

1. [Audit event schema](#1-audit-event-schema)
2. [SQLite storage schema](#2-sqlite-storage-schema)
3. [tessera audit verify](#3-tessera-audit-verify)
4. [Hash chain integrity guarantees](#4-hash-chain-integrity-guarantees)
5. [SQLite to Postgres migration](#5-sqlite-to-postgres-migration)
6. [Retention guidance](#6-retention-guidance)

---

## 1. Audit event schema

Full JSON Schema: [`schemas/audit_event.schema.json`](../schemas/audit_event.schema.json).

### 1.1 Top-level fields

| Field | Type | Description |
| --- | --- | --- |
| `schemaVersion` | string | Always `"v0.1"`. Reject events with unknown versions. |
| `eventId` | string | Globally unique. Format: `evt_` + 26 base62 characters. |
| `tenantId` | string | Chain stream identifier. Derives from `AuthContext.scope`; `TESSERA_DEPLOYMENT_ID` in dev mode. |
| `eventType` | string | One of: `decision`, `passthrough`, `startup`, `reload`, `regex_timeout`, `audit_self_check`. |
| `occurredAt` | string | ISO 8601 UTC, microsecond precision. Example: `2026-05-10T18:32:00.123456Z`. |
| `prevEventHash` | string | SHA-256 hex of the previous event in the same scope. `""` for the first event in a scope. |
| `eventHash` | string | SHA-256 hex of this event's RFC 8785 canonical JSON. |
| `payload` | object | Event-type-specific body. See §1.2. |

### 1.2 Payload fields

| Field | Applicable event types | Description |
| --- | --- | --- |
| `mode` | `decision` | `enforcement`, `log_only`, or `observation`. |
| `decision` | `decision` (enforcement) | Engine action honored: `allow`, `block`, `log_only`, `require_approval`. |
| `would_decision` | `decision` (log_only) | Action engine would have honored; upstream is always called regardless. |
| `policy_id` | `decision` | Matched policy `id`, or `null` when default action applied. |
| `reason` | `decision` | Reason string with `${arg.X}` / `${audit.event_id}` interpolation. `null` if none. |
| `upstream` | `decision`, `passthrough` | Upstream name from `tessera.yaml`. |
| `tool_call` | `decision` | Object with `name`, `arguments` (verbatim, no redaction in v0.1), `_meta`. |
| `principal_id` | `decision`, `passthrough` | `AuthContext.principal_id`. `"anonymous"` in dev mode. |
| `request_id` | `decision`, `passthrough` | UUID per incoming request. |
| `decision_error` | `decision` | `"regex_timeout"` or `"policy_error"` on evaluation failure; `null` otherwise. |

**Arguments are stored verbatim.** DLP redaction is a policy concern in v0.1; use `pii-block.yaml` to block calls before they reach the log. Full redaction at emit time is a v0.2 deliverable.

### 1.3 Schema example

```json
{
  "schemaVersion": "v0.1",
  "eventId": "evt_4xKj2mNpQrStUvWxYzAbCd",
  "tenantId": "alice",
  "eventType": "decision",
  "occurredAt": "2026-05-10T18:32:00.123456Z",
  "prevEventHash": "a3f1c9e2b4d67890abcdef1234567890abcdef1234567890abcdef1234567890",
  "eventHash": "9b8e7d6c5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8",
  "payload": {
    "mode": "enforcement",
    "decision": "block",
    "policy_id": "pii-block",
    "reason": "Argument 'query' matches PII pattern. Request blocked.",
    "upstream": "aws",
    "tool_call": {
      "name": "aws_athena_start_query_execution",
      "arguments": { "query": "SELECT * FROM users WHERE ssn = '123-45-6789'" },
      "_meta": { "tessera_intent": { "verbs": ["read.search"] } }
    },
    "principal_id": "alice",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "decision_error": null
  }
}
```

---

## 2. SQLite storage schema

Default path: `/var/lib/tessera/audit.db`. Override via `TESSERA_AUDIT_PATH` or `audit.path` in `tessera.yaml`.

Full table DDL and column descriptions live in [`tessera/audit/sinks/sqlite.py`](../tessera/audit/sinks/sqlite.py). Key points:

- Primary key: `event_id`. Unique index on `(scope, seq)` and `event_hash`.
- `payload_json` stores the full RFC 8785 canonical event JSON. All other indexed columns are denormalized from it.
- PRAGMAs applied on every connection: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`. WAL allows concurrent reads during live writes — safe to run `tessera audit verify` against a live database.
- v0.1 has no migration framework. Do not add columns manually; the verifier and emitter operate on the fixed column set.

---

## 3. tessera audit verify

Walks the stored event chain, recomputes each event hash from `payload_json`, and verifies `prevEventHash` linkages.

### 3.1 Synopsis

```
tessera audit verify [OPTIONS]
```

### 3.2 Flags

| Flag | Default | Description |
| --- | --- | --- |
| `--audit-path PATH` | `tessera.yaml` → `TESSERA_AUDIT_PATH` → `/var/lib/tessera/audit.db` | SQLite file to verify. |
| `--scope TEXT` | `default` | Verify one named scope. Mutually exclusive with `--all`. |
| `--all` | off | Verify every scope in the database. |
| `--json` | off | Emit NDJSON (one object per scope). Suitable for monitoring pipelines. |

### 3.3 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | All checked hashes correct; all `prevEventHash` links intact. |
| `2` | Configuration error (bad path, unreadable file, missing scope). |
| `3` | Integrity failure — at least one hash mismatch or chain break. |
| `1` | Unexpected runtime error. |

### 3.4 Example output

**`--json` success:**

```json
{
  "scope": "alice",
  "events_checked": 12345,
  "first_event_at": "2026-05-01T00:00:00.000000Z",
  "last_event_at": "2026-05-10T18:00:00.000000Z",
  "ok": true,
  "first_failure": null
}
```

**`--json` failure** — `first_failure.kind` is `hash_mismatch` (stored hash ≠ recomputed hash) or `chain_break` (`prevEventHash` ≠ preceding row's `eventHash`):

```json
{
  "scope": "alice",
  "events_checked": 12345,
  "ok": false,
  "first_failure": {
    "seq": 9876,
    "event_id": "evt_4xKj2mNpQrStUvWxYzAbCd",
    "kind": "hash_mismatch",
    "expected_event_hash": "9b8e7d6c...",
    "computed_event_hash": "ffffffff..."
  }
}
```

**Typical invocations:**

```bash
# Verify default scope
tessera audit verify

# Verify all scopes, filter failures in monitoring pipeline
tessera audit verify --all --json | jq 'select(.ok == false)'

# Non-default path
tessera audit verify --audit-path /backups/2026-05-10-audit.db --all
```

---

## 4. Hash chain integrity guarantees

- **Algorithm.** Each event is hashed as `SHA-256(RFC 8785 canonical JSON)`. RFC 8785 (JCS) produces a deterministic byte sequence regardless of platform or library. Spec: <https://www.rfc-editor.org/rfc/rfc8785>. Implementation: `tessera/audit/canonical_json.py`.
- **Chaining.** `prevEventHash` is embedded in the event before hashing, so `eventHash` commits the full chain history up to that point. Any modification to a stored event or any insertion, deletion, or reordering within a scope is detectable via `tessera audit verify`.
- **Per-scope isolation.** Each `tenantId` value keys an independent chain. Tokens sharing a `scope` write into the same chain; separate scopes are independent. A break in one scope does not affect verification of another.
- **Restart survivability.** On startup, `AuditEmitter` calls `sink.head_hash(scope)` to restore the in-memory chain head from the last persisted event. The chain is continuous across process restarts.
- **Limits.** The chain detects after-the-fact tampering; it does not prevent it. It does not protect against a full database swap (internally consistent fraudulent database). It does not detect events that were never emitted (pre-crash drops). For stronger guarantees, checkpoint `head_hash` values to an immutable out-of-band log periodically.

---

## 5. SQLite to Postgres migration

v0.2 will ship `tessera.audit.sinks.postgres:PostgresSink`. The `AuditSink` Protocol (`tessera/audit/sinks/base.py`) is designed for drop-in sink replacement.

**Until then:** export to JSONL via `iter_events` (or `SELECT payload_json FROM audit_events ORDER BY scope, seq`) and replay against the new sink using the v0.2 `tessera audit import` command. Verify the SQLite chain before export; importing a broken chain preserves the break. After import, run `tessera audit verify` against the Postgres database.

Operators who need Postgres before v0.2 can implement a custom `AuditSink` against the Protocol in `tessera/audit/sinks/base.py`.

---

## 6. Retention guidance

Tessera v0.1 applies no automatic retention policy. Events accumulate indefinitely. Deletion requires a deliberate operator decision.

A typical `decision` event serializes to 600–900 bytes. At sustained load:

| Throughput | Events/day | Approx. daily growth |
| --- | --- | --- |
| 10 rps | 864 K | ~600 MB |
| 50 rps | 4.3 M | ~3 GB |
| 200 rps | 17.3 M | ~12 GB |

Allow 2–3× headroom above the main file size for the WAL. Plan rotation every 30–60 days at high throughput, or use the Postgres sink (v0.2) which supports native partitioning.

**Simplest rotation:** archive the old database file, start Tessera with a fresh database. The new chain begins with `prevEventHash: ""`. The archived chain can be verified independently at any time.

**Cron verification example:**

```bash
#!/bin/sh
tessera audit verify --audit-path /var/lib/tessera/audit.db --all --json \
  > /tmp/tessera-verify.json
if [ $? -eq 3 ]; then
  mail -s "Tessera audit chain integrity failure" ops@example.com \
    < /tmp/tessera-verify.json
fi
```

`tessera audit reset` (automated archival with bridge-row insertion) is a v0.2 deliverable.
