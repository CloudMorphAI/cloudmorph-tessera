# Tessera v0.1 — Policy Reference

Authoritative reference for authoring, understanding, and deploying policies with the
Tessera MCP firewall.

**Contents**

1. [Policy YAML schema reference](#1-policy-yaml-schema-reference)
2. [Condition catalog](#2-condition-catalog)
3. [The 7 reference policies, line by line](#3-the-7-reference-policies-line-by-line)
4. [Authoring a custom policy from scratch](#4-authoring-a-custom-policy-from-scratch)
5. [Intent declarations](#5-intent-declarations)
6. [Composition limitations in v0.1](#6-composition-limitations-in-v01)

---

## 1. Policy YAML schema reference

A policy is a single YAML file in the directory pointed to by `policies.dir` in
`tessera.yaml` (default `/etc/tessera/policies`). Files whose names start with `_` are
treated as configuration files and are not loaded as policies.

The canonical schema lives at `schemas/policy.schema.json`.

### 1.1 Top-level structure

```yaml
id: <string>           # required
name: <string>         # required
description: <string>  # optional
match:                 # required block
  upstream: <string>   # default "*"
  tool: <glob>         # default "*"; exclusive with tool_pattern
  tool_pattern: <regex># default none; exclusive with tool
  require_intent: bool # default false
when:                  # optional; default [] (always-true)
  - condition: <name>
    <fields>: ...
action: <string>       # required
reason: <string>       # optional; supports ${arg.X} and ${audit.event_id}
priority: <integer>    # optional; default 0
```

### 1.2 Field reference

**`id`** — Required. `[a-z0-9-]{1,64}`, unique across all loaded policies. Used in
audit events, metrics labels, and error messages. Filename is informational; `id` is
canonical.

**`name`** — Required. Human-readable label, max 100 characters. Shown in CLI output
and health check responses.

**`description`** — Optional. Free-form explanation; not used by the engine.

**`match.upstream`** — Optional, default `"*"`. Name of a configured upstream from
`tessera.yaml: upstreams[]`, or `"*"` to apply to all upstreams.

**`match.tool`** — Optional, default `"*"`. Glob match on the MCP tool name
(`params.name`). Mutually exclusive with `tool_pattern`.

**`match.tool_pattern`** — Optional. Regex match on tool name. Uses the `regex` library
with a 100ms per-match timeout and a load-time ReDoS corpus test. Mutually exclusive
with `tool`.

**`match.require_intent`** — Optional, default `false`. When `true`, this policy is
skipped entirely for calls that carry no intent block in `_meta`. Allows intent-aware
rules to coexist safely with intent-blind agents.

**`when`** — Optional, default `[]` (always match). A list of conditions AND-ed
together; all must be true for the policy to fire. Evaluated left-to-right with
short-circuit. Missing arguments evaluate to `false` (fail-closed). See
[Section 2](#2-condition-catalog) for all 16 conditions.

**`action`** — Required. One of `allow`, `block`, `log_only`, `require_approval`.

| Value | Effect in `enforcement` | Effect in `log_only` mode |
|-------|------------------------|--------------------------|
| `allow` | Forward to upstream. | Forward; header `X-Tessera-Decision: would_allow`. |
| `block` | JSON-RPC error `-32603`; upstream NOT called. | Forward; headers `X-Tessera-Decision: would_block`, `X-Tessera-Policy-Id`, `X-Tessera-Reason`. |
| `log_only` | Forward; audit records `log_only`. | Forward; headers reflect `would_allow`. |
| `require_approval` | JSON-RPC error `-32604` `approval_required`; upstream NOT called. | Forward; header `X-Tessera-Decision: would_block`. |

**`reason`** — Optional. Appears in JSON-RPC error bodies and audit events. Supports
`${arg.X}` (expands to `arguments["X"]`) and `${audit.event_id}` interpolation.

**`priority`** — Optional, default `0`. Higher values evaluate first. Ties broken
alphabetically by `id`. First-match-wins: once a policy matches and all conditions are
true, evaluation stops.

### 1.3 Regex safety

All regex patterns in `tool_pattern`, `arg_matches_regex`, `arg_contains_pattern`, and
`intent_purpose_matches` are validated at load time by `tessera/policy/regex_safety.py`.
Each pattern is tested against five strings (lengths 10 / 100 / 1 000 / 10 000 /
100 000 chars); each match must complete within 50ms.

- **Startup failure** — bad pattern causes exit 2 (`event=policy_validation_failed
  reason=regex_potential_redos`).
- **Reload failure** — policy file skipped; prior version stays active.
- **Runtime timeout** — 100ms cap per match via `regex` library; condition returns
  `false`; audit records `decision_error: regex_timeout`. The request is not failed.

### 1.4 Policy lifecycle

| Phase | Behavior |
|-------|----------|
| Startup | All `*.yaml` files loaded; any failure causes exit 2. |
| File-watch (`reload: watch`) | Per-file reload; failure keeps prior version. |
| SIGHUP | Per-file reload of all policies; also re-reads `runtime.lockdown`. |
| `/healthz` | Returns `policy_state: {loaded: N, errored: [{path, error}]}`. |

---

## 2. Condition catalog

**Common conventions:**
- `arg` — key in `params.arguments`. `"*"` iterates all top-level values as strings.
- Missing arg — evaluates to `false`; short-circuits the `when` list.
- Numeric conditions — coerce to float; non-numeric evaluates to `false`.
- Regex conditions — `regex` library, 100ms per-match timeout.

### 2.1 Argument conditions

| Condition | Required fields | Truth condition |
|-----------|----------------|-----------------|
| `arg_equals` | `arg`, `value` | `arguments[arg] == value` (string equality) |
| `arg_greater_than` | `arg`, `value` | `float(arguments[arg]) > value` |
| `arg_less_than` | `arg`, `value` | `float(arguments[arg]) < value` |
| `arg_matches_regex` | `arg`, `pattern` | `regex.search(pattern, str(arguments[arg]))`; `arg: "*"` scans all values |
| `arg_contains_pattern` | `arg`, `pattern` | Alias of `arg_matches_regex` |
| `arg_in_set` | `arg`, `values` | `arguments[arg] in values` |
| `arg_size_greater_than` | `arg`, `bytes` | `len(json.dumps(arguments[arg])) > bytes` |

**Examples:**

```yaml
# arg_equals
- condition: arg_equals
  arg: environment
  value: "production"

# arg_greater_than
- condition: arg_greater_than
  arg: max_tokens
  value: 100000

# arg_less_than
- condition: arg_less_than
  arg: confidence_score
  value: 0.5

# arg_matches_regex — scan all args for SSN pattern
- condition: arg_matches_regex
  arg: "*"
  pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"

# arg_in_set
- condition: arg_in_set
  arg: region
  values: ["us-east-1", "us-west-2", "eu-west-1"]

# arg_size_greater_than
- condition: arg_size_greater_than
  arg: body
  bytes: 10000
```

### 2.2 Tool conditions

**`tool_name_in`** — Checks that the tool name is in the list. No action verbs registry
required. Useful for exact tool-level allow/denylists.

| Field | Type |
|-------|------|
| `values` | list of strings |

```yaml
- condition: tool_name_in
  values:
    - "aws_ec2_terminate_instances"
    - "aws_rds_delete_db_instance"
```

**`action_class_in`** — Checks that the tool's verb set (from the action verbs registry)
intersects with `values`. Returns `false` for unregistered tools.

| Field | Type |
|-------|------|
| `values` | list of verb strings |

```yaml
- condition: action_class_in
  values: ["write.delete", "execute.deploy"]
```

### 2.3 Intent conditions

These conditions evaluate to `false` when no intent block is present unless the policy
also sets `match.require_intent: true`.

**`intent_class_in`** — Checks that `intent.verbs` intersects `values`.

```yaml
- condition: intent_class_in
  values: ["write.delete", "write.create"]
```

**`intent_purpose_matches`** — Regex search on `intent.purpose`. 100ms timeout applies.

```yaml
- condition: intent_purpose_matches
  pattern: "cost.?attribution|billing"
```

### 2.4 Context conditions

**`region_in`** — Prefix match: `arguments[arg].startswith(any prefix in regions)`.

| Field | Type |
|-------|------|
| `arg` | string |
| `regions` | list of prefix strings |

```yaml
- condition: region_in
  arg: region
  regions: ["eu-", "europe-"]
```

**`time_of_day_outside`** — True when the request wall-clock time is outside the
`[start, end)` daily window.

| Field | Type | Notes |
|-------|------|-------|
| `start` | `HH:MM` (24h) | Window start (inclusive) |
| `end` | `HH:MM` (24h) | Window end (exclusive) |
| `tz` | IANA timezone string | e.g., `"UTC"`, `"Europe/London"` |

```yaml
- condition: time_of_day_outside
  start: "09:00"
  end: "17:00"
  tz: "Europe/London"
```

**`meta_field_equals`** — Dot-path equality check on `_meta`.

| Field | Type |
|-------|------|
| `key` | dot-path string |
| `value` | string |

```yaml
- condition: meta_field_equals
  key: "x-custom-header.approved"
  value: "true"
```

### 2.5 Logical combinators

**`any_of`** — Logical OR; short-circuits on first true. Nesting supported.

```yaml
- condition: any_of
  conditions:
    - condition: arg_equals
      arg: environment
      value: "production"
    - condition: arg_equals
      arg: environment
      value: "prod"
```

**`none_of`** — True only if none of the nested conditions are true (NOT OR).

```yaml
- condition: none_of
  conditions:
    - condition: action_class_in
      values: ["read.list", "read.describe", "read.get", "read.search", "read.aggregate"]
```

### 2.6 Complete reference table

| Condition | Key fields | Truth |
|-----------|-----------|-------|
| `arg_equals` | `arg`, `value` | string equality |
| `arg_greater_than` | `arg`, `value` | numeric `>` |
| `arg_less_than` | `arg`, `value` | numeric `<` |
| `arg_matches_regex` | `arg`, `pattern` | `regex.search`; `"*"` scans all |
| `arg_in_set` | `arg`, `values` | membership |
| `arg_contains_pattern` | `arg`, `pattern` | alias of `arg_matches_regex` |
| `arg_size_greater_than` | `arg`, `bytes` | JSON byte count `>` |
| `tool_name_in` | `values` | exact tool name match |
| `action_class_in` | `values` | verb set intersects |
| `intent_class_in` | `values` | intent verbs intersect |
| `intent_purpose_matches` | `pattern` | regex on `intent.purpose` |
| `region_in` | `arg`, `regions` | prefix match |
| `time_of_day_outside` | `start`, `end`, `tz` | outside daily window |
| `meta_field_equals` | `key`, `value` | dot-path `_meta` equality |
| `any_of` | `conditions` | OR (short-circuit) |
| `none_of` | `conditions` | NOT OR |

### 2.7 Action verbs taxonomy

Built-in verbs in `tessera/policy/action_verbs.py`:

```
read.list    read.describe    read.get    read.search    read.aggregate
analyze      summarize        compare
write.create write.update     write.delete
execute.run  execute.deploy
notify.send  notify.publish
escalate.approve  escalate.deny
audit.log    audit.export
simulate     dry_run
```

Extend via `policies/_action_verbs.yaml` (leading `_` prevents it loading as a policy):

```yaml
# policies/_action_verbs.yaml
mappings:
  github_create_issue: [write.create]
  github_close_issue: [write.update]
  github_delete_repo: [write.delete]
```

The loader merges custom mappings with built-ins (file overrides). Tools not in the
registry have an empty verb set; `action_class_in` returns `false` for them.

---

## 3. The 7 reference policies, line by line

All reference policies are in `policies/`. They are mode-agnostic: the `action` field
declares intent; the deployment-level `policies.mode` determines how that decision is
acted upon. All 7 work unchanged in `enforcement`, `log_only`, and `observation` modes.

Listed in descending priority order (highest fires first).

---

### 3.1 `pii-block.yaml` — priority 100

```yaml
id: pii-block
name: PII Block
description: Block tool calls with arguments matching PII patterns (SSN, credit card numbers).
match:
  upstream: "*"
  tool: "*"
when:
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: "*"
        pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
      - condition: arg_matches_regex
        arg: "*"
        pattern: "\\b4[0-9]{12}(?:[0-9]{3})?\\b"
action: block
reason: "Request contains PII data patterns"
priority: 100
```

- `match: "*"` on both fields — universal scope; PII can leak through any tool call.
- `when` is a single `any_of`; one match among the two patterns is sufficient.
- First pattern `\b\d{3}-\d{2}-\d{4}\b` — US Social Security Number (`XXX-XX-XXXX`).
- Second pattern `\b4[0-9]{12}(?:[0-9]{3})?\b` — 13- and 16-digit Visa card numbers.
- Both use `arg: "*"` — every top-level argument value is scanned as a string.
- `priority: 100` — highest of all reference policies; fires before anything else.

Add more PII patterns by appending branches to the `any_of` block.

---

### 3.2 `secret-leak-block.yaml` — priority 95

```yaml
id: secret-leak-block
name: Secret Leak Block
description: Block tool calls where arguments appear to contain API keys or tokens.
match:
  upstream: "*"
  tool: "*"
when:
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: "*"
        pattern: "(?:AKIA|ASIA)[A-Z0-9]{16}"
      - condition: arg_matches_regex
        arg: "*"
        pattern: "(?:sk-|pk-)[a-zA-Z0-9]{20,}"
      - condition: arg_matches_regex
        arg: "*"
        pattern: "gh[pousr]_[A-Za-z0-9_]{36}"
action: block
reason: "Potential secret key detected in arguments"
priority: 95
```

- Universal match; secret leaks can come from any agent calling any tool.
- Three `arg_matches_regex` conditions (all `arg: "*"`) under `any_of`:
  - `(?:AKIA|ASIA)[A-Z0-9]{16}` — AWS long-term (`AKIA`) and short-term (`ASIA`) access
    key IDs.
  - `(?:sk-|pk-)[a-zA-Z0-9]{20,}` — common API key prefixes (OpenAI, Stripe, and
    similar providers).
  - `gh[pousr]_[A-Za-z0-9_]{36}` — GitHub tokens: personal (`ghp_`), OAuth (`gho_`),
    user-to-server (`ghu_`), server-to-server (`ghs_`), refresh (`ghr_`).
- `priority: 95` — fires after PII check but before environment-based policies.

---

### 3.3 `prod-protection.yaml` — priority 90

```yaml
id: prod-protection
name: Production Protection
description: Block destructive write operations when targeting production resources.
match:
  upstream: "*"
  tool: "*"
when:
  - condition: action_class_in
    values: ["write.delete", "execute.deploy"]
  - condition: any_of
    conditions:
      - condition: arg_equals
        arg: environment
        value: "production"
      - condition: arg_equals
        arg: environment
        value: "prod"
      - condition: arg_matches_regex
        arg: resource_name
        pattern: ".*-prod$"
action: block
reason: "Destructive action blocked in production environment"
priority: 90
```

- `when` is a two-condition AND: both must be true.
- First: `action_class_in ["write.delete", "execute.deploy"]` — only delete and deploy
  operations are in scope. Read operations, writes to non-prod targets, etc. pass through.
- Second: `any_of` — at least one production indicator must be present:
  `environment == "production"`, `environment == "prod"`, or
  `resource_name` ending in `-prod`.
- `priority: 90` — fires before `data-residency-eu` and `write-action-approval`.

---

### 3.4 `data-residency-eu.yaml` — priority 80

```yaml
id: data-residency-eu
name: EU Data Residency
description: Ensure data operations stay within EU regions.
match:
  upstream: "*"
  tool: "*"
when:
  - condition: action_class_in
    values: ["write.create", "write.update", "read.get"]
  - condition: none_of
    conditions:
      - condition: region_in
        arg: region
        regions: ["eu-", "europe-"]
      - condition: region_in
        arg: location
        regions: ["eu-", "europe-"]
action: block
reason: "Data operation outside EU residency boundary"
priority: 80
```

- First condition: limits scope to data-movement operations (create, update, get).
- Second condition `none_of` — fires when NEITHER the `region` argument NOR the
  `location` argument starts with `eu-` or `europe-`. Logic: block if the operation
  targets a non-EU region.
- Two `region_in` branches cover AWS-style (`region`) and GCP-style (`location`)
  argument naming. Either being EU is sufficient to pass through.
- `region_in` is a prefix match: `eu-west-1`, `eu-central-1`, `europe-west1` all match.

---

### 3.5 `write-action-approval.yaml` — priority 70

```yaml
id: write-action-approval
name: Write Action Approval
description: Require human approval for all write and delete operations.
match:
  upstream: "*"
  tool: "*"
when:
  - condition: action_class_in
    values: ["write.create", "write.update", "write.delete"]
action: require_approval
reason: "Write operation requires human approval"
priority: 70
```

- Single `action_class_in` condition covering all three write verbs.
- `action: require_approval` — in `enforcement` mode returns JSON-RPC error `-32604`
  with `approval_required: <reason>`; upstream is NOT called.
- `priority: 70` — fires after `prod-protection` (90). A delete targeting production is
  blocked outright by `prod-protection` and never reaches this policy. A delete targeting
  a non-production environment reaches this policy and requires approval instead.

---

### 3.6 `read-only-mode.yaml` — priority 60

```yaml
id: read-only-mode
name: Read-Only Mode
description: Allow only read operations; block everything else.
match:
  upstream: "*"
  tool: "*"
when:
  - condition: none_of
    conditions:
      - condition: action_class_in
        values: ["read.list", "read.describe", "read.get", "read.search", "read.aggregate"]
action: block
reason: "System is in read-only mode — only read operations are permitted"
priority: 60
```

- Single `none_of` wrapping a single `action_class_in` check. Reads: "block if the
  tool is NOT one of the five read verbs."
- All five read verbs in the taxonomy are listed, covering the full breadth of read
  operations.
- Tools with an empty verb set (not registered) are also blocked: `action_class_in`
  returns `false`, so `none_of([false])` is `true`. Unknown tools are blocked in
  read-only mode by default.
- Enable this policy by placing the file in `policies.dir`; remove it to restore write
  access.

---

### 3.7 `cost-cap.yaml` — priority 50

```yaml
id: cost-cap
name: Cost Cap
description: Block tool calls that exceed per-request cost thresholds.
match:
  upstream: "*"
  tool: "*"
when:
  - condition: any_of
    conditions:
      - condition: arg_greater_than
        arg: max_tokens
        value: 100000
      - condition: arg_greater_than
        arg: estimated_cost_usd
        value: 1.0
action: block
reason: "Request exceeds cost threshold"
priority: 50
```

- `any_of` with two `arg_greater_than` conditions: either exceeded threshold triggers
  the block.
- `max_tokens > 100000` — guards against oversized token requests.
- `estimated_cost_usd > 1.0` — guards against high-cost calls.
- `priority: 50` — lowest of the reference policies; cost checks run after all security
  policies.
- Both conditions silently pass (`false`) when the argument is absent, so this policy
  has no effect on tools that do not declare cost metadata. Both thresholds are
  configurable by copying and editing the policy file.

---

## 4. Authoring a custom policy from scratch

This section walks through writing a policy not covered by the reference set: blocking
data export operations outside business hours.

### Step 1 — Define the objective

> Block data export operations on the `analytics` upstream outside 08:00–18:00 UTC.

### Step 2 — Choose the match scope

```yaml
match:
  upstream: analytics
  tool_pattern: ".*_export"
```

Narrow to the specific upstream and tools ending in `_export`. Alternatively, use
`match.tool: "*"` with `action_class_in: [audit.export]` if your tools are registered
with that verb.

### Step 3 — Translate conditions

```yaml
when:
  - condition: time_of_day_outside
    start: "08:00"
    end: "18:00"
    tz: "UTC"
```

### Step 4 — Write the file

`policies/after-hours-export-block.yaml`:

```yaml
id: after-hours-export-block
name: After-Hours Export Block
description: >
  Block data export operations on the analytics upstream outside business hours
  (08:00-18:00 UTC). Prevents runaway agents from triggering large exports at night.
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

### Step 5 — Lint and test

```
tessera policy lint --policy-dir policies/
```

Write pass and fail fixtures under
`tests/fixtures/policies/after-hours-export-block/{pass,fail}/` and run:

```
tessera policy test --policy-dir policies/ \
  --fixture-dir tests/fixtures/policies/after-hours-export-block/
```

### Step 6 — Deploy

Drop the file into `policies.dir`. With `reload: watch`, Tessera picks it up within
seconds. Check `/healthz` for `policy_state.errored` to confirm clean load.

### Authoring tips

- Set `priority >= 90` for security-critical rules that must fire before broad-match
  ones.
- Always include a `reason` string — it appears in JSON-RPC error bodies, audit events,
  and `X-Tessera-Reason` headers in `log_only` mode.
- Author under `mode: log_only` first. Review `X-Tessera-Decision: would_block` headers
  on live traffic to confirm the policy matches what you expect before flipping to
  `enforcement`.
- Use `arg: "*"` in `arg_matches_regex` to scan all arguments; watch for
  `regex_timeout` warnings in audit logs if the regex is slow on large payloads.
- Register custom tools in `policies/_action_verbs.yaml` so `action_class_in` works
  for them.

---

## 5. Intent declarations

Tessera supports intent-aware and intent-blind agents simultaneously in a single
deployment without reconfiguration.

### Intent-aware agents

An intent-aware agent populates `_meta.<intent.meta_key>` (default `tessera_intent`)
on every `tools/call`:

```json
{
  "params": {
    "name": "aws_s3_list_buckets",
    "arguments": {},
    "_meta": {
      "tessera_intent": {
        "verbs": ["read.list"],
        "purpose": "Inventory S3 buckets for the cost-attribution report."
      }
    }
  }
}
```

`verbs` is required when an intent block is present; `purpose` is optional (max 1024
characters). When intent is present, `intent_class_in` and `intent_purpose_matches`
conditions evaluate against the declared values, and policies with
`match.require_intent: true` are eligible for evaluation.

Intent-aware policies provide the strongest enforcement guarantees: a policy can combine
`action_class_in` (what the tool does) with `intent_class_in` (what the agent says it
is doing) to detect mismatches and block suspicious calls.

### Intent-blind agents

Off-the-shelf MCP clients — Cursor, Claude Desktop, Windsurf — do not populate
`_meta.tessera_intent` in their standard configurations. Tessera handles both modes
simultaneously:

- Policies with `match.require_intent: true` are **silently skipped** for calls without
  intent. Intent-specific rules do not accidentally block standard clients.
- Policies with `match.require_intent: false` (the default) evaluate normally against
  tool name, arguments, action verbs, time, and all other non-intent conditions.
- `intent_class_in` and `intent_purpose_matches` evaluate to `false` for intent-blind
  calls (fail-closed on missing data).

A single `policies.dir` therefore serves mixed deployments — some agents declaring
intent, others not — without forking the policy set.

### Strict mode

Set `intent.required: true` in `tessera.yaml` to enforce intent presence globally. Every
`tools/call` without a valid intent block is rejected with reason `intent_required`,
regardless of `match` clauses. Enable this only when all agents in the deployment are
known to be intent-aware; it blocks all intent-blind clients including standard MCP
tools.

---

## 6. Composition limitations in v0.1

Tessera v0.1 evaluates policies as an ordered, first-match-wins list of independent
YAML files. Several composition features common in mature policy systems are explicitly
deferred to future releases.

### No Rego / OPA

Policy evaluation is pure Python. There is no Open Policy Agent dependency and no
support for inline Rego files alongside YAML. Rego was evaluated for v0.1 and deferred:
it adds approximately 100 MB to the container image, and the 16-condition YAML set
covers the intended v0.1 use cases. A Rego escape hatch would need to be authored fresh
(the prototype `_keep/rego/main.rego` was tied to an executor-style input shape that no
longer exists). Tracked as a v0.2 deliverable. See [docs/ROADMAP.md](ROADMAP.md).

### No policy namespacing

All policies in `policies.dir` share a flat namespace. There is no concept of policy
groups, tenants, or per-scope isolation. Every loaded policy potentially applies to every
request. Namespacing and multi-scope evaluation are a v0.2 cloud concern. See
[docs/ROADMAP.md](ROADMAP.md).

### No policy chaining or inheritance

Policies cannot reference other policies by ID. There is no `extends`, `import`, or
`compose` directive. Each YAML file is a self-contained rule. The only intra-policy
composition is within a single `when` list via `any_of` and `none_of`. A policy graph
with chaining, inheritance, or parameterized templates is deferred to v0.2. See
[docs/ROADMAP.md](ROADMAP.md).

### No per-policy version pinning or signed bundles

The policy directory is trusted as-is. There is no mechanism to pin a policy to a schema
version, sign files cryptographically, or verify bundle integrity at load time. OSS users
own and control their `policies.dir`. Signed bundles are a v0.2 deliverable.

### No native rate limiting

Policies cannot enforce rate limits. A misbehaving agent can call the proxy at arbitrary
frequency. Deploy Tessera behind nginx, Caddy, Cloudflare, or AWS API Gateway for rate
limiting until v0.2 ships native support. See [docs/ROADMAP.md](ROADMAP.md).

### Summary

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
