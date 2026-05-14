# Policy Engine

How YAML policy files become runtime block / allow / log_only / require_approval decisions. The engine is the pure-Python evaluation core that runs inside the proxy's hot path (see `proxy-enforcement-and-audit.md` for the surrounding flow). This document covers the schema, the condition catalog, the resolution order, the bundled defaults, and the regex-safety contract.

## Source-of-truth contract

The contract between user-authored YAML and the runtime is `schemas/policy.schema.json` (JSON-Schema Draft-07) and `tessera/policy/schema.py` (pydantic v2 models). They describe the same shape; pydantic is authoritative at load time, JSON-Schema is the external publishable form.

Every policy YAML is a mapping with these top-level fields:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string `[a-z0-9-]{1,64}` | Unique within the loaded set. Validated by `Policy.validate_id`. |
| `name` | string | Human-readable display name. |
| `description` | string | Optional context for operators. |
| `match` | `MatchSpec` | `upstream`, `tool` / `tool_pattern` (mutually exclusive), `require_intent`. |
| `when` | list of `ConditionType` | AND-combined conditions; first-match-wins across policies. |
| `action` | enum: `allow` / `block` / `log_only` / `require_approval` | What the engine returns. |
| `reason` | string | Surfaced in `error.data.reason` on block / audit events. |
| `priority` | int (default 0) | Higher fires first; alphabetical `id` as tie-breaker. |

`MatchSpec.tool` is a glob (`fnmatch` style — `"*"` matches all, `"aws_*"` is a prefix match). `MatchSpec.tool_pattern` is a regex evaluated via the `regex` library with a 100ms timeout. Setting both is a validation error.

Pydantic models reject extra keys (`extra="forbid"`) — a typo in a condition discriminator or a stray top-level field fails validation at load time rather than silently misbehaving.

## The 21-condition catalog

`tessera/policy/schema.py` defines a discriminated-union of 21 condition types. The discriminator field is `condition`; pydantic dispatches to the right model based on its literal value. Authoritative count is the entries in the `ConditionType` annotation and the corresponding entries in the `_DISPATCH` table at `tessera/policy/conditions.py:526`.

| Condition | Purpose | Notes |
|-----------|---------|-------|
| `arg_equals` | Argument matches a value | `arg: "*"` iterates all top-level args |
| `arg_greater_than` | Argument > value (numeric coercion) | Fail-closed on non-numeric |
| `arg_less_than` | Argument < value | Same |
| `arg_matches_regex` | Argument matches a regex | 100ms timeout per match |
| `arg_in_set` | Argument ∈ value list | |
| `arg_contains_pattern` | Alias of `arg_matches_regex` | Naming choice for readability |
| `arg_size_greater_than` | `len(json.dumps(arg))` > bytes | Bound on JSON-serialized payload size |
| `tool_name_in` | Tool name ∈ value list | |
| `action_class_in` | Tool's intent verbs ∩ values is non-empty | Verb registry: `tessera/policy/action_verbs.py:ACTION_VERBS` (50+ AWS/GCP/Azure/Databricks/Snowflake tools today) |
| `intent_class_in` | Declared intent verbs ∩ values is non-empty | Requires `_meta.tessera_intent` |
| `intent_purpose_matches` | Intent `purpose` field matches regex | Requires `_meta.tessera_intent.purpose` |
| `region_in` | Region prefix match | `arg` arg + `regions` list of prefixes |
| `time_of_day_outside` | Now ∉ [start, end] in tz | IANA tz; handles midnight wrap |
| `meta_field_equals` | `_meta.<dot.path>` equals value | Dot-path navigation |
| `any_of` | OR of inner conditions | Recursive |
| `none_of` | NOT (OR of inner conditions) | Recursive |
| `predicted_cost` | Estimated USD cost > / < / between threshold | Requires cost backend + AWS mapping; band-aware (high/medium/ceiling) |
| `blast_radius` | Affected-principal count > / < threshold | Requires `blast_radius_backend`; fail-closed = block |
| `affected_resource_count` | `len(jmespath(arg, args))` > / < threshold | JMESPath-driven |
| `data_volume` | Byte estimate > / < threshold | Three estimators: `static_arg_size`, `s3_get_byte_estimate`, `rds_query_result_estimate` |
| `cumulative_spend_today` | Per-scope daily spend > / < threshold | Requires `state_backend` (`DailySpendState`) |

