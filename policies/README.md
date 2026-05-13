# Tessera Reference Policies

These 14 policies ship with Tessera as a mode-agnostic starting library. Copy the ones you need into your deployment's `policies/` directory and tune thresholds to match your requirements.

## How policies behave

Policies are **mode-agnostic** — they make the same decisions across `enforcement`, `log_only`, and `observation` modes. The mode only changes what Tessera does with the decision (block vs log vs trace). Deploy in `log_only` first to observe would-block counts before flipping to `enforcement`.

The `action_class_in` condition relies on the built-in verb registry in [`tessera/policy/action_verbs.py`](../tessera/policy/action_verbs.py). Tools not in the registry return an empty verb set, so `action_class_in` conditions will not match unknown tools. Add custom tool → verb mappings via `policies/_action_verbs.yaml`.

## At-a-glance

| File | ID | Priority | Action |
|---|---|---|---|
| `owasp-mcp-tool-poisoning.yaml` | `owasp-mcp-tool-poisoning` | 110 | block |
| `pii-block.yaml` | `pii-block` | 100 | block |
| `postgres-mcp-protection.yaml` | `postgres-mcp-protection` | 96 | block |
| `secret-leak-block.yaml` | `secret-leak-block` | 95 | block |
| `owasp-mcp-prompt-injection.yaml` | `owasp-mcp-prompt-injection` | 95 | block |
| `github-mcp-protection.yaml` | `github-mcp-protection` | 95 | block |
| `jira-mcp-protection.yaml` | `jira-mcp-protection` | 92 | block |
| `prod-protection.yaml` | `prod-protection` | 90 | block |
| `slack-mcp-protection.yaml` | `slack-mcp-protection` | 90 | block |
| `salesforce-mcp-protection.yaml` | `salesforce-mcp-protection` | 85 | require_approval |
| `data-residency-eu.yaml` | `data-residency-eu` | 80 | block |
| `write-action-approval.yaml` | `write-action-approval` | 70 | require_approval |
| `read-only-mode.yaml` | `read-only-mode` | 60 | block |
| `cost-cap.yaml` | `cost-cap` | 50 | block |

---

## Generic policies (7)

### `prod-protection.yaml`

**ID**: `prod-protection` · **Priority**: 90 · **Action**: `block`

> Block destructive write operations when targeting production resources.

```yaml
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
```

**When to use this** — Turn on the moment an agent can talk to your infrastructure (Terraform, Pulumi, AWS CLI, kubectl, cloud-provider MCP). Catches the most common destructive-call failure mode: the agent picks the wrong environment. Pair with `write-action-approval` if you want a human gate instead of an outright block. Tune by extending the `any_of` block with your own environment-naming conventions (e.g. `live`, `production-us`).

---

### `secret-leak-block.yaml`

**ID**: `secret-leak-block` · **Priority**: 95 · **Action**: `block`

> Block tool calls where arguments appear to contain API keys or tokens.

```yaml
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
```

**When to use this** — Turn on whenever an agent can read source files, environment configs, or cloud-secret-manager outputs. Three patterns covered out of the box: AWS access keys (AKIA/ASIA), OpenAI-style keys (`sk-` / `pk-`), and GitHub personal access tokens (`ghp_`, `gho_`, etc). Extend the `any_of` block with patterns for additional secret shapes in your stack — GCP service-account JSON, Azure connection strings, Stripe `sk_live_*`, Slack `xoxb-*`.

---

### `pii-block.yaml`

**ID**: `pii-block` · **Priority**: 100 · **Action**: `block`

> Block tool calls with arguments matching PII patterns (SSN, credit card numbers).

```yaml
when:
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: "*"
        pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
      - condition: arg_matches_regex
        arg: "*"
        pattern: "\\b4[0-9]{12}(?:[0-9]{3})?\\b"
```

**When to use this** — Turn on for any workload that touches customer data, support tickets, CRM records, or healthcare. Covers US SSN (XXX-XX-XXXX) and Visa credit-card shapes only — intentionally narrow to avoid false positives. Extend with `arg_matches_regex` conditions for email, phone, passport, and your domain-specific PII (member IDs, account numbers). Often paired with `data-residency-eu` for GDPR-adjacent workloads.

---

### `cost-cap.yaml`

**ID**: `cost-cap` · **Priority**: 50 · **Action**: `block`

> Block tool calls that exceed per-request cost thresholds.

```yaml
when:
  - condition: any_of
    conditions:
      - condition: arg_greater_than
        arg: max_tokens
        value: 100000
      - condition: arg_greater_than
        arg: estimated_cost_usd
        value: 1.0
```

