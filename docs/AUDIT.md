# Tessera Audit System

Version: v0.1
Date: 2026-05-10

This document covers the Tessera v0.1 audit subsystem: the audit event schema, the `tessera audit verify` command, hash chain integrity guarantees, the SQLite default sink, the migration path to Postgres, and retention guidance.

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

Every event emitted by Tessera conforms to schema version `v0.1`. The full JSON Schema lives at `schemas/audit_event.schema.json`. The sections below describe every field.

### 1.1 Top-level fields

| Field | Type | Always present | Description |
| --- | --- | --- | --- |
| `schemaVersion` | string | yes | Always `"v0.1"` for this release. Consumers must reject events with an unknown version. |
| `eventId` | string | yes | Globally unique event identifier. Format: `evt_` followed by 26 URL-safe characters (base62). |
| `tenantId` | string | yes | Scope/stream identifier. In OSS multi-token mode this is the per-token scope value from `AuthContext.scope`. In dev mode (no auth) this is `TESSERA_DEPLOYMENT_ID`. See Section 4 for how `tenantId` keys the hash chain. |
| `eventType` | string | yes | One of: `decision`, `passthrough`, `startup`, `reload`, `regex_timeout`, `audit_self_check`. See Section 1.2. |
| `occurredAt` | string | yes | ISO 8601 UTC timestamp with microsecond precision. Example: `2026-05-10T18:32:00.123456Z`. |
| `prevEventHash` | string | yes | SHA-256 hex digest of the previous event in the same scope. Empty string `""` for the first event in a scope. |
| `eventHash` | string | yes | SHA-256 hex digest of this event's canonical JSON (RFC 8785 JCS). Unique across all rows. |
| `payload` | object | yes | Event-type-specific body. See Section 1.3. |

### 1.2 Event types

| `eventType` | When emitted | Decision fields present |
| --- | --- | --- |
| `decision` | A `tools/call` was evaluated by the policy engine. | `decision` (enforcement) or `would_decision` (log_only). |
| `passthrough` | A non-`tools/call` method (`tools/list`, `initialize`, `ping`, etc.) was forwarded without evaluation. | Neither. |
| `startup` | Tessera process started. | Neither. |
| `reload` | Policy reload triggered (file-watch or SIGHUP). | Neither. |
| `regex_timeout` | A regex condition exceeded the 100ms per-match limit during policy evaluation. | `decision_error: "regex_timeout"`. |
| `audit_self_check` | Internal integrity verification ran (scheduled or manual). | Neither. |

### 1.3 Payload fields

All payload fields are nested under the top-level `payload` key. Not all fields are present on every event type; the table below notes applicability.

| Field | Type | Applicable event types | Description |
| --- | --- | --- | --- |
| `mode` | string | `decision` | Deployment enforcement mode at the time of the call: `enforcement`, `log_only`, or `observation`. Present on `decision` events; absent on `passthrough` and lifecycle events. |
| `decision` | string | `decision` in `enforcement` mode | The action the engine returned and honored: `allow`, `block`, `log_only`, or `require_approval`. Mutually exclusive with `would_decision`. |
| `would_decision` | string | `decision` in `log_only` mode | The action the engine would have honored if in enforcement. Mutually exclusive with `decision`. In `log_only` the upstream is always called regardless of this value. |
| `policy_id` | string or null | `decision` | The `id` of the matching policy, or `null` when the default action applied (no policy matched). |
| `reason` | string or null | `decision` | The reason string from the matched policy, with `${arg.X}` and `${audit.event_id}` interpolation applied. `null` when no policy matched or no reason was declared. |
| `upstream` | string | `decision`, `passthrough` | The upstream name from `tessera.yaml` that received (or would have received) the forwarded call. |
| `tool_call` | object | `decision` | The tool call as received. Contains `name` (string), `arguments` (object, stored verbatim — see note below), and `_meta` (object, contains `tessera_intent` if present). |
| `principal_id` | string | `decision`, `passthrough` | Caller identity from `AuthContext.principal_id`. `"anonymous"` in dev mode. |
| `request_id` | string | `decision`, `passthrough` | UUID generated per incoming request. Used to correlate a single request across logs and audit. |
| `decision_error` | string or null | `decision` | Non-null when the engine encountered an internal error during evaluation: `"regex_timeout"` if a condition exceeded the 100ms match limit; `"policy_error"` for unexpected evaluation failures. In both cases the condition is treated as false (fail-open for that condition only; other conditions and policies still apply). |