The first 16 ship in v0.1; the last five are v0.2.0 semantic conditions that unlock cost-aware and blast-radius policies. The numbers in the v0.2.0 CHANGELOG entry for "5 new semantic conditions" enumerate `predicted_cost`, `blast_radius`, `affected_resource_count`, `cumulative_spend_today`, and `data_volume` — `time_of_day_outside` was already in v0.1 despite the CHANGELOG line that bundles it with the new five.

Dispatch is a single dict lookup in `_DISPATCH` keyed on `type(cond)`. Unknown condition types return `False` (fail-closed) rather than raising — defensive against engine/schema version skew during a rolling upgrade.

## Fail-direction policy per condition

Each condition declares a fail-direction when its dependencies are missing or its computation errors:

- **Cost-related conditions (`predicted_cost`, `cumulative_spend_today`)** — fail-closed in the **don't block** direction (return `False`). Missing cost data is not, by itself, grounds for blocking; a downstream policy can still block on operation shape, but a cost cap that can't read pricing must not stop a benign call.
- **`blast_radius`** — fail-closed in the **block** direction (return `True`). Uncertainty about how many principals an IAM mutation affects is treated as "potentially many." This is the inverse of cost-data uncertainty.
- **Regex evaluators (`arg_matches_regex`, `arg_contains_pattern`, `intent_purpose_matches`, `tool_pattern`)** — on timeout return `False` and accumulate a `regex_timeout:<policy_id>` entry into the thread-local `_decision_ctx.errors`. The engine surfaces this on the resulting `Decision.decision_error` so the audit event records `decision_error: regex_timeout` and the operator can see which policy timed out.
- **Unknown args, unknown intent fields, unknown verb names** — fail-closed `False`. A condition that names an arg the tool didn't send simply doesn't match.

The asymmetry between cost and blast-radius is deliberate. Cost over-blocks are operator-visible immediately (every call gets rejected). Blast-radius over-blocks fail in the conservative direction; under-blocks fail invisibly (a destructive IAM change slides through). The defaults pick block-on-uncertainty for the security-sensitive condition and allow-on-uncertainty for the cost-sensitive one.

## Resolution order

`PolicyEngine.evaluate(context)` walks the loader's pre-sorted policy list. The sort is descending `priority`, then ascending `id` as deterministic tiebreaker. First-match-wins:

```python
for policy in self._policies:
    if not match_upstream(policy.match.upstream, context["upstream"]):
        continue
    if not match_tool(policy.match.tool, policy.match.tool_pattern, tool_name):
        continue
    if policy.match.require_intent and context["intent"] is None:
        continue
    if evaluate_conditions(policy.when, eval_context):
        return Decision(action=policy.action, reason=policy.reason, policy_id=policy.id, ...)
return Decision(self._default_action, "default", None)
```

The match block runs three cheap filters (upstream, tool, intent-required) before evaluating the `when` list. Conditions inside `when` are AND-combined and short-circuit on the first `False`. Policies are processed in priority order and the first one whose match and when both succeed wins — no priority-aware combinator beyond that. Higher-priority policies cannot be combined with lower-priority ones; the matching policy's action stands.

When no policy matches, the engine returns `Decision(default_action, "default", None)` where `default_action` is the engine's constructor argument, populated from `policies.default_action` in `tessera.yaml` (defaults to `block` in production; `tessera policy test` defaults to `allow` to keep fixture decks usable without explicit overrides).

The first-match-wins choice is deliberate. Combinator approaches (deny-overrides, permit-overrides, ordered fall-through) are common in policy engines and produce more expressive policy sets, but they require the operator to reason about interactions. First-match-wins with explicit `priority` is mechanical: the policy that fires is the one with the highest priority whose conditions are all true; the operator can read the YAMLs in priority order and predict the decision at a glance.

