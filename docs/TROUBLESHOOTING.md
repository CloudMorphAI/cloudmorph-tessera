# Tessera v0.1 — Troubleshooting Guide

This guide covers the most common operational issues with Tessera v0.1, their symptoms,
and step-by-step remediation. For configuration reference see `docs/CONFIGURATION.md`;
for audit chain details see `docs/AUDIT.md`.

---

## Table of contents

1. [Policy not applying](#1-policy-not-applying)
2. [Intent missing or rejected](#2-intent-missing-or-rejected)
3. [Audit verify failures](#3-audit-verify-failures)
4. [Docker volume permission errors](#4-docker-volume-permission-errors)
5. [Regex timeout warnings](#5-regex-timeout-warnings)
6. [/metrics returns 401](#6-metrics-returns-401)
7. [healthz shows errored policies](#7-healthz-shows-errored-policies)

---

## 1. Policy not applying

### Symptoms

- A tool call you expected to be blocked goes through without an error.
- No block decision appears in the audit log.
- The response has `X-Tessera-Decision: would_block` but the upstream was still reached.

### Cause A — deployment is in `log_only` mode

`tessera init` scaffolds every new deployment with `policies.mode: log_only`. In this
mode the engine runs, a decision is computed, but **the upstream is always called and
the response is always returned**. Blocking is never enforced. The response headers
tell you what *would* happen in enforcement mode.

**Diagnosis.** Check the `X-Tessera-Mode` response header on any proxied request:

```
X-Tessera-Mode: log_only
X-Tessera-Decision: would_block
X-Tessera-Policy-Id: prod-protection
X-Tessera-Reason: Write operations are blocked on production upstreams
```

Or query `/healthz` and look at the `policies.mode` field reported by your config:

```bash
curl -s http://localhost:8080/healthz | jq
```

```json
{
  "status": "ok",
  "policy_state": {
    "loaded": 3,
    "errored": []
  }
}
```

Note: `/healthz` does not directly echo the mode string. Check your `tessera.yaml`
or the startup log line `policies.mode=log_only` for confirmation.

**Remediation.** Once you have reviewed audit output and are satisfied with your
policy tuning, flip the mode:

```yaml
# tessera.yaml
policies:
  mode: enforcement   # was: log_only
```

`mode` is not reloadable via SIGHUP; restart the process or container:

```bash
docker restart tessera
# or
kill -TERM <pid>  # SIGTERM drains then exits; supervisor restarts
```

After restarting, verify enforcement is active:

```bash
curl -s http://localhost:8080/healthz -H "Authorization: Bearer <token>" | jq
```

The `X-Tessera-Mode` header on subsequent requests will change to `enforcement`.

---

### Cause B — policy file failed to load (silently skipped)

If a policy file has a syntax error or a ReDoS pattern was rejected at reload time,
that policy is dropped from the active set. The remaining loaded policies still
evaluate, but the errored policy is silently absent.

**Diagnosis.** Check `/healthz` for non-empty `errored`:

```bash
curl -s http://localhost:8080/healthz | jq '.policy_state'
```

```json
{
  "loaded": 2,
  "errored": [
    {
      "path": "/etc/tessera/policies/prod-protection.yaml",
      "error": "regex_potential_redos: pattern '(a+)+$' in condition 2 failed corpus test"
    }
  ]
}
```

See [section 7](#7-healthz-shows-errored-policies) for full remediation steps.

---

### Cause C — policy has `require_intent: true` and the agent sends no intent

A policy with `match.require_intent: true` is **skipped** for intent-blind agents
(off-the-shelf MCP clients that do not populate `_meta.tessera_intent`). The policy
is not evaluated; it neither allows nor blocks the call.

**Diagnosis.** Enable `DEBUG` logging to see per-request engine decisions:

```yaml
# tessera.yaml (or set env var TESSERA_LOG_LEVEL=DEBUG)
log_level: DEBUG
```

Look for log lines like:

```
DEBUG tessera.policy.engine policy_id=prod-protection skipped reason=require_intent_not_met
```

**Remediation options:**

- Remove `require_intent: true` from the policy if it should apply to all agents.
- Set `intent.required: true` globally to block intent-blind agents entirely (strict
  mode — see [section 2](#2-intent-missing-or-rejected)).
- Migrate your agent to populate `_meta.<meta_key>` with a valid intent block.

---

### Cause D — deployment is in `observation` mode

In `observation` mode the policy engine is **not called at all**. All requests pass
through. This mode is for traffic baselining only.

```
X-Tessera-Mode: observation
```

Audit events are still written but contain no `decision` or `would_decision` field.
Change `mode` to `log_only` or `enforcement` and restart.

---

## 2. Intent missing or rejected

### Symptoms

- Requests are blocked with `intent_required` reason even though you expect them to
  pass.
- Audit rows show `decision: block` with `reason: intent_required`.
- Some agents pass through fine; others are blocked.

### Background

Tessera has two separate intent-related controls:

| Config field | Default | Effect |
|---|---|---|
| `intent.required` | `false` | Global: blocks *all* calls without intent when `true` |
| `match.require_intent` (per policy) | absent | Per-policy: skips that policy for intent-blind calls |

### Cause A — `intent.required: true` is set globally

Any call that does not carry a valid `_meta.<meta_key>` block is rejected before
policy evaluation with `reason: intent_required`.

**Diagnosis.**

```bash
# Check tessera.yaml
grep -A2 "^intent:" tessera.yaml
```

```yaml
intent:
  meta_key: tessera_intent
  required: true
```

```bash
# Confirm in audit log
tessera audit verify --all --json | jq '.[] | select(.decision == "block")'
```

**Remediation.**

If you want to allow intent-blind agents (vanilla Cursor, Claude Desktop, Windsurf),
set `required: false`:

```yaml
intent:
  meta_key: tessera_intent
  required: false
```

Restart after changing this field (not SIGHUP-reloadable).

---

### Cause B — `meta_key` mismatch

The agent populates `_meta.my_intent` but Tessera is configured to read
`_meta.tessera_intent`. The intent block is present in the request but Tessera
cannot find it under the configured key.

**Diagnosis.** Enable `DEBUG` logging and look for:

```
DEBUG tessera.intent meta_key=tessera_intent found=false
```

**Remediation.** Align the `meta_key` config with whatever key your agent writes:

```yaml
intent:
  meta_key: my_intent   # must match what the agent sends
```

---

### Cause C — malformed intent block

Tessera validates the intent block: `verbs` must be a non-empty list of known action
verbs; `purpose` must be a string not exceeding 1024 characters.

Common mistakes:

```jsonc
// Wrong: verbs is a string, not a list
"tessera_intent": { "verbs": "read", "purpose": "..." }

// Wrong: unknown verb
"tessera_intent": { "verbs": ["query"], "purpose": "..." }

// Wrong: purpose too long (> 1024 chars)
"tessera_intent": { "verbs": ["read"], "purpose": "<1025 char string>" }
```

**Diagnosis.** Audit log records the validation failure reason. With `DEBUG` logging:

```
DEBUG tessera.intent validation_failed reason=verbs_not_list
DEBUG tessera.intent validation_failed reason=unknown_verb verb=query
DEBUG tessera.intent validation_failed reason=purpose_too_long length=2048
```

**Remediation.** Fix the agent's intent payload. Valid verbs are documented in
`tessera/policy/action_verbs.py` (e.g., `read`, `write`, `delete`, `list`,
`create`, `update`, `execute`, `audit`, `export`). Custom verb mappings can be
added in `policies/_action_verbs.yaml`.

---

## 3. Audit verify failures

### Symptoms

- `tessera audit verify` exits with code 2 or 3.
- Output includes `hash_mismatch` or `chain_break` errors.
- CI/compliance checks that run `tessera audit verify` start failing.

### Running the verifier

```bash
# Verify all scopes, output JSON
tessera audit verify --all --json

# Verify a single scope
tessera audit verify --scope alice --json

# Verify against a non-default DB path
tessera audit verify --all --json --audit-path /var/lib/tessera/audit.db
```

Exit codes: `0` = chain intact, `2` = integrity failure found, `3` = DB not found
or unreadable.

---

### Failure kind: `hash_mismatch`

```json
{
  "scope": "alice",
  "status": "failed",
  "kind": "hash_mismatch",
  "seq": 42,
  "event_id": "01HXYZ...",
  "message": "stored hash does not match recomputed hash of event body"
}
```

**Meaning.** The canonical JSON of event at sequence 42 was recomputed and its SHA-256
hash does not match the `event_hash` stored in the database. The event body was
modified after it was written — either directly in the SQLite file or via a bug in a
custom `AuditSink` implementation.

**Remediation steps.**

1. Do not modify the audit DB while investigating.
2. Check if a custom `TESSERA_AUDIT_SINK` plugin is in use; a plugin that rewrites
   event fields after emission will cause `hash_mismatch` on verify.
3. Check for direct SQLite manipulation (backups, migrations, manual edits).
4. If the DB was migrated between hosts, confirm the file was transferred intact
   (no partial write, no encoding conversion).
5. If the mismatch is isolated to a known-bad window and you need the chain healthy
   going forward, document the incident and rotate to a new scope. **Do not delete
   the affected rows** — deletion causes `chain_break` on rows that follow.

---

### Failure kind: `chain_break`

```json
{
  "scope": "alice",
  "status": "failed",
  "kind": "chain_break",
  "seq": 43,
  "message": "prev_hash at seq=43 does not match event_hash at seq=42"
}
```

**Meaning.** The `prev_hash` pointer on event 43 does not match the computed hash of
event 42. This indicates that rows were deleted, reordered, or inserted between
sequence 42 and 43.

**Common causes:**

- Rows deleted from the `audit_events` table manually.
- A bulk-delete rotation script that removed rows from the middle of the chain.
  (v0.1 retains all rows by default; user-managed rotation must preserve the head
  row of each scope. Deleting any non-tail row breaks the chain for all rows that
  follow.)
- Tessera was connected to a restored backup that was older than the current chain
  and new events were appended on top of the old tail.

**Remediation.**

- Never delete rows from the middle of a chain. If you must truncate for storage
  reasons, delete only the oldest rows *and* update the chain head to the new
  earliest row. A full migration story is a v0.2 deliverable.
- If a break occurred due to a bad backup restore, document the break boundary in
  your compliance records and start a new scope going forward.

---

## 4. Docker volume permission errors

### Symptoms

- Container exits immediately at startup with a permission error.
- Audit log lines never appear; `audit.db` is never created.
- Logs show: `PermissionError: [Errno 13] Permission denied: '/var/lib/tessera/audit.db'`
- `/healthz` returns 503 or the container never becomes healthy.

### Background

The Tessera container runs as user `tessera` (uid 10001, gid 10001). The audit
database is written to `/var/lib/tessera/`. When you mount an external Docker volume
or a bind-mount host directory at that path, the mounted directory must be owned and
writable by uid 10001.

### Diagnosis

```bash
# Check the ownership of the mount source on the host
ls -la /path/to/host/tessera-data/

# Check inside a running (or failed) container
docker run --rm \
  -v tessera_audit:/var/lib/tessera \
  --entrypoint id \
  ghcr.io/cloudmorph-ai/tessera:0.1.0

docker run --rm \
  -v tessera_audit:/var/lib/tessera \
  --entrypoint ls \
  ghcr.io/cloudmorph-ai/tessera:0.1.0 -la /var/lib/tessera
```

### Remediation — named Docker volume

Named volumes are normally managed by Docker and owned by root inside the volume.
Fix ownership before the Tessera container starts:

```bash
# One-off fix: run a temporary privileged container to chown the volume
docker run --rm \
  -v tessera_audit:/var/lib/tessera \
  --user root \
  busybox chown -R 10001:10001 /var/lib/tessera
```

Then start Tessera normally. The `tessera` user (uid 10001) will be able to write
to the volume.

### Remediation — bind-mount host directory

```bash
# On the host, set ownership to uid 10001
sudo chown -R 10001:10001 /path/to/host/tessera-data/
sudo chmod 750 /path/to/host/tessera-data/
```

### Remediation — docker-compose

Add an `init` service that fixes ownership before the main service starts, or use
the `user: root` override on the volume initialization step. Example:

```yaml
services:
  tessera-init:
    image: busybox
    command: chown -R 10001:10001 /var/lib/tessera
    volumes:
      - tessera_audit:/var/lib/tessera
    user: root

  tessera:
    image: ghcr.io/cloudmorph-ai/tessera:0.1.0
    depends_on:
      tessera-init:
        condition: service_completed_successfully
    volumes:
      - tessera_audit:/var/lib/tessera

volumes:
  tessera_audit:
```

### Note on read-only mounts

`/etc/tessera/` (config, policies, tokens) is mounted read-only (`:ro`). Only
`/var/lib/tessera/` needs to be read-write. Do not attempt to mount `/var/lib/tessera`
as `:ro` — the SQLite sink will fail to open the database.

---

## 5. Regex timeout warnings

### Symptoms

- Startup fails with exit code 2 and a log line mentioning `regex_potential_redos`.
- A policy reloads but disappears from `policy_state.loaded` and appears in
  `policy_state.errored`.
- At runtime, audit events contain `"decision_error": "regex_timeout"` for a
  specific policy.

### Background

Tessera uses the `regex` library (not stdlib `re`) for all pattern matching. Two
safety layers are applied:

1. **Load-time corpus test.** When a policy file is loaded or reloaded, each regex
   pattern is tested against five synthetic strings of lengths 10, 100, 1000, 10000,
   and 100000 characters. Each test must complete within 50ms. A pattern that fails
   this test is considered ReDoS-prone.
   - At **startup**: the process exits with code 2. Tessera refuses to start.
   - At **reload**: only that policy file is rejected; the previous valid version
     is kept in memory and the file appears in `policy_state.errored`.

2. **Per-match runtime timeout.** Every pattern evaluation at request time is bounded
   to 100ms. If the timeout fires, the condition returns `false` and the audit event
   records `decision_error: regex_timeout`. The request is not failed — only that
   condition is treated as non-matching.

### Identifying the offending pattern

At startup Tessera logs the policy file and condition index:

```
ERROR tessera.policy.regex_safety event=policy_validation_failed
      reason=regex_potential_redos
      path=/etc/tessera/policies/pii-block.yaml
      condition_index=2
      pattern=(\\d{1,3}\\.){3}\\d{1,3}(:\\d+)?
```

For runtime timeouts, check audit events:

```bash
tessera audit verify --all --json | \
  jq '.[] | select(.decision_error == "regex_timeout") | {policy_id, tool_name}'
```

The Prometheus metric `regex_timeout_total{policy_id="..."}` also tracks this per
policy if metrics are enabled.

You can also lint policies before deploying — lint runs the same corpus check:

```bash
tessera policy lint --policy-dir policies/
```

A passing lint guarantees no ReDoS patterns and no YAML schema errors.

### Remediating a slow pattern

The general approach is to reduce backtracking by making patterns more specific or
by anchoring them.

**Common ReDoS patterns and safer alternatives:**

```yaml
# Dangerous: nested quantifiers cause exponential backtracking
# (a+)+$  or  (\w+\s+)+  or  (.*a){10,}

# Avoid: unbounded repetition of groups containing repetition
pattern: "(\\w+\\s+)+"       # bad

# Better: anchor and limit
pattern: "^(\\w+ ){1,20}\\w+$"   # anchored, bounded

# Avoid: alternation with shared prefixes in unbounded quantifier
pattern: "(foo|fo)+"          # bad

# Better: deduplicate the alternation
pattern: "foo+"               # good
```

**IP address matching — safe form:**

```yaml
# Instead of: (\d{1,3}\.){3}\d{1,3}
# Use the fully-explicit form:
pattern: "\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"
```

**Validation workflow:**

```bash
# Edit the pattern, then lint before saving to the watched policies dir
tessera policy lint --policy-dir /tmp/test-policies/

# Or test a single file
tessera policy lint --policy-dir /tmp/test-policies/ --json
```

If you cannot simplify a pattern to pass the corpus test, consider moving the
matching logic to a custom condition or pre-filter at your upstream instead.

---

## 6. /metrics returns 401

### Symptoms

- `GET /metrics` returns HTTP 401.
- Prometheus scrape job fails with authentication error.
- The error persists even when using a token that works for `/mcp/` routes.

### Background

`/metrics` is disabled by default (`metrics.enabled: false`). When enabled, bearer
authentication is required. Two token models are supported:

| Config | Behaviour |
|---|---|
| `metrics.bearer_token_env` unset | Any token from the main token list grants `/metrics` access |
| `metrics.bearer_token_env: TESSERA_METRICS_TOKEN` set | Only the token in that env var grants `/metrics` access; main-list tokens are rejected |

### Cause A — metrics is not enabled

```bash
# Check config
grep -A3 "^metrics:" tessera.yaml
```

```yaml
metrics:
  enabled: false   # /metrics is not mounted; returns 404 not 401
```

A 404 response means metrics is disabled. A 401 means it is enabled but the token
is wrong. Confirm which you are seeing before proceeding.

Enable metrics:

```yaml
metrics:
  enabled: true
```

Restart (this field is not SIGHUP-reloadable).

---

### Cause B — dedicated metrics token set but wrong token used

If `TESSERA_METRICS_TOKEN` is set, Tessera accepts **only** that token for
`/metrics`. Sending a main-list token returns 401.

**Diagnosis.** Check whether the env var is populated in the running container:

```bash
docker exec tessera env | grep TESSERA_METRICS_TOKEN
```

**Remediation.** Configure your Prometheus scrape job to send the dedicated token:

```yaml
# prometheus.yml scrape config
scrape_configs:
  - job_name: tessera
    static_configs:
      - targets: ['tessera:8080']
    bearer_token: <value of TESSERA_METRICS_TOKEN>
```

---

### Cause C — main-list token used but dedicated token env var is also set

Once `TESSERA_METRICS_TOKEN` is set, **main-list tokens no longer work for
`/metrics`**. This is intentional — it lets you give Prometheus a read-only token
with no ability to call MCP upstreams.

Unset `TESSERA_METRICS_TOKEN` if you want main-list tokens to work for metrics again,
or update the Prometheus config to use the dedicated token.

---

### Cause D — `Authorization` header format incorrect

Tessera expects the standard HTTP Bearer format:

```
Authorization: Bearer <token>
```

Not `Token <token>`, not a bare token, not `bearer <token>` (case is not
significant for the scheme name, but the space and the word `Bearer` must be present).

Test directly:

```bash
curl -v -H "Authorization: Bearer $TESSERA_METRICS_TOKEN" \
  http://localhost:8080/metrics
```

---

## 7. healthz shows errored policies

### Symptoms

- `GET /healthz` returns `policy_state.errored` with one or more entries.
- Fewer policies than expected are active (`policy_state.loaded` is lower than the
  number of files in the policies directory).
- A policy you recently edited stopped applying.

### Reading the error

```bash
curl -s http://localhost:8080/healthz | jq '.policy_state.errored'
```

```json
[
  {
    "path": "/etc/tessera/policies/cost-cap.yaml",
    "error": "1 validation error for Policy\nconditions -> 0 -> arg\n  field required (type=value_error.missing)"
  },
  {
    "path": "/etc/tessera/policies/pii-block.yaml",
    "error": "regex_potential_redos: pattern '(a+)+b' in condition 0 failed corpus test (50ms cap)"
  }
]
```

The `error` string is the verbatim message from the policy loader — either a
Pydantic validation error or a `regex_safety` rejection message.

### Remediation steps

1. **Read the error string.** It tells you exactly which file and which field is
   wrong. Pydantic errors include the field path (e.g., `conditions -> 0 -> arg`).

2. **Lint before editing.** Use the CLI to validate without a running server:

   ```bash
   tessera policy lint --policy-dir policies/
   tessera policy lint --policy-dir policies/ --json   # machine-readable
   ```

   Lint runs the full schema validation and the ReDoS corpus test. Exit 0 = all
   policies valid.

3. **Fix the offending file.** Common mistakes:

   - Missing required field (`action`, `match.tool` or `match.tool_pattern`, etc.)
   - `conditions` is not a list (bare object instead of `- key: value` list item)
   - `regex_potential_redos`: see [section 5](#5-regex-timeout-warnings) for pattern
     simplification guidance
   - Duplicate `policy_id` across files (second file is rejected)

4. **Trigger a reload.** If `policies.reload: watch` is set, saving the file
   triggers an automatic reload within a few seconds. Otherwise send SIGHUP:

   ```bash
   kill -HUP <tessera-pid>
   # or
   docker kill --signal HUP tessera
   ```

5. **Verify the fix:**

   ```bash
   curl -s http://localhost:8080/healthz | jq '.policy_state'
   ```

   `errored` should now be empty and `loaded` should include the recovered policy.

### Startup vs reload behaviour

The policy reload error handling is intentionally asymmetric:

| Event | Behaviour |
|---|---|
| **Startup** | Every policy file must validate. If any file errors, Tessera **exits with code 2**. The errored file must be fixed before the process will start. |
| **Reload (watch or SIGHUP)** | Per-file isolation. The errored file is skipped; the **previous valid version** of that file stays in memory. Other policies are unaffected. `/healthz` reflects the error. |

This means a bad edit during a running deployment degrades gracefully — the old
policy remains active — but a fresh deployment with an invalid file will not start.
Always run `tessera policy lint` in CI before deploying new policy files.

---

## General diagnostic checklist

When Tessera behaves unexpectedly, work through this list in order:

```
1. Check /healthz for errored policies
   curl -s http://localhost:8080/healthz | jq

2. Check the X-Tessera-Mode header on a proxied request
   curl -v http://localhost:8080/mcp/<upstream> ... 2>&1 | grep X-Tessera

3. Enable DEBUG logging and replay the request
   TESSERA_LOG_LEVEL=DEBUG tessera serve

4. Run policy lint to validate all policy files
   tessera policy lint --policy-dir policies/

5. Check audit chain integrity
   tessera audit verify --all --json

6. Confirm volume ownership if running in Docker
   docker run --rm -v tessera_audit:/var/lib/tessera busybox ls -la /var/lib/tessera
```

---

## Reference: key commands

```bash
# Validate all policy files (schema + ReDoS corpus check)
tessera policy lint --policy-dir policies/

# Dump full audit chain status for all scopes
tessera audit verify --all --json

# Dump audit chain status for one scope
tessera audit verify --scope alice --json

# Check proxy health and policy load state
curl -s http://localhost:8080/healthz | jq

# Start with debug logging
tessera serve --log-level DEBUG

# Scaffold a new deployment (defaults to log_only mode)
tessera init --dir ./my-tessera
```

---

## Reference: response headers in log_only mode

When `policies.mode: log_only`, every proxied response includes these headers:

| Header | Values | Meaning |
|---|---|---|
| `X-Tessera-Mode` | `log_only` | Active deployment mode |
| `X-Tessera-Decision` | `would_block`, `would_allow`, `no_match` | What would happen in enforcement mode |
| `X-Tessera-Policy-Id` | `<policy id>` | Only present when `would_block` |
| `X-Tessera-Reason` | `<interpolated reason string>` | Only present when `would_block` |

In `enforcement` mode, `X-Tessera-Mode: enforcement` is sent and
`X-Tessera-Decision` reflects the actual decision honored. In `observation` mode,
`X-Tessera-Mode: observation` is sent and no decision headers are added.