**Note on `arguments` storage.** Tessera v0.1 stores `arguments` verbatim without redaction. DLP-style redaction is a policy concern: use the reference `pii-block.yaml` policy to block calls whose arguments match sensitive patterns before they reach the audit log. Full redaction at emit time is a v0.2 deliverable.

### 1.4 Complete schema example

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
    "would_decision": null,
    "policy_id": "pii-block",
    "reason": "Argument 'query' matches PII pattern (SSN-like). Request blocked.",
    "upstream": "aws",
    "tool_call": {
      "name": "aws_athena_start_query_execution",
      "arguments": {
        "query": "SELECT * FROM users WHERE ssn = '123-45-6789'"
      },
      "_meta": {
        "tessera_intent": {
          "verbs": ["read.search"],
          "purpose": "Look up user record for support ticket."
        }
      }
    },
    "principal_id": "alice",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "decision_error": null
  }
}
```

For a `passthrough` event (no policy evaluation):

```json
{
  "schemaVersion": "v0.1",
  "eventId": "evt_7yLk3nOpRsUvWxYzAbCdEf",
  "tenantId": "alice",
  "eventType": "passthrough",
  "occurredAt": "2026-05-10T18:32:01.000000Z",
  "prevEventHash": "9b8e7d6c5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8",
  "eventHash": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b",
  "payload": {
    "upstream": "aws",
    "principal_id": "alice",
    "request_id": "660f9511-f30c-52e5-b827-557766551111"
  }
}
```

### 1.5 Field presence matrix

| Field path | `decision` (enforcement) | `decision` (log_only) | `decision` (observation) | `passthrough` | `startup` / `reload` |
| --- | --- | --- | --- | --- | --- |
| `payload.mode` | yes | yes | yes | no | no |
| `payload.decision` | yes | no | no | no | no |
| `payload.would_decision` | no | yes | no | no | no |
| `payload.policy_id` | yes | yes | no | no | no |
| `payload.reason` | yes | yes | no | no | no |
| `payload.upstream` | yes | yes | yes | yes | no |
| `payload.tool_call` | yes | yes | yes | no | no |
| `payload.principal_id` | yes | yes | yes | yes | no |
| `payload.request_id` | yes | yes | yes | yes | no |
| `payload.decision_error` | yes (nullable) | yes (nullable) | no | no | no |

---

## 2. SQLite storage schema

The default audit sink writes to a single SQLite database file (default path: `/var/lib/tessera/audit.db`, overridable via `TESSERA_AUDIT_PATH` or `audit.path` in `tessera.yaml`).

### 2.1 Table definition

```sql
CREATE TABLE IF NOT EXISTS audit_events (
  event_id        TEXT    PRIMARY KEY,
  scope           TEXT    NOT NULL,
  seq             INTEGER NOT NULL,
  event_type      TEXT    NOT NULL,
  occurred_at     TEXT    NOT NULL,
  payload_json    TEXT    NOT NULL,
  prev_event_hash TEXT    NOT NULL,
  event_hash      TEXT    NOT NULL UNIQUE,
  schema_version  TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_scope_seq
  ON audit_events (scope, seq);

CREATE INDEX IF NOT EXISTS idx_audit_scope_occurred_at
  ON audit_events (scope, occurred_at);
```

### 2.2 Column descriptions

| Column | Maps to event field | Notes |
| --- | --- | --- |
| `event_id` | `eventId` | Primary key. |
| `scope` | `tenantId` | Groups the hash chain stream. Per-scope chains are independent. |
| `seq` | (internal) | Monotonically increasing integer per scope. Assigned at emit time as `MAX(seq WHERE scope=?)+1`. Starts at 1. The `(scope, seq)` pair is unique. |
| `event_type` | `eventType` | Denormalized for fast filtering without JSON parsing. |
| `occurred_at` | `occurredAt` | Stored as ISO 8601 string for portability. Indexed with `scope` for time-range queries. |
| `payload_json` | Entire event JSON | Full event serialized as RFC 8785 canonical JSON. Reconstruct the complete event object from this column plus the indexed columns. |
| `prev_event_hash` | `prevEventHash` | SHA-256 hex of the preceding event in the same scope. Empty string for scope's first row. |
| `event_hash` | `eventHash` | SHA-256 hex of this row's canonical JSON. Unique constraint catches accidental duplicate emission. |
| `schema_version` | `schemaVersion` | Stored for forward-compatibility scanning. |

### 2.3 PRAGMAs

The SQLite sink applies these PRAGMAs on every new connection:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
```

`WAL` mode allows concurrent readers while a writer is active, which is important for running `tessera audit verify` against a live database without blocking the proxy. `synchronous = NORMAL` provides durability on commit with acceptable performance on typical SSDs.

### 2.4 No migrations in v0.1

Tessera v0.1 has no migration framework. The schema is created once with `CREATE TABLE IF NOT EXISTS`. Future schema changes will be handled by a migration system in v0.2. Do not add columns or indexes to the live table manually; the verifier and emitter operate on the fixed column set above.

---

## 3. tessera audit verify

`tessera audit verify` walks the stored event chain for one or all scopes, recomputes each event hash from its stored `payload_json`, and verifies adjacent `prevEventHash` linkages. It reports any break in chain continuity.

### 3.1 Synopsis

```
tessera audit verify [OPTIONS]
```

### 3.2 Options

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--audit-path PATH` | path | Value from `tessera.yaml` `audit.path`, then `TESSERA_AUDIT_PATH`, then `/var/lib/tessera/audit.db` | Path to the SQLite database file to verify. Use this when running the command against a copy or a non-default path. |
| `--scope TEXT` | string | — | Verify only the named scope (chain stream). Mutually exclusive with `--all`. If neither `--scope` nor `--all` is given, the command defaults to the `default` scope. |
| `--all` | flag | off | Verify every distinct scope present in the database. Produces one result object per scope. Mutually exclusive with `--scope`. |
| `--json` | flag | off | Emit output as JSON (one JSON object per line when `--all` is used). Machine-readable; suitable for cron scripts and monitoring pipelines. |

### 3.3 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Verification passed. Every checked event hash is correct and every `prevEventHash` matches the preceding row's `eventHash`. The chain is intact. |
| `3` | Integrity failure. At least one event hash mismatches or a `prevEventHash` link is broken. The `first_failure` field in the output identifies the first bad event. |

Exit code `2` is reserved for configuration errors (bad `--audit-path`, unreadable file, missing scope). Exit code `1` is reserved for unexpected runtime errors.

### 3.4 Output shape

**Single-scope success (`--json`):**

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

**Single-scope failure (`--json`):**

```json
{
  "scope": "alice",
  "events_checked": 12345,
  "first_event_at": "2026-05-01T00:00:00.000000Z",
  "last_event_at": "2026-05-10T18:00:00.000000Z",
  "ok": false,
  "first_failure": {
    "seq": 9876,
    "event_id": "evt_4xKj2mNpQrStUvWxYzAbCd",
    "kind": "hash_mismatch",
    "expected_event_hash": "9b8e7d6c5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8",
    "computed_event_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  }
}
```

The `kind` field in `first_failure` is one of:

| `kind` | Meaning |
| --- | --- |
| `hash_mismatch` | The stored `event_hash` does not match the SHA-256 recomputed from the stored `payload_json`. The event row was modified after writing. |
| `chain_break` | The stored `prev_event_hash` does not match the `event_hash` of the preceding row in the scope. An event was inserted, deleted, or reordered. |

**Multi-scope with `--all` (`--json`, one JSON object per line):**

```
{"scope":"alice","events_checked":12345,"first_event_at":"2026-05-01T00:00:00.000000Z","last_event_at":"2026-05-10T18:00:00.000000Z","ok":true,"first_failure":null}
{"scope":"ci-shared","events_checked":88,"first_event_at":"2026-05-08T09:00:00.000000Z","last_event_at":"2026-05-10T17:55:00.000000Z","ok":true,"first_failure":null}
```

Exit code is `0` only if every scope passes. If any scope fails, exit code is `3`.

### 3.5 Human-readable output (default, no `--json`)

Without `--json`, the command prints a summary table to stdout:

```
Verifying scope: alice
  Events checked : 12345
  First event    : 2026-05-01T00:00:00.000000Z
  Last event     : 2026-05-10T18:00:00.000000Z
  Chain status   : OK

All scopes verified. No integrity failures.
```

On failure:

```
Verifying scope: alice
  Events checked : 12345
  First event    : 2026-05-01T00:00:00.000000Z
  Last event     : 2026-05-10T18:00:00.000000Z
  Chain status   : FAILED

  First failure:
    seq          : 9876
    event_id     : evt_4xKj2mNpQrStUvWxYzAbCd
    kind         : hash_mismatch
    stored hash  : 9b8e7d6c5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8
    computed hash: ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff

INTEGRITY FAILURE: 1 scope failed verification.
```

Exit code is `3`.

### 3.6 Usage examples

Verify the default scope against a default-path database:

```
tessera audit verify
```

Verify a specific scope:

```
tessera audit verify --scope ci-shared
```

Verify all scopes in a non-default database:

```
tessera audit verify --audit-path /backups/2026-05-10-audit.db --all
```

Verify all scopes and emit machine-readable output for a monitoring script:

```
tessera audit verify --all --json | jq 'select(.ok == false)'
```

Run from cron and alert on exit code 3:

```bash
#!/bin/sh
tessera audit verify --audit-path /var/lib/tessera/audit.db --all --json \
  > /tmp/tessera-verify.json
if [ $? -eq 3 ]; then
  mail -s "Tessera audit chain integrity failure" ops@example.com < /tmp/tessera-verify.json
fi
```

### 3.7 Running verify against a live database

`tessera audit verify` opens the SQLite file in read-only mode. The `WAL` journal mode (applied by the sink at startup) allows a concurrent reader without blocking the proxy's write path. You can safely run `tessera audit verify` against a live database. For production deployments, prefer running against a filesystem-level copy or snapshot to avoid holding a read transaction open for the full duration of a large chain walk.

---

## 4. Hash chain integrity guarantees

### 4.1 Algorithm

Every audit event is hashed using SHA-256 applied to the event's RFC 8785 canonical JSON (JCS) serialization. RFC 8785 defines a deterministic byte sequence for any JSON value (sorted object keys, no insignificant whitespace, specific Unicode escaping rules). The deterministic serialization ensures that the hash is reproducible given the same event data regardless of platform or library version.

The hash computation in `tessera/audit/chain.py`:

1. Take the full event dict (all top-level fields).
2. Serialize to canonical JSON using `tessera/audit/canonical_json.py` (RFC 8785 JCS).
3. Compute SHA-256 of the UTF-8 bytes.
4. Store the resulting 64-character lowercase hex string as `event_hash`.

The `prevEventHash` field is set before hashing, so the previous event's hash is baked into the current event's hash. This means the current hash is a function of the entire chain history up to that point.

### 4.2 Per-scope chains

Tessera maintains an independent hash chain per scope. A scope corresponds to the `tenantId` field and derives from `AuthContext.scope`. In a multi-token deployment:

- Token `alice` (scope `alice`) has its own chain: events 1, 2, 3, ... with each event's `prevEventHash` linking to the prior event in the `alice` stream.
- Token `bob` (scope `bob`) has a separate chain that does not cross-reference `alice`'s chain.
- Tokens sharing a scope (e.g., two CI tokens both configured with `scope: ci-shared`) write into the same chain.

Per-scope isolation means that a break in one scope's chain does not affect verification of another scope. Operators can verify a specific scope without re-reading the entire database.

The `HashChain` object in `tessera/audit/chain.py` maintains a per-scope in-memory head hash, updated atomically on each emit.

### 4.3 Restart survivability

When Tessera starts, the `AuditEmitter` calls `sink.head_hash(scope)` for each active scope to restore the in-memory chain head from the persistent store. This restores the hash chain pointer from the last persisted event before startup. Subsequent events chain off the restored head, so the chain is continuous across process restarts.

The restore path in `tessera/audit/chain.py` (`HashChain.restore_head`):

1. Tessera starts.
2. `AuditEmitter.startup_restore()` calls `sink.head_hash(scope)` for each scope.
3. `HashChain.restore_head(scope, head_hash)` stores the returned hash as the in-memory chain head for that scope.
4. The next emitted event sets `prevEventHash` to that restored value.

If the database is empty for a scope (first-ever event), `sink.head_hash(scope)` returns an empty string. `HashChain` treats an empty-string head as the start-of-chain sentinel, and the first event in that scope has `prevEventHash: ""`.

### 4.4 What the chain does and does not guarantee

**What it guarantees:**

- Any modification to a stored event's `payload_json` is detectable: recomputing the hash will produce a different value.
- Any insertion, deletion, or reordering of events within a scope is detectable via a broken `prevEventHash` link.
- Verification is reproducible: running `tessera audit verify` twice on an unmodified database always produces the same result.

**What it does not guarantee:**

- The chain does not prevent tampering by someone with write access to the SQLite file. It detects tampering after the fact.
- The chain does not protect against a complete database replacement (swapping in a fraudulent database that has internally consistent hashes). For that protection, take periodic `event_hash` checkpoints out-of-band (e.g., write the current `head_hash` to a separate immutable log).
- The chain does not cover events that were never emitted (e.g., a crash before `emit()` returned). Missing events create no detectable gap in the chain.

### 4.5 SHA-256 collision resistance

SHA-256 provides 128-bit second-preimage resistance. For the purposes of audit logging in v0.1, SHA-256 is appropriate. A v0.2 upgrade path to SHA-3 or BLAKE3 would require migrating stored hashes.

---

## 5. SQLite to Postgres migration

### 5.1 v0.1 scope

Tessera v0.1 ships only the SQLite sink (`tessera.audit.sinks.sqlite:SqliteSink`). The `AuditSink` Protocol is designed so that a Postgres sink can be dropped in without any proxy or chain code changes. The Postgres sink itself (`tessera.audit.sinks.postgres:PostgresSink`) is a v0.2 deliverable.

### 5.2 Migration path (manual export and import)

When the Postgres sink ships in v0.2, the migration path from an existing SQLite database is:

**Step 1. Stop Tessera or drain traffic.**

Stop the Tessera process or redirect traffic away so that no new events are written to SQLite during the migration. This avoids a gap between the last exported event and the first event written to Postgres.

**Step 2. Export events from SQLite.**

Use the SQLite CLI or a Python script to export rows as newline-delimited JSON:

```bash
sqlite3 /var/lib/tessera/audit.db \
  "SELECT payload_json FROM audit_events ORDER BY scope, seq" \
  > /tmp/tessera-export.ndjson
```

Each line of `payload_json` is the complete canonical event JSON including all chain fields.

**Step 3. Verify the chain before migration.**

Before writing to Postgres, confirm the SQLite chain is intact:

```
tessera audit verify --audit-path /var/lib/tessera/audit.db --all
```

If verification fails, do not import to Postgres without first understanding and resolving the integrity failure. Importing a broken chain to Postgres preserves the break.

**Step 4. Import into Postgres.**

With the Postgres sink configured (`TESSERA_AUDIT_SINK=tessera.audit.sinks.postgres:PostgresSink`), run the v0.2 import command (name TBD for v0.2):

```
tessera audit import --source /tmp/tessera-export.ndjson
```

The import command will reconstruct `scope`, `seq`, `event_type`, `occurred_at`, `prev_event_hash`, `event_hash`, and `schema_version` from each event's `payload_json`, and write them into the Postgres table. It verifies the chain as it imports.

**Step 5. Verify the Postgres chain.**

After import, run verify against the new Postgres database to confirm no corruption occurred during import.

**Step 6. Switch Tessera to Postgres.**

Update `TESSERA_AUDIT_SINK` and restart Tessera. The Postgres sink's `head_hash(scope)` will return the last imported event's hash, and the chain continues from there.

### 5.3 Dual-write option (not in v0.1)

A dual-write option (writing to both SQLite and Postgres during a transition window) is not available in v0.1. It will be considered for v0.2 alongside the Postgres sink itself.

### 5.4 Schema equivalence

The Postgres table will use the same logical schema as SQLite. Column types change (`TEXT` becomes `VARCHAR` or `TEXT`, `INTEGER` becomes `BIGINT`), and the Postgres table will add `created_at TIMESTAMPTZ DEFAULT NOW()` for database-level auditing, but the data columns map 1:1.

### 5.5 No automatic migration

There is no automated SQLite-to-Postgres migration in v0.1. The full migration story — including tooling, documentation, and the Postgres sink implementation — is a v0.2 deliverable. Operators who need Postgres before v0.2 ships can implement a custom `AuditSink` by following the Protocol in `tessera/audit/sinks/base.py` and pointing `TESSERA_AUDIT_SINK` at their implementation.

---

## 6. Retention guidance

### 6.1 Default behavior: keep forever

Tessera v0.1 applies no automatic retention policy. Every emitted event is kept in the SQLite database indefinitely. The database file grows as events accumulate. There is no built-in rotation, TTL, or archival mechanism.

This is intentional. Audit logs are evidence. Deleting them requires a deliberate operator decision. Tessera does not make that decision for you.

### 6.2 Operator-managed rotation

Operators who need to bound database size should implement rotation outside Tessera. The key constraint is:

**The hash chain stays intact as long as the tail (most recent) row per scope survives the rotation.**

If you delete old rows, the chain between the oldest surviving row and any newer rows is logically intact. The `prevEventHash` of the oldest surviving row still points to the deleted row's `event_hash`, which means `tessera audit verify` will report a chain break at that row (because the previous event no longer exists). This is expected behavior when you intentionally truncate old history.

**Recommended rotation pattern:**

1. Before rotating, export the rows to be deleted to an archive file (cold storage, S3, a separate database, etc.).
2. Note the `event_hash` of the last event in the export (the newest row among those being deleted).
3. If you want the live database's chain to remain verifiable going forward, keep one sentinel row per scope that bridges the gap. A sentinel row has `prevEventHash` equal to the exported tail's `event_hash`, and its own hash reflects that. The v0.2 `tessera audit reset` command will automate this.
4. Delete the old rows.
5. Run `tessera audit verify` to confirm the remaining chain is intact.

A simpler approach: truncate and restart. Archive the old database file to cold storage, start Tessera with a fresh database. The new chain begins with `prevEventHash: ""` for the first event. The old chain is preserved in the archive and can be verified independently.

### 6.3 Disk sizing guidance

Event size varies by payload. A typical `decision` event with a modest `arguments` object serializes to 600-900 bytes of canonical JSON. At 200 rps (Tessera's target throughput) sustained:

| Throughput | Events per day | Approximate daily growth |
| --- | --- | --- |
| 10 rps | 864,000 | ~600 MB |
| 50 rps | 4,320,000 | ~3 GB |
| 200 rps | 17,280,000 | ~12 GB |

SQLite WAL mode keeps the main file size stable during writes; the WAL file grows during high write periods and checkpoints periodically. Allow 2-3x headroom above the main file size for the WAL.

For high-throughput deployments, plan for rotation after no more than 30-60 days of data, or use the Postgres sink (v0.2) which supports tablespace management and native partitioning.

### 6.4 tessera audit reset (v0.2)

`tessera audit reset` is a v0.2 deliverable. It will:

- Archive events older than a configurable cutoff to a specified path.
- Insert a bridge row per scope that records the archived tail's `event_hash` as its `prevEventHash`, making the live chain verifiable without the archived data.
- Print the `event_hash` of each bridge row so operators can record it out-of-band.

Until v0.2, operators manage rotation manually as described in Section 6.2.

### 6.5 Compliance considerations

Tessera does not enforce any specific retention period. Consult your applicable regulations (GDPR, HIPAA, SOC 2, etc.) to determine the minimum and maximum retention periods for your deployment's audit logs. The hash chain provides tamper-evidence for the retained period; it does not substitute for a formal chain-of-custody process for regulated environments.
