# Tessera v0.1 — Troubleshooting

---

## 1. Policy not applying

**Symptom.** A tool call you expected to be blocked goes through; no block appears in the audit log; `X-Tessera-Decision: would_block` but upstream was still called.

**Remediation.** Check `X-Tessera-Mode` on any proxied response. `log_only` (the default after `tessera init`) computes decisions but never enforces. Flip `policies.mode: enforcement` in `tessera.yaml` and restart (`mode` is not SIGHUP-reloadable). Also check whether the policy skipped silently because `match.require_intent: true` is set but the agent sends no intent — enable `DEBUG` logging and look for `skipped reason=require_intent_not_met`. Alternatively the policy file may have failed to load (see issue 7).

```bash
curl -s http://localhost:8080/healthz | jq '.policy_state'
```

---

## 2. Intent missing on tool call

**Symptom.** Requests blocked with `reason: intent_required`; audit rows show `decision: block`; some agents pass, others are blocked.

**Remediation.** Two separate controls: `intent.required: true` (global — rejects all calls without intent) and `match.require_intent: true` (per-policy — silently skips that policy for intent-blind agents). Confirm which is firing via the audit log. If the intent block is present but rejected, check `intent.meta_key` matches the key your agent writes (e.g. `_meta.tessera_intent` vs `_meta.my_intent`). Valid intent verbs: `read`, `write`, `delete`, `list`, `create`, `update`, `execute`, `audit`, `export`. Run lint to catch malformed intent schemas before deploy.

```bash
grep -A3 "^intent:" tessera.yaml
```

---

## 3. `tessera audit verify` reports `hash_mismatch` or `chain_break`

**Symptom.** `tessera audit verify --all --json` exits code 2; output contains `hash_mismatch` or `chain_break` entries.

**Remediation.** `hash_mismatch` means an event body was altered after write — check for custom `AuditSink` plugins that rewrite fields, or direct SQLite edits. `chain_break` means rows were deleted or reordered — never delete rows from the middle of a chain; only remove the oldest tail rows. If a break is isolated to a known window, document it and rotate to a new scope going forward. Do not delete affected rows — that extends the break.

```bash
tessera audit verify --all --json | jq '.[] | select(.status == "failed")'
```

---

## 4. Docker volume permission denied on `/var/lib/tessera`

**Symptom.** Container exits at startup with `PermissionError: [Errno 13] ... '/var/lib/tessera/audit.db'`; `/healthz` never becomes reachable.

**Remediation.** Tessera runs as uid 10001 (`tessera` user). Fix ownership on the mounted volume before starting the container:

```bash
docker run --rm -v tessera_audit:/var/lib/tessera --user root \
  busybox chown -R 10001:10001 /var/lib/tessera
```

For bind-mounts: `sudo chown -R 10001:10001 /path/to/host/tessera-data/`. Mount `/etc/tessera/` read-only; `/var/lib/tessera/` must be read-write.

---

## 5. Regex timeout warnings in audit log

**Symptom.** Startup exits code 2 with `regex_potential_redos`; a policy disappears from `policy_state.loaded`; audit events contain `"decision_error": "regex_timeout"`.

**Remediation.** Tessera tests every regex against synthetic strings at load time (50ms cap) and enforces a 100ms per-match runtime cap. Identify the offending pattern from the startup log (`condition_index` is logged). Fix by anchoring patterns and eliminating nested quantifiers (e.g. replace `(\w+\s+)+` with `^(\w+ ){1,20}\w+$`). Lint before deploying:

```bash
tessera policy lint --policy-dir policies/
```

---

## 6. `/metrics` returns 401

**Symptom.** `GET /metrics` returns 401; Prometheus scrape fails; a token that works for `/mcp/` routes is rejected.

**Remediation.** Metrics are disabled by default (`metrics.enabled: false`) — a 404 means disabled, a 401 means enabled but wrong token. When `metrics.bearer_token_env` is set, only that dedicated token is accepted for `/metrics`; main-list tokens are explicitly rejected (by design). Confirm whether the env var is set: `docker exec tessera env | grep TESSERA_METRICS_TOKEN`. Update your Prometheus `bearer_token` to the dedicated value, or unset the env var to fall back to main-list tokens. Token format must be `Authorization: Bearer <token>` — bare tokens and `Token <token>` are rejected.

---

## 7. `/healthz` shows errored policies

**Symptom.** `policy_state.errored` is non-empty; `policy_state.loaded` is lower than the number of files in the policies directory; a recently edited policy stopped applying.

**Remediation.** Read the verbatim error string — Pydantic errors include the field path. Common causes: missing required field, `conditions` not a list, ReDoS pattern (see issue 5), duplicate `policy_id`. Lint to reproduce offline: `tessera policy lint --policy-dir policies/`. Fix the file, then reload: save the file (if `policies.reload: watch`) or send SIGHUP (`docker kill --signal HUP tessera`). At startup all files must validate or Tessera exits code 2; at reload, only the bad file is skipped and the previous valid version stays active.

```bash
curl -s http://localhost:8080/healthz | jq '.policy_state.errored'
```

---

## 8. Bearer token rejected

**Symptom.** All routes return 401; token is correct but Tessera rejects it.

**Remediation.** Tessera loads tokens from one of three sources, in precedence order: `TESSERA_BEARER_TOKENS` (inline `name1:token1,name2:token2`), `TESSERA_BEARER_TOKENS_FILE` (YAML file with `tokens: [{name, token, scope?}]`), or `TESSERA_BEARER_TOKEN` (legacy single token). Confirm the file (or inline list) is reachable and that the token in the `Authorization` header exactly matches one of the configured tokens (no trailing whitespace, no BOM). Format must be `Authorization: Bearer <token>` — scheme name is case-insensitive but the space and word `Bearer` are required. Minimum token length is 16 characters after trimming. Rotate a compromised token by removing it from the configured source and restarting the process (policy reloads via watchdog; token reloads require restart).

```bash
docker exec tessera cat /etc/tessera/tokens.txt | wc -l
```

---

## 9. Upstream MCP server unreachable / timeout

**Symptom.** Requests return 502 or 504; audit log shows `upstream_error: connection refused` or `upstream_error: timeout`; `/healthz` is healthy.

**Remediation.** Tessera acts as a proxy — a 502/504 means it reached your config but could not reach the upstream. Check `upstreams[].url` in `tessera.yaml` is correct and reachable from inside the container (DNS, network policy). Increase `upstreams[].timeout_seconds` if the upstream is legitimately slow. For Docker Compose, ensure Tessera and the upstream share a network and use the service name as the hostname, not `localhost`.

```bash
docker exec tessera wget -qO- http://<upstream-host>:<port>/health
```

---

## 10. Tessera won't start (config validation error)

**Symptom.** Process exits immediately with code 1 or 2 and a YAML or Pydantic validation error in the logs; no `healthz` endpoint appears.

**Remediation.** Exit code 1 = config schema error (`tessera.yaml` field missing or wrong type). Exit code 2 = policy file error (see issue 7) or ReDoS pattern (see issue 5). The startup log prints the exact field path for schema errors. Validate config and policies offline before deploying:

```bash
tessera config validate --config tessera.yaml
tessera policy lint --policy-dir policies/
```

Required top-level fields: `upstreams` (non-empty list). Bearer tokens are required for non-loopback exposure and resolved from `TESSERA_BEARER_TOKENS`, `TESSERA_BEARER_TOKENS_FILE`, or `TESSERA_BEARER_TOKEN` (precedence order). All `upstreams[].url` values must be valid URLs.
