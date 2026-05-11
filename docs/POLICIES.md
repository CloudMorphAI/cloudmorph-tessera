# Tessera v0.1 — Policy Reference

Developer reference for authoring YAML policies. For the full YAML body of each reference policy, open `policies/<id>.yaml` directly.

**Contents**

1. [Policy YAML schema reference](#1-policy-yaml-schema-reference)
2. [Condition catalog](#2-condition-catalog)
3. [Reference policies](#3-reference-policies)
4. [Authoring a custom policy](#4-authoring-a-custom-policy)
5. [Intent declarations](#5-intent-declarations)
6. [Composition limitations in v0.1](#6-composition-limitations-in-v01)

---

## 1. Policy YAML schema reference

<a name="yaml-schema"></a>

A policy is a single YAML file in the directory pointed to by `policies.dir` in `tessera.yaml` (default `/etc/tessera/policies`). Files whose names start with `_` are not loaded as policies. The canonical schema lives at `schemas/policy.schema.json`.

Full annotated skeleton:

```yaml
id: my-policy              # [a-z0-9-]{1,64}, unique across all loaded policies
name: My Policy            # max 100 chars; shown in CLI and /healthz
description: optional      # not used by the engine
match:
  upstream: "*"            # named upstream from tessera.yaml, or "*"
  tool: "*"                # glob; mutually exclusive with tool_pattern
  # tool_pattern: ".*_export"  # regex; ReDoS-checked at load; 100ms cap
  require_intent: false    # true → skip this policy for intent-blind calls
when:                      # AND-list; empty = always match
  - condition: action_class_in
    values: ["write.delete"]
  - condition: arg_equals
    arg: environment
    value: "production"
action: block              # allow | block | log_only | require_approval
reason: "Reason: ${arg.environment} (event ${audit.event_id})"
priority: 90               # higher fires first; first-match-wins
```

### Top-level fields

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `id` | yes | — | `[a-z0-9-]{1,64}`, unique. Canonical identifier in audit events and metrics. |
| `name` | yes | — | Human-readable label, max 100 chars. |
| `description` | no | — | Free-form; not used by the engine. |
| `match.upstream` | no | `"*"` | Named upstream from `tessera.yaml: upstreams[]` or `"*"` for all. |
| `match.tool` | no | `"*"` | Glob on MCP tool name. Mutually exclusive with `tool_pattern`. |
| `match.tool_pattern` | no | — | Regex on tool name. Load-time ReDoS check; 100ms per-match cap. Mutually exclusive with `tool`. |
| `match.require_intent` | no | `false` | When `true`, policy is skipped for calls with no intent block in `_meta`. |
| `when` | no | `[]` | List of conditions AND-ed together; all must be true. Empty = always match. |
| `action` | yes | — | `allow` \| `block` \| `log_only` \| `require_approval` |
| `reason` | no | — | Appears in JSON-RPC errors and audit events. Supports `${arg.X}` and `${audit.event_id}`. |
| `priority` | no | `0` | Higher values fire first. Ties broken alphabetically by `id`. First-match-wins. |

### `action` values

| Value | `enforcement` mode | `log_only` mode |
|-------|--------------------|-----------------|
| `allow` | Forward to upstream. | Forward; `X-Tessera-Decision: would_allow`. |
| `block` | JSON-RPC error `-32603`; upstream NOT called. | Forward; `X-Tessera-Decision: would_block`, `X-Tessera-Policy-Id`, `X-Tessera-Reason`. |
| `log_only` | Forward; audit records `log_only`. | Forward; headers reflect `would_allow`. |
| `require_approval` | JSON-RPC error `-32604` `approval_required`; upstream NOT called. | Forward; `X-Tessera-Decision: would_block`. |

### Policy lifecycle

| Phase | Behavior |
|-------|----------|
| Startup | All `*.yaml` files loaded; any failure → exit 2. |
| File-watch (`reload: watch`) | Per-file reload on change; failure keeps prior version. |
| SIGHUP | Full reload of all policies; re-reads `runtime.lockdown`. |
| `/healthz` | Returns `policy_state: {loaded: N, errored: [{path, error}]}`. |

### Regex safety

Patterns in `tool_pattern`, `arg_matches_regex`, `arg_contains_pattern`, and `intent_purpose_matches` are validated at load time by `tessera/policy/regex_safety.py` against strings of 10/100/1 000/10 000/100 000 chars; each must match within 50ms. Bad pattern → exit 2 at startup, or file skipped on reload. Runtime timeout is 100ms; condition returns `false` and audit records `decision_error: regex_timeout`.

---

## 2. Condition catalog

<a name="condition-catalog"></a>

**Conventions:** `arg` is a key in `params.arguments`; `"*"` scans all top-level values as strings. Missing arg → `false`. Numeric conditions coerce to float; non-numeric → `false`. All conditions in a `when` list are AND-ed; short-circuit on first `false`.

### Full condition reference

| Condition | Key fields | Truth condition |
|-----------|-----------|-----------------|
| `arg_equals` | `arg`, `value` | `arguments[arg] == value` (string) |
| `arg_greater_than` | `arg`, `value` | `float(arguments[arg]) > value` |
| `arg_less_than` | `arg`, `value` | `float(arguments[arg]) < value` |
| `arg_matches_regex` | `arg`, `pattern` | `regex.search(pattern, str(arguments[arg]))`; `"*"` scans all values |
| `arg_contains_pattern` | `arg`, `pattern` | Alias of `arg_matches_regex` |
| `arg_in_set` | `arg`, `values` | `arguments[arg] in values` |
| `arg_size_greater_than` | `arg`, `bytes` | `len(json.dumps(arguments[arg])) > bytes` |
| `tool_name_in` | `values` | Exact tool name match |
| `action_class_in` | `values` | Tool's verb set intersects `values`; `false` for unregistered tools |
| `intent_class_in` | `values` | `intent.verbs` intersects `values`; `false` when no intent block |
| `intent_purpose_matches` | `pattern` | Regex search on `intent.purpose`; 100ms timeout |
| `region_in` | `arg`, `regions` | `arguments[arg].startswith(any prefix in regions)` |
| `time_of_day_outside` | `start`, `end`, `tz` | Wall-clock outside `[HH:MM, HH:MM)` daily window in IANA tz |
| `meta_field_equals` | `key`, `value` | Dot-path equality on `_meta` |
| `any_of` | `conditions` | Logical OR; short-circuits on first true |
| `none_of` | `conditions` | True only if none of the nested conditions are true |

### Condition mini-examples

```yaml
# Scan every argument for a US SSN pattern
- condition: arg_matches_regex
  arg: "*"
  pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"

# Block if request targets a non-EU region (prefix match on two arg names)
- condition: none_of
  conditions:
    - condition: region_in
      arg: region
      regions: ["eu-", "europe-"]
    - condition: region_in
      arg: location
      regions: ["eu-", "europe-"]

# Only allow calls within business hours (London time)
- condition: time_of_day_outside
  start: "09:00"
  end: "17:00"
  tz: "Europe/London"

# Block if any write verb AND argument size is large (AND-list)
- condition: action_class_in
  values: ["write.create", "write.update"]
- condition: arg_size_greater_than
  arg: body
  bytes: 10000

# Check intent declared by agent (intent-aware only)
- condition: intent_class_in
  values: ["write.delete"]
- condition: intent_purpose_matches
  pattern: "cost.?attribution|billing"

# Allow only specific tool names
- condition: tool_name_in
  values:
    - "aws_ec2_describe_instances"
    - "aws_s3_list_buckets"

# Block outside allowed region set
- condition: arg_in_set
  arg: region
  values: ["us-east-1", "us-west-2", "eu-west-1"]
```

### Action verbs taxonomy

Built-in verbs in `tessera/policy/action_verbs.py`:

- `read.list`, `read.describe`, `read.get`, `read.search`, `read.aggregate`
- `analyze`, `summarize`, `compare`
- `write.create`, `write.update`, `write.delete`
- `execute.run`, `execute.deploy`
- `notify.send`, `notify.publish`
- `escalate.approve`, `escalate.deny`
- `audit.log`, `audit.export`
- `simulate`, `dry_run`

Extend via `policies/_action_verbs.yaml` (leading `_` prevents it loading as a policy):

```yaml
# policies/_action_verbs.yaml
mappings:
  github_create_issue: [write.create]
  github_close_issue: [write.update]
  github_delete_repo: [write.delete]
```

Tools not in the registry have an empty verb set; `action_class_in` returns `false` for them.

---

## 3. Reference policies

All reference policies are in `policies/`. They are mode-agnostic: the deployment-level `policies.mode` determines how the `action` is acted upon. All 14 work unchanged in `enforcement`, `log_only`, and `observation` modes.

**Priority ladder (core 7):** PII (100) → secrets (95) → prod protection (90) → EU residency (80) → write approval (70) → read-only mode (60) → cost cap (50). A `block` at priority 90 prevents lower-priority `require_approval` rules from seeing the same call (first-match-wins).

### Original 7 (core controls)

| ID | Priority | File | What it does / Threat addressed |
|----|----------|------|----------------------------------|
| `pii-block` | 100 | `policies/pii-block.yaml` | Scans all args for SSN and Visa card patterns; blocks on match. Prevents PII exfiltration through any tool call. |
| `secret-leak-block` | 95 | `policies/secret-leak-block.yaml` | Scans all args for AWS keys, `sk-`/`pk-` API keys, and GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`). Prevents credential leakage. |
| `prod-protection` | 90 | `policies/prod-protection.yaml` | Blocks `write.delete` and `execute.deploy` when `environment == production/prod` or `resource_name` ends in `-prod`. Prevents accidental destructive ops in prod. |
| `data-residency-eu` | 80 | `policies/data-residency-eu.yaml` | Blocks data-movement ops (`write.create`, `write.update`, `read.get`) when neither `region` nor `location` arg has an `eu-`/`europe-` prefix. Enforces GDPR residency. |
| `write-action-approval` | 70 | `policies/write-action-approval.yaml` | Returns `require_approval` for all `write.create`, `write.update`, `write.delete` ops. Ensures human-in-the-loop for any mutation. |
| `read-only-mode` | 60 | `policies/read-only-mode.yaml` | Blocks any tool not in the five `read.*` verbs (including unregistered tools). Locks a deployment to read-only access. |
| `cost-cap` | 50 | `policies/cost-cap.yaml` | Blocks when `max_tokens > 100 000` or `estimated_cost_usd > 1.0`. Prevents runaway token/cost spend. |

### OWASP / MCP-protection 7 (provider-specific and injection controls)

| ID | Priority | File | Threat addressed |
|----|----------|------|-----------------|
| `owasp-mcp-prompt-injection` | — | `policies/owasp-mcp-prompt-injection.yaml` | OWASP MCP Top 10: prompt injection. Blocks args containing instruction-override phrases, system-prompt mentions, heredoc markers, base64 payload smuggling, and the Cursor+Jira `_meta` field injection vector (August 2025). |
| `owasp-mcp-tool-poisoning` | — | `policies/owasp-mcp-tool-poisoning.yaml` | OWASP MCP Top 10: tool poisoning / typo-squatting. Blocks tool names matching impostor namespaces (`github_official`, `git_hub`, `g0thub`, `slack_app`, `sl4ck`, `jira_official`, etc.) via `tool_pattern`. |
| `github-mcp-protection` | — | `policies/github-mcp-protection.yaml` | Blocks destructive GitHub MCP ops (`delete_repo`, `force_push`, `delete_branch`) on protected branches or production repos. |
| `jira-mcp-protection` | — | `policies/jira-mcp-protection.yaml` | Blocks Jira MCP mutations on security-critical tickets; guards against the August 2025 Cursor+Jira 0-Click `_meta` smuggling attack. |
| `postgres-mcp-protection` | — | `policies/postgres-mcp-protection.yaml` | Blocks Postgres MCP `DROP`, `TRUNCATE`, and `ALTER` on critical tables (`prod_*`, `users`, `customers`, `payment_methods`, `sessions`). |
| `salesforce-mcp-protection` | — | `policies/salesforce-mcp-protection.yaml` | Blocks Salesforce MCP delete/update ops on production org IDs. Org IDs in the file are templates — replace with actual prod org IDs. |
| `slack-mcp-protection` | — | `policies/slack-mcp-protection.yaml` | Blocks Slack MCP message ops to public channels when args contain PII, secrets, or API key patterns. Prevents data leakage via chat. |

---

## 4. Authoring a custom policy

Drop a file into `policies.dir`. With `reload: watch` it is picked up within seconds. Check `/healthz` for `policy_state.errored` to confirm a clean load.

**Example — block data exports outside business hours on the analytics upstream:**

```yaml
# policies/after-hours-export-block.yaml
id: after-hours-export-block
name: After-Hours Export Block
description: >
  Blocks analytics export tools outside 08:00-18:00 UTC.
  Prevents runaway agents from triggering large exports at night.
match:
  upstream: analytics
  tool_pattern: ".*_export"
when:
  - condition: time_of_day_outside
    start: "08:00"
    end: "18:00"
    tz: "UTC"
action: block
reason: "Export operations are only permitted 08:00-18:00 UTC"
priority: 75
```

Alternatively narrow by verb instead of tool name pattern:

```yaml
match:
  upstream: analytics
  tool: "*"
when:
  - condition: action_class_in
    values: ["audit.export"]
  - condition: time_of_day_outside
    start: "08:00"
    end: "18:00"
    tz: "UTC"
```

**Tips:**
- Set `priority >= 90` for security-critical rules that must fire before broad-match ones.
- Always include `reason` — it appears in JSON-RPC errors, audit events, and `X-Tessera-Reason` headers.
- Author under `mode: log_only` first; review `X-Tessera-Decision: would_block` on live traffic before switching to `enforcement`.
- Use `arg: "*"` in `arg_matches_regex` to scan all arguments; watch `audit` logs for `regex_timeout` on large payloads.
- Register custom tools in `policies/_action_verbs.yaml` so `action_class_in` works for them.
- Unregistered tools have an empty verb set — `action_class_in` returns `false`, so they pass through verb-based allow rules and are blocked by verb-based block rules (e.g. `read-only-mode`).

Lint and test:

```
tessera policy lint --policy-dir policies/
tessera policy test --policy-dir policies/ --fixture-dir tests/fixtures/policies/<id>/
```

---

## 5. Intent declarations

`_meta.tessera_intent` shape (key name configurable via `intent.meta_key` in `tessera.yaml`):

```json
{
  "_meta": {
    "tessera_intent": {
      "verbs": ["read.list"],
      "purpose": "Inventory S3 buckets for the cost-attribution report."
    }
  }
}
```

`verbs` is required when an intent block is present; `purpose` is optional (max 1024 chars).

**Intent-aware vs intent-blind:** Off-the-shelf MCP clients (Cursor, Claude Desktop, Windsurf) do not populate `_meta.tessera_intent`. Tessera handles both simultaneously:

- Policies with `match.require_intent: true` are silently skipped for calls without an intent block — they do not accidentally block standard clients.
- `intent_class_in` and `intent_purpose_matches` evaluate to `false` for intent-blind calls (fail-closed).
- A single `policies.dir` serves mixed deployments without forking the policy set.

Set `intent.required: true` in `tessera.yaml` to reject every call that lacks a valid intent block globally (`reason: intent_required`). Enable only when all agents in the deployment are confirmed intent-aware.

---

## 6. Composition limitations in v0.1

| Feature | v0.1 | Planned |
|---------|------|---------|
| `any_of` / `none_of` combinators | Available | — |
| First-match-wins ordered evaluation | Available | — |
| `priority` field | Available | — |
| Rego / OPA evaluation | Not available | v0.2 |
| Policy namespacing / scopes | Not available | v0.2 |
| Policy chaining / inheritance | Not available | v0.2 |
| Signed policy bundles | Not available | v0.2 |
| Native rate limiting | Not available | v0.2 |

- **No Rego / OPA** — Pure Python evaluation. Rego deferred; adds ~100 MB to container image. See [docs/ROADMAP.md](ROADMAP.md).
- **No policy namespacing** — Flat namespace; every loaded policy potentially applies to every request. See [docs/ROADMAP.md](ROADMAP.md).
- **No chaining or inheritance** — No `extends`, `import`, or `compose`. Each YAML is self-contained. Intra-policy composition is `any_of`/`none_of` within a single `when` list only.
- **No per-policy version pinning or signed bundles** — Policy directory is trusted as-is.
- **No native rate limiting** — Deploy Tessera behind nginx, Caddy, Cloudflare, or AWS API Gateway for rate limiting.