## Lockdown short-circuit

Before the policy loop, the engine checks `context["runtime"]["lockdown"]`. When `runtime.lockdown: true`, every call returns `Decision(Action.block, "lockdown_active", None)` without consulting any policy. This is the incident-response kill-switch.

The lockdown flag is in `tessera.yaml` under `runtime.lockdown`. It's the only field that re-reads on SIGHUP without a full restart (per v0.1 docs); a `kill -HUP <pid>` toggles enforcement on or off instantly without dropping audit-chain state.

## Action verbs registry

`action_class_in` evaluates the tool name against a registry that maps tool names to a set of intent verbs (e.g., `aws_s3_delete_object` → `{write.delete}`, `aws_iam_create_user` → `{write.create}`). The 50+ entries in `tessera/policy/action_verbs.py:ACTION_VERBS` cover S3, EC2, ECS, IAM, Lambda, CloudFormation, CloudWatch, VPC, RDS, Secrets Manager, ELB on AWS; Storage, Compute, Run, Container on GCP; Blob, Compute, ContainerApps on Azure; Workspace, SQL, Unity Catalog on Databricks; Account, Database, Schema, SQL on Snowflake.

The taxonomy is locked to 20 verbs (`read.list`, `read.describe`, `read.get`, `read.search`, `read.aggregate`, `analyze`, `summarize`, `compare`, `write.create`, `write.update`, `write.delete`, `execute.run`, `execute.deploy`, `notify.send`, `notify.publish`, `escalate.approve`, `escalate.deny`, `audit.log`, `audit.export`, `simulate`, `dry_run`). Extending requires a major schema bump because policies consume verb names directly.

Custom MCP servers with new tool names extend the registry via `policies/_action_verbs.yaml`:

```yaml
mappings:
  my_custom_delete_tool: [write.delete]
  my_custom_query_tool: [read.list, read.aggregate]
```

`load_user_mappings()` parses the YAML, validates every verb against `KNOWN_VERBS`, and the proxy merges the result into a module-level `_user_mappings` dict. `verbs_for(action)` consults user mappings first, then builtins; the merged registry is consulted at evaluation time. Tools not in either return an empty frozenset — `action_class_in` does not match unknown tools (treated as ambiguous, fail-closed).

Naming convention is underscored (`aws_s3_list_buckets`), matching the convention used by the dominant open-source MCP servers. A defensive `.` → `_` fallback in `verbs_for` lets older dotted names still resolve, but this is a safety net, not a contract.

## Regex safety: ReDoS prevention

Every regex pattern in a loaded policy must pass `tessera/policy/regex_safety.py:validate_pattern` at load time. The corpus is five synthetic strings of increasing length (10, 100, ~1000, ~10000, ~99995 chars). Each is run through the pattern with a 100ms hard timeout; a pattern that takes ≥50ms on any corpus string is rejected with `PolicyError(reason="regex_potential_redos")`.

This catches catastrophic backtracking patterns at policy-load time, before they can degrade the live proxy. The `regex` library (not stdlib `re`) is used because it supports per-match timeouts via the `timeout=` kwarg. At runtime, every regex evaluation in `tessera/policy/conditions.py:_match_regex` is also wrapped in the 100ms timeout; on timeout the condition returns `False` and the audit event records `decision_error: regex_timeout`.

Load-time corpus tests are uniform across pattern fields: `match.tool_pattern`, `arg_matches_regex.pattern`, `arg_contains_pattern.pattern`, `intent_purpose_matches.pattern`. The same corpus catches the same class of bug regardless of where the pattern lives.

## Loader: per-file reload isolation

`tessera/policy/loader.py:FilesystemPolicyLoader` reads every `*.yaml` and `*.yml` from the policy directory. Files prefixed with `_` are skipped (config files like `_action_verbs.yaml` are loaded separately). The loader has two behaviors:

- **Startup load** — every file must parse, validate, and pass the regex-safety corpus. Any single failure raises `PolicyError`, the proxy exits with code 2, and `tessera serve` reports the failing file + line.
- **Reload (via SIGHUP or `watch` mode)** — files that fail to parse are logged and **skipped**; the previously-loaded version remains in memory. Files removed from disk are dropped from the registry. The healthz endpoint exposes `policy_state.errored` so operators see the failure without log access.

