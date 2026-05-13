# Tessera Reference Policies — v0.2.0

Tessera ships **12 reference policies**: 7 generic mode-agnostic guardrails + 5
AWS-illustrative examples. Copy the ones you need into your deployment's
`policies/` directory and tune thresholds to match your requirements.

The 7 vendor-specific policies (GitHub, Jira, OWASP prompt injection, OWASP tool
poisoning, Postgres, Salesforce, Slack) have migrated to the
[**Tessera Cloud premium pack**](https://cloudmorph.ai/tessera/packs):
`tessera-intelligence/packs/vendor-mcp-protection/v1.0.0/`. They are no longer
shipped in the OSS repo.

## How policies behave

Policies are **mode-agnostic** — they make the same decisions across `enforcement`,
`log_only`, and `observation` modes. The mode only changes what Tessera does with
the decision (block vs log vs trace). Deploy in `log_only` first to observe
would-block counts before flipping to `enforcement`.

The `action_class_in` condition relies on the built-in verb registry in
[`tessera/policy/action_verbs.py`](../tessera/policy/action_verbs.py). Tools not
in the registry return an empty verb set, so `action_class_in` conditions will not
match unknown tools. Add custom tool → verb mappings via `policies/_action_verbs.yaml`.

## At-a-glance

| File | ID | Priority | Action |
|---|---|---|---|
| `pii-block.yaml` | `pii-block` | 100 | block |
| `secret-leak-block.yaml` | `secret-leak-block` | 95 | block |
| `aws-cost-runaway-stop-EXAMPLE.yaml` | `aws-cost-runaway-stop-example` | 95 | block |
| `aws-iam-blast-radius-EXAMPLE.yaml` | `aws-iam-blast-radius-example` | 90 | block |
| `prod-protection.yaml` | `prod-protection` | 90 | block |
| `aws-ec2-cost-cap-EXAMPLE.yaml` | `aws-ec2-cost-cap-example` | 80 | block |
| `aws-bedrock-cost-ceiling-EXAMPLE.yaml` | `aws-bedrock-cost-ceiling-example` | 80 | block |
| `data-residency-eu.yaml` | `data-residency-eu` | 80 | block |
| `aws-region-allowlist-EXAMPLE.yaml` | `aws-region-allowlist-example` | 70 | block |
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

**When to use this** — Turn on the moment an agent can talk to your infrastructure.
Catches the most common destructive-call failure mode: the agent picks the wrong
environment. Pair with `write-action-approval` if you want a human gate instead of
an outright block.

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

**When to use this** — Turn on whenever an agent can read source files, environment
configs, or cloud-secret-manager outputs.

---

### `pii-block.yaml`

**ID**: `pii-block` · **Priority**: 100 · **Action**: `block`

> Block tool calls with arguments matching PII patterns (SSN, credit card numbers).

---

### `cost-cap.yaml`

**ID**: `cost-cap` · **Priority**: 50 · **Action**: `block`

> Block tool calls that exceed per-request cost thresholds (`max_tokens` or `estimated_cost_usd`).

---

### `read-only-mode.yaml`

**ID**: `read-only-mode` · **Priority**: 60 · **Action**: `block`

> Allow only read operations; block everything else.

---

### `write-action-approval.yaml`

**ID**: `write-action-approval` · **Priority**: 70 · **Action**: `require_approval`

> Require human approval for all write and delete operations.

---

### `data-residency-eu.yaml`

**ID**: `data-residency-eu` · **Priority**: 80 · **Action**: `block`

> Ensure data operations stay within EU regions (AWS `eu-*` / GCP `europe-*`).

---

## AWS-illustrative policies (5)

These are **illustrative examples** showing how Tessera's semantic conditions
(`predicted_cost`, `blast_radius`, `cumulative_spend_today`, `region_in`) map to
real AWS scenarios. They require the `predicted_cost` / `blast_radius` /
`cumulative_spend_today` condition implementations to be present — shipped in the
**Tessera Cloud premium pack** `aws-cost-aware-defaults`.

To use them as-is in `log_only` mode for testing, copy them to your `policies/`
dir; conditions that cannot be evaluated return `false` (fail-open for unknown
conditions), so they will not block in the absence of the premium-pack conditions.

### `aws-ec2-cost-cap-EXAMPLE.yaml`

**ID**: `aws-ec2-cost-cap-example` · **Priority**: 80 · **Action**: `block`

> Block EC2 RunInstances calls predicted to cost more than $50/hr.

---

### `aws-iam-blast-radius-EXAMPLE.yaml`

**ID**: `aws-iam-blast-radius-example` · **Priority**: 90 · **Action**: `block`

> Block IAM policy changes (PutRolePolicy / AttachRolePolicy) affecting > 100 principals.

---

### `aws-region-allowlist-EXAMPLE.yaml`

**ID**: `aws-region-allowlist-example` · **Priority**: 70 · **Action**: `block`

> Block operations outside EU regions (illustrative data-residency policy for AWS).

---

### `aws-cost-runaway-stop-EXAMPLE.yaml`

**ID**: `aws-cost-runaway-stop-example` · **Priority**: 95 · **Action**: `block`

> Halt all cost-incurring calls when cumulative daily spend on the scope exceeds $500.

---

### `aws-bedrock-cost-ceiling-EXAMPLE.yaml`

**ID**: `aws-bedrock-cost-ceiling-example` · **Priority**: 80 · **Action**: `block`

> Block Bedrock InvokeModel calls whose ceiling cost estimate exceeds $1.50.

---

## Vendor-specific policies (premium pack)

The 7 vendor-specific guardrails (GitHub, Jira, OWASP prompt injection, OWASP tool
poisoning, Postgres, Salesforce, Slack) were part of the OSS v0.1.x release. As of
v0.2.0 they live in the **Tessera Cloud premium pack**
`tessera-intelligence/packs/vendor-mcp-protection/v1.0.0/` (per OQ-3).

Access them via `tessera intelligence pull vendor-mcp-protection` (requires a
Tessera Cloud license). They are identical in content to the v0.1.x OSS versions;
the migration is structural, not a quality downgrade.

---

## Tuning checklist

- **`prod-protection`** — extend the `any_of` block to match your environment naming conventions.
- **`secret-leak-block`** — extend the `any_of` block with your specific secret shapes.
- **`pii-block`** — add `arg_matches_regex` conditions for email, phone, passport, and domain-specific PII.
- **`cost-cap`** — adjust `max_tokens` and `estimated_cost_usd` thresholds to your budget.
- **`data-residency-eu`** — add region-prefix patterns for your providers; for other residency boundaries, copy and rewrite.
- **`aws-ec2-cost-cap-EXAMPLE`** — tune `usd_threshold` to your per-call EC2 budget.
- **`aws-iam-blast-radius-EXAMPLE`** — tune `principal_count_threshold` to your org size.
- **`aws-region-allowlist-EXAMPLE`** — change the `regions` list to your allowlisted prefixes.
- **`aws-cost-runaway-stop-EXAMPLE`** — tune `usd_threshold` to your daily spend cap.
- **`aws-bedrock-cost-ceiling-EXAMPLE`** — tune `usd_threshold` to your per-call Bedrock budget.
