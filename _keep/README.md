# _keep/ — Assets for the v0.1 Python Rewrite

This folder holds every file from the old codebase that is directly reusable in the
Python-only MCP firewall rewrite. Do not modify these files — the rewriter should copy
them into the new package structure and adapt as needed.

## Contents

### `rego/`
OPA policy bundle from the old `cloudmorph-mcp/test-fixtures/bundles/readonly/` path.

- `main.rego` — Readonly policy: allows only list/describe/read actions, blocks writes and deletes.
- `main_test.rego` — OPA unit tests for the above policy (use `opa test rego/ --coverage`).
- `manifest.json` — OPA bundle manifest declaring the rules root.

These files are production-quality and pass `opa test`. Python will call OPA via subprocess
(or WASM) using these same files unchanged. The rewrite should add more policy files alongside
these (cost-cap, prod-protection, data-residency, PII-block, write-action-approval).

### `fixtures/decisions/`
Six JSON fixtures representing real MCP tool_call policy decision payloads. Use as pytest
fixtures to validate the policy engine produces correct allow/block decisions. The `README.md`
inside explains each fixture's intent.

### `schemas/`
JSON Schema (draft-07) definitions for Request, Job, and Approval objects. These were the
old contracts schemas — adapt as needed for the firewall's ToolCallRequest, PolicyDecision,
and AuditEvent shapes.

### `action_verbs/action_verbs.py`
Canonical taxonomy mapping tool names to action classes (read, list, write, delete, etc.).
Copy into the new package as-is. Used by the policy engine to classify tool_calls before
evaluating against policy rules.

### `audit/`
Hash-chain audit log implementation in Python. Directly reusable:

- `chain.py` — SHA-256 hash chain: each event includes the hash of the previous event,
  creating a tamper-evident chain. Port the sink to SQLite (the old code used S3 and stdout).
- `canonical_json.py` — Deterministic JSON serialization (sorted keys, no whitespace).
  Required for reproducible hash computation.
- `emitter.py` — `AuditEmitter` class: accepts events, runs them through the chain, dispatches
  to configured sinks.
- `sinks/buffered.py` — Buffered sink (batches writes for throughput).
- `sinks/stdout.py` — Debug sink (prints to stdout).
- `sinks/__init__.py`, `__init__.py` — Package exports.

Note: The S3 sink from the original was not copied — it assumed AWS credentials and is out of
scope for the self-hosted Docker image. The rewrite should add a SQLite sink as the default.
