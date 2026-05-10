# Tessera Reference Policies

These 7 policies ship with Tessera as a mode-agnostic starting library. Copy the ones you need into your deployment's `policies/` directory and tune thresholds to match your requirements.

## Policies

| File | ID | Priority | Action | Purpose |
|------|----|----------|--------|---------|
| `cost-cap.yaml` | `cost-cap` | 50 | block | Block calls exceeding max_tokens > 100,000 or estimated_cost_usd > $1.00 |
| `prod-protection.yaml` | `prod-protection` | 90 | block | Block destructive operations (write.delete, execute.deploy) targeting production |
| `data-residency-eu.yaml` | `data-residency-eu` | 80 | block | Block data write/read operations where region/location arg is outside EU |
| `pii-block.yaml` | `pii-block` | 100 | block | Block calls where any argument matches SSN or Visa credit card number patterns |
| `write-action-approval.yaml` | `write-action-approval` | 70 | require_approval | Require human approval for all write.create / write.update / write.delete operations |
| `read-only-mode.yaml` | `read-only-mode` | 60 | block | Block any operation that is not a read (read.list / read.describe / read.get / read.search / read.aggregate) |
| `secret-leak-block.yaml` | `secret-leak-block` | 95 | block | Block calls where any argument contains AWS access keys, OpenAI/Anthropic keys, or GitHub tokens |

## Usage

These policies are **mode-agnostic** — they behave consistently across `enforcement`, `log_only`, and `observation` modes. Deploy in `log_only` first to observe would-block counts before flipping to `enforcement`.

The `action_class_in` condition relies on the built-in verb registry in `tessera/policy/action_verbs.py`. Tools not in the registry return an empty verb set, so `action_class_in` conditions will not match unknown tools. Add custom tool → verb mappings via `policies/_action_verbs.yaml`.

## Tuning

- **cost-cap**: adjust `max_tokens` and `estimated_cost_usd` thresholds.
- **prod-protection**: extend the `any_of` block to match additional environment naming conventions.
- **data-residency-eu**: add further `region_in` conditions to cover additional EU region prefix patterns (e.g., `"europe-west"` for GCP).
- **pii-block**: add `arg_matches_regex` conditions for additional PII patterns (email, phone, passport numbers).
- **secret-leak-block**: extend the `any_of` block with additional secret patterns for your specific tool ecosystem.