The reload isolation prevents a typo-in-one-policy from taking down the whole policy set, while ensuring startup is strict so misconfigurations are caught in CI rather than at runtime.

`watch` mode uses watchdog's `PollingObserver` (not inotify) so it works on container-mounted volumes where inotify is unreliable. Detection latency is 1–5 seconds depending on poll interval.

## Three enforcement modes

The mode is set in `tessera.yaml` under `policies.mode`:

- **`enforcement`** — engine result is honored: `block` → JSON-RPC error `-32603`; `require_approval` → JSON-RPC error `-32604`; `allow` / `log_only` → forward to upstream. This is production behavior.
- **`log_only`** — engine evaluates; decision is recorded as `would_decision` in the audit event; upstream is **always** forwarded regardless. Response carries `X-Tessera-Mode: log_only`, `X-Tessera-Decision` (`would_block`, `would_allow`, `no_match`), and on `would_block` also `X-Tessera-Policy-Id` and `X-Tessera-Reason` headers. The pattern is "deploy in log_only, observe would-block counts, flip to enforcement once the rate is acceptable."
- **`observation`** — engine is not invoked; every call is forwarded with a minimal audit event. Pure pass-through with audit. Used when an operator wants the audit log but no policy enforcement at all.

Modes are not SIGHUP-reloadable; switching modes requires a process restart. The default scaffolded by `tessera init` is `log_only` — `tessera serve` is safe to try without breaking real traffic.

The mode is a runtime knob over the same policies. Policies don't carry mode-specific logic; they make the same decisions regardless of mode. `policies/README.md` calls this "mode-agnostic policies": the mode changes what Tessera does with the decision (block vs log vs trace), not the decision itself.

## The 7 ship-with-package generic policies

`tessera/policies_default/*.yaml` and the dev-facing `policies/*.yaml` ship 7 generic mode-agnostic guardrails. They are described architecturally — the design intent, not the YAML body — below.

| Policy | Priority | Action | Design intent |
|--------|----------|--------|---------------|
| `pii-block` | 100 | block | Block tool calls where any argument matches a PII regex (SSN, credit-card, email patterns). Highest priority so it fires before any cost or routing policy. The threat is irreversible data exposure once an MCP call lands on a logging/store upstream. |
| `secret-leak-block` | 95 | block | Block calls where any argument matches a credential-shaped pattern (AWS access keys `AKIA|ASIA`, OpenAI `sk-`/`pk-`, GitHub `gh[pousr]_`). Catches the failure mode where an agent reads `.env` and pipes the secret into a tool call. |
| `prod-protection` | 90 | block | Block destructive verbs (`write.delete`, `execute.deploy`) when targeting resources whose `environment` arg is `production`/`prod` or whose `resource_name` matches `*-prod`. The most common production-safety failure: agent picks wrong environment. |
| `data-residency-eu` | 80 | block | Block writes that would land in non-EU regions. Used by EU-resident customers for GDPR data-flow constraints. Region matching uses `arg_in_set` on cloud-region args. |
| `write-action-approval` | 70 | require_approval | Escalate every `write.*` and `execute.deploy` verb to human approval in `enforcement` mode. The softer alternative to `prod-protection` when the operator wants a human in the loop rather than an outright block. |
| `read-only-mode` | 60 | block | Block any non-read verb across all upstreams. The "I trust this agent to look but not touch" posture; appropriate for analyst-style agents in production. |
| `cost-cap` | 50 | block | Block tool calls whose `max_tokens` or `estimated_cost_usd` exceed a per-request threshold. Generic in that it operates on convention-named args; the AWS-specific cost policies (below) use the semantic cost backend. |

The priorities are chosen so that security-critical policies (PII, secrets, prod) fire before resource-protection policies (residency, approval, read-only) which fire before cost policies. The hierarchy is: don't leak data → don't break prod → don't run up the bill.