**When to use this** — Turn on for any agent that can call LLM APIs, BigQuery, AWS Lambda invocations, or anything with per-request cost. The tool call must expose `max_tokens` or `estimated_cost_usd` as args; if your wrapper doesn't surface these, add a wrapper that does. Tune the two thresholds to match your per-call budget. For tier-based budgets (e.g. "free users: $0.10/call, paid users: $5/call"), copy the policy and gate it on the user tier with `arg_equals`.

---

### `read-only-mode.yaml`

**ID**: `read-only-mode` · **Priority**: 60 · **Action**: `block`

> Allow only read operations; block everything else.

```yaml
when:
  - condition: none_of
    conditions:
      - condition: action_class_in
        values: ["read.list", "read.describe", "read.get", "read.search", "read.aggregate"]
```

**When to use this** — Turn on for "explore-only" sessions or read-only audits: the agent can list, get, describe, search, aggregate, but not write/delete/create anything. Useful as a kill-switch when you don't yet trust an agent, or as a per-persona constraint (e.g. analyst personas vs operator personas). Drop it into your policy bundle and remove when you're ready to authorize writes.

---

### `write-action-approval.yaml`

**ID**: `write-action-approval` · **Priority**: 70 · **Action**: `require_approval`

> Require human approval for all write and delete operations.

```yaml
when:
  - condition: action_class_in
    values: ["write.create", "write.update", "write.delete"]
```

**When to use this** — Turn on when you want a human gate on every write, not an outright block. Hooks into the approval pipeline configured in `tessera.yaml` (Slack, PagerDuty, manual CLI approval, etc). Useful for compliance-flavored workloads where every change must be approved-by-name. Pair with `prod-protection` if you want approvals only for production writes — `prod-protection` runs first by priority and short-circuits the approval flow for the cases it owns.

---

### `data-residency-eu.yaml`

**ID**: `data-residency-eu` · **Priority**: 80 · **Action**: `block`

> Ensure data operations stay within EU regions.

```yaml
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
```

**When to use this** — Turn on for EU-data workloads (GDPR, Schrems II, customer contractual commitments). Blocks tool calls whose `region` or `location` argument doesn't start with `eu-` (AWS) or `europe-` (GCP). For other residency boundaries (US-only, India-only, etc.), copy the policy file, rename it, and rewrite the allowlist — Tessera ships only the EU boundary out of the box. Tune by adding region-prefix patterns specific to your providers (e.g. `"europe-west"`).

---

## Vendor-specific MCP guardrails (7)

### `github-mcp-protection.yaml`

**ID**: `github-mcp-protection` · **Priority**: 95 · **Action**: `block`

> Block destructive GitHub MCP operations on protected branches or production repos.

```yaml
when:
  - condition: tool_name_in
    values:
      - "mcp__github__delete_repo"
      - "mcp__github__force_push"
      - "mcp__github__delete_branch"
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: branch
        pattern: "^(main|master|prod-[^/]{1,100}|release-[^/]{1,100})$"
      - condition: arg_matches_regex
        arg: repo
        pattern: "^[^/]{0,200}-prod$|^[^/]{0,200}-production$"
```

