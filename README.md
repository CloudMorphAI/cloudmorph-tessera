# Tessera

<!-- mcp-name: io.github.CloudMorphAI/tessera -->

**Runtime intelligent firewall for AI agent and MCP tool calls.**

[![PyPI version](https://img.shields.io/pypi/v/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![Python versions](https://img.shields.io/pypi/pyversions/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fcloudmorphai%2Ftessera-blue.svg)](https://ghcr.io/cloudmorphai/tessera)

Tessera is a deterministic in-process firewall that sits between an AI agent and every MCP server, evaluates each tool call against a YAML policy bench, and either forwards, blocks, or routes for approval — writing each decision to a hash-chained audit log.

## v0.7.0 benchmarks (single worker, loopback, 24 bundled policies)

| Metric | Value | Conditions |
|---|---|---|
| p50 HTTP cycle | **6.40 ms** | 10 concurrent conns, full proxy stack (auth + 24-policy eval + SQLite audit write) |
| p99 HTTP cycle | **12.30 ms** | 10 concurrent conns, SQLite write jitter is dominant |
| Sustained throughput | **2,009 RPS** | 200 concurrent conns, single uvicorn worker (linear with N workers behind nginx) |
| Engine-eval microbench | 25-86 µs | in-process only, no HTTP, no audit |
| HTTP overhead above engine | ~6.3 ms p50 | uvicorn + auth + audit write + JSON serde |

Hardware: Intel Core Ultra 5 115U (15W mobile chip), WSL2. Honest developer-hardware numbers — not inflated production claims. Full methodology: [benchmarks/results/v0.4.0-production.md](benchmarks/results/v0.4.0-production.md).

## Install + first block in 60 seconds

```bash
pip install cloudmorph-tessera
tessera init                                        # writes tessera.yaml + policies/ in cwd
TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" tessera serve
```

Tessera now listens on `http://127.0.0.1:8080/mcp`. Wire it into Cursor / Claude Code / your agent's MCP config with a single "tessera" entry (recipes in [recipes/](recipes/)), and every `tools/call` flows through 24 bundled defensive policies — `prod-protection`, `cost-cap`, `secret-leak-block`, `prompt-injection-heuristic`, `aws-mcp-passrole-guard`, plus 19 others. Tessera fans out tool discovery across all configured upstreams and namespaces tools as `<upstream>__<tool>` in the unified catalog.

---

## What this protects against

Concrete categories the 24 bundled policies cover out of the box:

- **Cost spikes** — `cost-cap`, `aws-bedrock-cost-ceiling-EXAMPLE`, `aws-cost-runaway-stop-EXAMPLE`, `aws-ec2-cost-cap-EXAMPLE`. Per-call ceiling, daily cumulative ceiling, model-specific Bedrock ceiling.
- **IAM blast-radius expansion** — `aws-mcp-passrole-guard`, `aws-mcp-admin-policy-deny`, `aws-mcp-create-access-key-deny`, `aws-iam-blast-radius-EXAMPLE`. PassRole approval gate, AWS-managed-admin attach hard-deny, access-key creation deny, principal-count guard.
- **Destructive operations on production** — `prod-protection`, `non-prod-only`, `write-action-approval`. Block by tag or name pattern, default-deny writes on prod, require human approval for delete-class actions.
- **Secret / PII exfiltration in arguments** — `secret-leak-block`, `pii-block`. Regex bench for API keys + tokens + SSN + credit-card numbers in tool-call args.
- **Prompt injection signals** — `prompt-injection-heuristic`. Regex bench for common jailbreak strings (`ignore previous`, `system: you are now`, etc.).
- **Region / data-residency violations** — `data-residency-eu`, `aws-region-allowlist-EXAMPLE`. Block ops outside permitted regions.
- **MCP server hygiene** — `aws-mcp-rds-public-deny`, `aws-mcp-ec2-imdsv1-deny`, `aws-mcp-kms-deletion-approval`. RDS public-access block, EC2 IMDSv1 deny, KMS deletion approval gate.

Vendor-specific packs (GitHub, Jira, Salesforce, Slack, Postgres, OWASP prompt injection, OWASP tool poisoning) are available via the Tessera Cloud premium pack `vendor-mcp-protection` — `tessera intelligence pull vendor-mcp-protection`.

## How it works

```
                ┌──────────────┐                ┌──────────────┐
   prompt  ───→ │   AI Agent   │ ─── MCP ──→    │   Tessera    │ ───→ MCP upstream
                │ (Claude /    │   tools/call   │  auth +      │      (AWS, GitHub,
                │  GPT / etc.) │                │  policy +    │ ◄─── Slack, your own)
                └──────────────┘                │  audit       │
                                                └──────┬───────┘
                                                       │ block / allow / require_approval
                                                       ▼
                                                  hash-chain audit log
```

Every inbound `POST /mcp` is:

1. **Authenticated** — bearer token matched; `AuthContext.scope` assigned (isolates audit streams per token).
2. **Evaluated** — policy engine walks the sorted set (descending `priority`, first-match-wins). Returns `allow`, `block`, `log_only`, or `require_approval`.
3. **Audited** — the decision is written to a SHA-256 hash-chain; `tessera audit verify` detects any tamper or gap.

In `enforcement` mode a `block` returns a JSON-RPC error and never touches the upstream. In `log_only` mode the upstream is always called and the decision rides in `X-Tessera-Decision` / `X-Tessera-Policy-Id` / `X-Tessera-Reason` response headers.

The engine is pure Python — no OPA, no LLM round-trip, no cloud credentials. Policy outcomes are deterministic.

## Tier levels

| Tier | Engine + 24 bundled policies | Premium packs |
|---|---|---|
| **Free** | Yes (local enforcement, hash-chain audit, multi-token scoping) | None |
| **Developer** | Yes | `aws-cost-aware-defaults`, `vendor-mcp-protection` |
| **Team** | Yes | + `hipaa-guardrails`, `pci-dss-controls` |
| **Enterprise** | Yes | All 12 packs (tri-cloud AWS+Azure+GCP), custom-pack authoring |

Premium packs are fetched from the Tessera Cloud CDN, Ed25519-signature-verified, and cached locally. Free-tier installs continue to enforce 24 bundled policies with no network calls.

## Installation

```bash
# Local development / CLI use
pip install cloudmorph-tessera

# With optional extras
pip install "cloudmorph-tessera[aws,gemini,intelligence,infracost,observability]"

# Production deploy (recommended)
docker pull ghcr.io/cloudmorphai/tessera:0.7.0
```

After install: `tessera version` prints `tessera 0.7.0`. Full install matrix + supported Python versions: [docs/INSTALL.md](docs/INSTALL.md).

## Wire it into Cursor

```bash
tessera serve --bind 127.0.0.1:8080
```

Then add to `~/.cursor/mcp.json` (macOS/Linux) or `%USERPROFILE%\.cursor\mcp.json` (Windows):

```json
{
  "mcpServers": {
    "tessera": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

Restart Cursor. Tessera appears in the MCP indicator. Every Cursor tool call is now policy-checked and audit-logged.

For Claude Code, Claude Desktop, VS Code Copilot, Continue, Cline, generic shell hooks, and the Cursor Hooks integration: [recipes/](recipes/) and [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Routing pattern — make Tessera the agent's default

Adding Tessera as one of many available MCP servers isn't enough — the agent will often prefer the direct cloud MCP server because it sees both. To make Tessera the default route, instruct the agent in its system context.

For Claude Code, drop a `CLAUDE.md` at project root:

```markdown
# Tool routing — use Tessera as the MCP firewall

When this project calls MCP tools that touch cloud resources (AWS, GCP, Azure,
Databricks, Snowflake, GitHub, Slack, Postgres, Kubernetes):

- Always prefer the `tessera` MCP server if the same tool is reachable through it.
- If a tool is only available via a direct cloud MCP server, stop and ask the
  user before proceeding — don't silently bypass the firewall.
- When a block response carries `error.data._meta.tessera_audit_event_id`,
  surface the policy reason to the user verbatim.
```

Equivalent goes in `.cursorrules` for Cursor, or the system prompt for Claude Desktop. This pattern is the difference between "a firewall the user must remember to use" and "a firewall the agent uses by default."

## Configuration at a glance

```yaml
listen:
  host: 127.0.0.1
  port: 8080

auth:
  type: bearer

policies:
  dir: /etc/tessera/policies
  reload: watch
  mode: log_only           # enforcement | log_only | observation
  default_action: block

upstreams:
  - name: aws
    kind: aws_mcp
    url: https://mcp.amazonaws.com
    aws_region: us-east-1
```

Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Annotated example: [tessera.example.yaml](tessera.example.yaml).

## Authoring policies

One YAML file per rule in `policies.dir`. Files prefixed with `_` are skipped. The engine evaluates in descending `priority`; first match wins.

```yaml
id: block-delete-prod
name: Block Delete in Production
description: Block delete calls targeting prod-suffixed resources.
match:
  upstream: "*"
when:
  - condition: action_class_in
    values: ["write.delete"]
  - condition: arg_matches_regex
    arg: resource_name
    pattern: ".*-prod$"
action: block
reason: "Delete blocked on production resource"
priority: 90
```

Validate before deploying:

```bash
tessera policy lint --policy-dir policies/
tessera policy test --policy-dir policies/ --fixture-dir tests/fixtures/
```

18 condition primitives shipped (`arg_equals`, `arg_matches_regex`, `arg_path_matches_regex`, `arg_in_set`, `predicted_cost`, `blast_radius`, `affected_resource_count`, `cumulative_spend_today`, `sts_chain_depth_greater_than`, `time_of_day_outside`, `any_of`, `none_of`, plus 6 more). Full catalog + fixture format: [docs/POLICIES.md](docs/POLICIES.md).

## What ships

- **24 bundled defensive policies** — 7 generic + 6 AWS-MCP defaults + 5 AWS-illustrative + 6 Batch 8 (intent / business-hours / oversized-payload / tool-allowlist / prompt-injection / non-prod-only).
- **Hash-chained audit log** — SQLite-backed; per-token scope isolation; `tessera audit verify` detects gap or tamper.
- **Three pluggable Protocols** — `Authenticator`, `PolicyLoader`, `AuditSink` resolved via importlib at startup. Same Protocols in Tessera Cloud (which swaps in Cognito + DynamoDB implementations).
- **Three enforcement modes** — `enforcement`, `log_only`, `observation`.
- **Multi-token bearer auth** + JWT mode (Entra / Okta / Cognito).
- **OAuth 2.1 PKCE + DCR + introspection** for management-plane SSO.
- **Multi-stage Docker image** — runs as UID 10001 (non-root).
- **Observability** — Prometheus metrics + optional OpenTelemetry tracing (off by default).
- **Optional extras** — `[aws]` (AWS-MCP routing), `[gemini]` (policy authoring), `[infracost]` (real-time cost), `[intelligence]` (premium-pack CDN client).

## Tessera Cloud

Hosted, multi-tenant, SSO, compliance evidence export, signed premium intelligence packs. Same engine, same Protocols — the implementations are swapped (e.g., `DynamoDBPolicyLoader` instead of `FilesystemPolicyLoader`). Your existing `tessera.yaml` and policy files work without changes when you migrate. https://cloudmorph.ai

## Manual smoke scenarios

Six human-readable customer journeys — fresh install, intelligence fetch + verify, policy-allow, cost-cap block, tier downgrade, anonymous CDN — under [tests/scenarios/](tests/scenarios/). Run them before tagging a release.

## Roadmap

Detail and rationale: [docs/ROADMAP.md](docs/ROADMAP.md).

- **stdio transport** — for Claude Desktop free-tier and agent runtimes that launch MCP servers as subprocesses.
- **Postgres audit sink** — for write volumes beyond SQLite's comfort zone; the `AuditSink` Protocol is already designed for it.
- **Native rate limiting** — per-token token bucket; workaround today is nginx/Caddy in front.
- **Rego escape hatch** — gated on a concrete use case the YAML condition catalog cannot express.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). `pip install -e ".[dev]"` and `pre-commit install` to get started.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Security

Report vulnerabilities privately via [SECURITY.md](SECURITY.md).