The 7 are bundled inside the wheel via `[tool.setuptools.package-data]` so `tessera init` and `importlib.resources.files("tessera.policies_default")` both find them. The same content lives in the repo's top-level `policies/` for dev-time convenience; the wheel ships only the `policies_default` copy.

## The 5 AWS-illustrative `EXAMPLE` policies

Alongside the 7 production defaults, the package ships 5 AWS-illustrative example policies (filename suffix `-EXAMPLE.yaml`, IDs suffix `-example`). They demonstrate the v0.2.0 semantic conditions wired against AWS-specific tool names. They are illustrative — designed to be copied, tuned, and deployed — not enabled by default.

- `aws-ec2-cost-cap-EXAMPLE` (priority 80) — Block `aws_ec2_RunInstances` whose `predicted_cost` exceeds $50/hr (band: high).
- `aws-iam-blast-radius-EXAMPLE` (priority 90) — Block IAM mutations whose `blast_radius` exceeds 100 principals.
- `aws-region-allowlist-EXAMPLE` (priority 70) — Block any call whose `Region` arg is not in `[eu-west-1, eu-central-1, eu-north-1]`.
- `aws-cost-runaway-stop-EXAMPLE` (priority 95) — Block all cost-incurring calls when `cumulative_spend_today` exceeds $500. Highest priority of the AWS examples so it fires before the per-call cost cap.
- `aws-bedrock-cost-ceiling-EXAMPLE` (priority 80) — Block `aws_bedrock_InvokeModel` calls whose `predicted_cost` (band: ceiling) exceeds $1.50.

These illustrate, not enforce. They require the cost / blast-radius / state backends to be wired; when those backends are absent the fail-closed-for-cost rule keeps them inert (fail in the don't-block direction). Operators copy them, tune thresholds, and deploy.

The 7 vendor-specific policies that shipped in v0.1.x (`github-mcp-protection`, `jira-mcp-protection`, `owasp-mcp-prompt-injection`, `owasp-mcp-tool-poisoning`, `postgres-mcp-protection`, `salesforce-mcp-protection`, `slack-mcp-protection`) migrated to the Tessera Cloud premium pack `vendor-mcp-protection` per the OQ-3 decision (described in `tessera-intelligence/arch/status/policy-packs.md`). Customers reach them via `tessera intelligence pull vendor-mcp-protection` once their license JWT validates.

## Decision payload and audit linkage

`Decision` is a dataclass: `action`, `reason`, `policy_id`, `decision_error` (optional). The engine returns it; the proxy translates it into a JSON-RPC response and emits the corresponding audit event. The `decision_error` is non-None when something went wrong during evaluation (today: only regex timeouts surface here). The audit event payload mirrors these fields and the proxy injects the resulting `tessera_audit_event_id` into the response body at `result._meta` or `error.data._meta` so the agent can surface the event id back to the user. This linkage is what lets a customer correlate "the call was blocked" with "the audit event that records why."

## Intent extraction is independent of the engine

`tessera/intent.py:extract_intent()` runs in the proxy before the engine is invoked. It validates `_meta.tessera_intent` has a `verbs` list and an optional `purpose` string (≤ 1024 chars). The result is passed into the engine via `context["intent"]` and consumed by `intent_class_in` and `intent_purpose_matches` conditions. Intent extraction is structurally independent of policy evaluation — the engine never reads `_meta` directly, only the pre-extracted `intent` dict.

Off-the-shelf agents that don't supply intent simply produce `context["intent"] = None`, and policies that need intent are skipped via `match.require_intent: true`. This is the "intent-blind agent support" property: Cursor, Claude Desktop, and Windsurf work without modification.

## Cross-references

- For the surrounding request flow (where `engine.evaluate()` sits in the proxy): `proxy-enforcement-and-audit.md`.
- For where `predicted_cost` and `cumulative_spend_today` get their data: `integrations-and-cost.md`.
- For where `blast_radius` gets its principal counts: `integrations-and-cost.md` (production evaluator) and `tessera-intelligence/arch/status/blast-radius.md` (rule definitions + in-test stub).
- For the YAML schema as JSON Schema: `schemas/policy.schema.json`.