**When to use this** — Turn on whenever an agent has the [GitHub MCP server](https://github.com/github/github-mcp-server) attached. Specifically catches `delete_repo`, `force_push`, and `delete_branch` against `main`/`master`/`prod-*`/`release-*` branches, or any repo whose name ends in `-prod` / `-production`. Tune by extending the branch regex to cover your additional protected names (e.g. `hotfix-.*`, `customer-.*`) or by adding prefix patterns to the repo regex.

---

### `jira-mcp-protection.yaml`

**ID**: `jira-mcp-protection` · **Priority**: 92 · **Action**: `block`

> Block Jira MCP operations on security-critical tickets and guard against the August 2025 Cursor+Jira 0-Click `_meta` smuggling attack pattern.

```yaml
when:
  - condition: tool_name_in
    values:
      - "mcp__jira__update_issue"
      - "mcp__jira__delete_issue"
      - "mcp__jira__create_issue"
      - "mcp__jira__transition_issue"
      - "mcp__jira__add_comment"
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: issue_key
        pattern: "(?i)^(prod|security)-[0-9]{1,10}$"
      - condition: arg_matches_regex
        arg: summary
        pattern: "(?i)(prod|security)-[0-9]{1,10}"
      - condition: arg_matches_regex
        arg: description
        pattern: "_meta\\s{0,10}[:=]\\s{0,10}\\{"
```

**When to use this** — Turn on whenever an agent has a Jira MCP server attached. Covers two distinct threats: (1) destructive operations on `prod-*` or `security-*` tickets, and (2) the **August 2025 Cursor+Jira 0-Click** prompt-injection attack where a malicious ticket smuggles `_meta: { ... }` into the description to coerce the agent into leaking secrets. Tune by adding your sensitive project-key prefixes to the alternation (e.g. `infra`, `soc`, `legal`) and tightening the numeric bound `{1,10}` if your project keys have a stricter format.

---

### `postgres-mcp-protection.yaml`

**ID**: `postgres-mcp-protection` · **Priority**: 96 · **Action**: `block`

> Block Postgres MCP DROP, TRUNCATE, and ALTER operations on critical tables.

```yaml
when:
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: query
        pattern: "(?i)\\b(DROP|TRUNCATE|ALTER)\\b\\s{0,30}\\b(TABLE\\s{0,30})?(IF\\s{0,30}EXISTS\\s{0,30})?[\"'`]?(prod_\\w{1,100}|users|customers|payment_methods|sessions)[\"'`]?"
      - condition: arg_matches_regex
        arg: sql
        pattern: "(?i)\\b(DROP|TRUNCATE|ALTER)\\b\\s{0,30}\\b(TABLE\\s{0,30})?(IF\\s{0,30}EXISTS\\s{0,30})?[\"'`]?(prod_\\w{1,100}|users|customers|payment_methods|sessions)[\"'`]?"
```

**When to use this** — Turn on whenever an agent has a Postgres MCP server attached, or any tool that runs arbitrary SQL. Blocks `DROP`, `TRUNCATE`, and `ALTER` against `prod_*` tables and the five canonical pivots: `users`, `customers`, `payment_methods`, `sessions`. The regex covers both `query` and `sql` arg names — if your tool uses something else (e.g. `statement`), add a third `arg_matches_regex` condition under `any_of`. Tune the table alternation to include the specific table names that matter in your schema.

---

### `salesforce-mcp-protection.yaml`

**ID**: `salesforce-mcp-protection` · **Priority**: 85 · **Action**: `require_approval`

> Block (via approval) Salesforce MCP delete and update operations on production org IDs.

```yaml
when:
  - condition: tool_name_in
    values:
      - "mcp__salesforce__delete_record"
      - "mcp__salesforce__delete_contact"
      - "mcp__salesforce__delete_account"
      - "mcp__salesforce__update_record"
      - "mcp__salesforce__update_contact"
      - "mcp__salesforce__update_account"
  - condition: arg_in_set
    arg: org_id
    values:
      - "00D000000000001"  # TEMPLATE: replace with actual production org ID
      - "00D000000000002"  # TEMPLATE: add more as needed
```

**When to use this** — Turn on whenever an agent has a Salesforce MCP server attached. **Requires customization before deployment** — the org IDs in the shipped file are templates (`00D000000000001` / `00D000000000002`). Replace them with your actual production Salesforce org IDs (find the format at [help.salesforce.com](https://help.salesforce.com/s/articleView?id=000385587)). The action is `require_approval` rather than `block` so a human can confirm legitimate ops; flip to `block` if you want an outright denial.

---

### `slack-mcp-protection.yaml`

**ID**: `slack-mcp-protection` · **Priority**: 90 · **Action**: `block`

> Block Slack MCP message operations to public channels containing sensitive content.

```yaml
when:
  - condition: tool_name_in
    values:
      - "mcp__slack__chat_postMessage"
      - "mcp__slack__chat_update"
      - "mcp__slack__conversations_setTopic"
  - condition: arg_in_set
    arg: channel
    values: ["#general", "#announcements", "#engineering", "#all-hands",
             "general", "announcements", "engineering", "all-hands"]
  - condition: any_of
    conditions:
      - condition: arg_matches_regex
        arg: text
        pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
      - condition: arg_matches_regex
        arg: text
        pattern: "\\b4[0-9]{12}(?:[0-9]{3})?\\b"
      - condition: arg_matches_regex
        arg: text
        pattern: "(?i)(api_key|apikey|secret|password|token)\\s*[:=]\\s*[a-z0-9_\\-]{8,}"
      - condition: arg_matches_regex
        arg: text
        pattern: "-----BEGIN (?:RSA )?PRIVATE KEY-----"
```

**When to use this** — Turn on whenever an agent can post to Slack. Three guardrails compose: (1) the call is a write to Slack, AND (2) the target channel is one of your "front-page" public channels, AND (3) the message text contains PII (SSN, CC), API-key-shaped strings, or a PEM private-key header. Tune the `channel` set to match your workspace's actual high-visibility channels and extend the `any_of` block for additional sensitive-content patterns (DB connection strings, JWTs).

---

### `owasp-mcp-prompt-injection.yaml`

**ID**: `owasp-mcp-prompt-injection` · **Priority**: 95 · **Action**: `block`

> Block tool calls whose arguments contain prompt-injection patterns, including instruction override phrases, system prompt mentions, heredoc-style markers, base64 payload smuggling, and the Cursor+Jira `_meta` field injection vector (August 2025).

```yaml
when:
  - condition: any_of
    conditions:
      - condition: arg_contains_pattern
        arg: "*"
        pattern: "(?i)ignore previous instructions"
      - condition: arg_contains_pattern
        arg: "*"
        pattern: "(?i)system prompt"
      - condition: arg_contains_pattern
        arg: "*"
        pattern: "(?i)override (?:the |your )?safety"
      - condition: arg_contains_pattern
        arg: "*"
        pattern: "(?i)override your"
      - condition: arg_matches_regex
        arg: "*"
        pattern: '<<[^>]{0,50}EOT'
      - condition: arg_matches_regex
        arg: "*"
        pattern: '[A-Za-z0-9+/]{200,}={0,2}'
      - condition: arg_matches_regex
        arg: "*"
        pattern: '_meta\s{0,10}[:=]\s{0,10}\{'
```

**When to use this** — Turn on as a universal baseline for any MCP-connected agent. Catches the common prompt-injection patterns named in the OWASP MCP Top 10: instruction overrides ("ignore previous instructions"), system-prompt mentions, safety overrides, heredoc smuggling (`<<EOT`), base64-encoded payloads (≥200 chars), and the `_meta: {` field-injection pattern from the August 2025 Cursor+Jira attack. Expect a small false-positive rate; deploy in `log_only` mode first to size it. Tune by adding patterns specific to your threat landscape and by lengthening the base64-length bound if your normal traffic includes legitimate long base64 payloads.

---

### `owasp-mcp-tool-poisoning.yaml`

**ID**: `owasp-mcp-tool-poisoning` · **Priority**: 110 · **Action**: `block`

> Block MCP tool calls whose name matches known impostor namespaces or typo-squatting patterns.

```yaml
match:
  upstream: "*"
  tool_pattern: "mcp__(github_official|github_v\\d|git_hub|slack_app|jira_official|g[01]thub|sl4ck|slck|jra|jlra)__"
action: block
```

**When to use this** — Turn on as a universal baseline for any MCP-connected agent. Blocks tool calls whose name impersonates a legitimate provider via typo-squatting (`g0thub`, `g1thub`, `sl4ck`, `slck`, `jra`, `jlra`) or lookalike namespacing (`github_official`, `github_v<N>`, `git_hub`, `slack_app`, `jira_official`). Highest priority in the shipped set (110) so it short-circuits before any vendor policy runs. Tune by extending the `tool_pattern` alternation when new impostor variants surface in your threat intel — e.g. add `confluence_official`, `jira_v\d` as they emerge.

---

## Tuning checklist

When forking these policies to your environment:

- **`prod-protection`** — extend the `any_of` block to match your environment naming conventions.
- **`secret-leak-block`** — extend the `any_of` block with patterns for your specific secret shapes (GCP, Azure, Stripe, Slack).
- **`pii-block`** — add `arg_matches_regex` conditions for email, phone, passport, and any domain-specific PII.
- **`cost-cap`** — adjust `max_tokens` and `estimated_cost_usd` thresholds to match your per-call budget.
- **`data-residency-eu`** — add region-prefix patterns specific to your providers (e.g. `"europe-west"` for GCP). For other residency boundaries, copy and rewrite.
- **`github-mcp-protection`** — extend the protected-branch regex (e.g. `hotfix-.*`) and the repo regex prefix-side (e.g. `prod-.*`).
- **`jira-mcp-protection`** — extend the `issue_key` / `summary` alternation with additional sensitive project-key prefixes. Tighten `{1,10}` if your project keys use a stricter format.
- **`postgres-mcp-protection`** — extend the critical-table alternation; add a third `arg_matches_regex` if your tool uses an arg name other than `query` or `sql`.
- **`salesforce-mcp-protection`** — **mandatory** — replace template org IDs with actual production Salesforce org IDs. Extend `tool_name_in` with any additional destructive Salesforce MCP tools in your environment.
- **`slack-mcp-protection`** — extend the `channel` set with your workspace's high-visibility channels; add patterns for additional sensitive-content shapes.
- **`owasp-mcp-tool-poisoning`** — extend the `tool_pattern` alternation with additional lookalike namespace fragments matching your threat model.
