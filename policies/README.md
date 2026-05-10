# Tessera Reference Policies

These 11 policies ship with Tessera as a mode-agnostic starting library. Copy the ones you need into your deployment's `policies/` directory and tune thresholds to match your requirements.

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
| `owasp-mcp-prompt-injection.yaml` | `owasp-mcp-prompt-injection` | 95 | block | Block calls where any argument contains prompt-injection phrases, heredoc markers, base64 payload smuggling, or the Cursor+Jira _meta field injection vector |
| `owasp-mcp-tool-poisoning.yaml` | `owasp-mcp-tool-poisoning` | 110 | block | Block calls to tools whose name matches known impostor namespaces or typo-squatting patterns (git_hub, github_official, sl4ck, g0thub, jra, etc.) |
| `github-mcp-protection.yaml` | `github-mcp-protection` | 95 | block | Block destructive GitHub MCP operations (delete_repo, force_push, delete_branch) on protected branches or production repos |
| `jira-mcp-protection.yaml` | `jira-mcp-protection` | 92 | block | Block Jira MCP operations on prod/security tickets and guard against the August 2025 Cursor+Jira 0-Click _meta smuggling attack pattern |
| `slack-mcp-protection.yaml` | `slack-mcp-protection` | 90 | block | Block Slack MCP message operations to public channels (#general, #announcements, #engineering, #all-hands) containing PII, secrets, or API keys |
| `salesforce-mcp-protection.yaml` | `salesforce-mcp-protection` | 85 | require_approval | Require approval for Salesforce MCP delete/update operations targeting production org IDs |
| `postgres-mcp-protection.yaml` | `postgres-mcp-protection` | 96 | block | Block Postgres MCP DROP, TRUNCATE, and ALTER DDL operations on critical tables (prod_*, users, customers, payment_methods, sessions) |

## Usage

These policies are **mode-agnostic** — they behave consistently across `enforcement`, `log_only`, and `observation` modes. Deploy in `log_only` first to observe would-block counts before flipping to `enforcement`.

The `action_class_in` condition relies on the built-in verb registry in `tessera/policy/action_verbs.py`. Tools not in the registry return an empty verb set, so `action_class_in` conditions will not match unknown tools. Add custom tool → verb mappings via `policies/_action_verbs.yaml`.

## Tuning

- **cost-cap**: adjust `max_tokens` and `estimated_cost_usd` thresholds.
- **prod-protection**: extend the `any_of` block to match additional environment naming conventions.
- **data-residency-eu**: add further `region_in` conditions to cover additional EU region prefix patterns (e.g., `"europe-west"` for GCP).
- **pii-block**: add `arg_matches_regex` conditions for additional PII patterns (email, phone, passport numbers).
- **secret-leak-block**: extend the `any_of` block with additional secret patterns for your specific tool ecosystem.
- **owasp-mcp-tool-poisoning**: extend `tool_pattern` alternation with additional lookalike namespace fragments matching your threat model (e.g. add `jira_v\d`, `confluence_official` as new impostor variants emerge).
- **github-mcp-protection**: extend the protected branch regex to cover additional naming conventions (e.g. `hotfix-.*`); extend the production repo regex to cover prefixes as well as suffixes (e.g. `prod-.*`).
- **jira-mcp-protection**: extend the `issue_key` / `summary` alternation with additional project key prefixes that are sensitive in your org (e.g. `infra`, `soc`); tighten the numeric bound `{1,10}` if your project keys use a stricter format.
- **slack-mcp-protection**: extend the `channel` set with additional public channel names used in your workspace; add further `arg_matches_regex` conditions under the `any_of` block for additional secret patterns (e.g. database connection strings, JWT tokens).
- **salesforce-mcp-protection**: replace the template org IDs (`00D000000000001`, `00D000000000002`) with your actual production Salesforce org IDs; extend the `tool_name_in` list with any additional destructive Salesforce MCP tools in your environment.
- **postgres-mcp-protection**: extend the critical table alternation to cover additional table names in your schema; if your Postgres MCP server uses an arg name other than `query` or `sql`, add a third `arg_matches_regex` condition under the `any_of` block.
