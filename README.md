# Tessera

<!-- mcp-name: io.github.CloudMorphAI/tessera -->

**Deterministic policy enforcement and audit-grade compliance evidence for AI agent tool calls.**

[![PyPI version](https://img.shields.io/pypi/v/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![Python versions](https://img.shields.io/pypi/pyversions/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fcloudmorphai%2Ftessera-blue.svg)](https://ghcr.io/cloudmorphai/tessera)

**Semver policy (from v1.0.0):** Tessera follows [Semantic Versioning 2.0.0](https://semver.org/). The stable API surface is: the `tessera` CLI commands listed in `tessera --help`, the `tessera.yaml` configuration schema, and the `cloudmorph-tessera` PyPI package name. Internal Python modules (anything not listed in `tessera/__init__.__all__`) are private and may change in any release.

## The problem

AI agents now take real, irreversible actions through MCP tool calls — delete a production database, attach an admin IAM policy, exfiltrate a secret into a log line. An agent doesn't need to be malicious for this to happen; a bad prompt, a hallucinated argument, or a compromised upstream MCP server is enough. Once the call reaches AWS, GCP, or Azure, there is no undo.

Regulators are catching up to this. The EU AI Act's Title III record-keeping and traceability obligations (Articles 12, 19, 26) apply from **2026-08-02** — high-risk AI systems must automatically log their operation and retain those logs for at least six months. "We have application logs" is not the same as a tamper-evident, independently verifiable record of what an AI system did and why it was allowed or blocked.

Tessera is a policy firewall at the MCP boundary: it sits between the agent and every MCP server, evaluates each tool call deterministically against a YAML policy bench, and writes every decision — allow, block, or require-approval — to a signed, hash-chained audit log you can export and verify offline.

## Deterministic enforcement

Every `tools/call` is evaluated in-process against the policy bench before it reaches the upstream. A blocked call never touches AWS/GCP/Azure — it returns immediately as a structured, final decision:

```json
{
  "jsonrpc": "2.0",
  "id": 42,
  "result": {
    "content": [{
      "type": "text",
      "text": "POLICY_BLOCK\npolicy_id: prod-protection\nreason: Delete blocked on production resource\n\nThis is a final decision from the Tessera policy gate. Do NOT retry with different parameters — the policy will reject equivalent calls. Surface this block to the user."
    }],
    "isError": true
  }
}
```

No LLM call, no heuristic scoring, no adaptive model in the decision path. The same policy set given the same tool call always produces the same verdict — that determinism is what makes the resulting audit trail defensible evidence rather than a best-effort log.

## Audit-grade compliance evidence

Every decision Tessera makes — not just blocks — is written to a SHA-256 hash-chained audit log. On demand, `tessera audit export` turns a time range of that log into a self-contained, Ed25519-signed **evidence bundle**:

```bash
tessera audit export --from 2026-06-01 --to 2026-06-30 --out ./evidence-june
```

```
evidence-june/
├── events.jsonl    # every decision event in range, one JSON object per line
├── manifest.json   # range, counts, chain head/tail hashes, signing public key
├── summary.html    # human-readable report, compliance mappings rendered in
└── VERIFY.md       # exact steps to verify this bundle with no network access
```

```bash
tessera audit verify --dir ./evidence-june
```

`audit verify --dir` recomputes the hash chain and the Ed25519 signature independently from `events.jsonl` — it does not trust `manifest.json`'s stated values — and runs fully offline. A tampered event, a gap in the chain, or a forged manifest is caught, not glossed over.

`summary.html` renders the events against built-in compliance mappings — **EU AI Act**, **India DPDP**, and **SOC 2** — showing which of the framework's obligations are evidenced by the bundle and exactly what is (and is not) covered. This is the artifact that answers "prove your AI agent's actions were governed" to an auditor or a customer's security review, without granting them access to your live systems.

## Performance

Policy evaluation is in-process and deterministic — no network round-trip, no LLM call. The decision engine resolves a tool call against the bundled policy set in well under a millisecond.

| Bench (`engine.evaluate()`) | p50 | p99 |
|---|---|---|
| Safe tool call — full ruleset walk, no match | 120.6 µs | 136.1 µs |
| Blocked — IAM PassRole | 63.0 µs | 76.0 µs |
| Blocked — admin-policy attach | 25.1 µs | 34.9 µs |

Measured on v0.7.2 over the 18-policy default set, GitHub Actions `ubuntu-latest`, Python 3.12.13 — CI-reproducible developer-hardware numbers, not inflated production claims. Full per-bench methodology: [benchmarks/results/v0.7.2.md](benchmarks/results/v0.7.2.md).

End-to-end HTTP throughput (sustained RPS, full-cycle latency) has **not** yet been formally benchmarked on production-spec hardware; earlier loopback figures were withdrawn pending a clean production run.

## Install + first block in 60 seconds

```bash
pip install cloudmorph-tessera
tessera init                                        # writes tessera.yaml + policies/ in cwd
TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" tessera serve
```

Tessera now listens on `http://127.0.0.1:8080/mcp`. Wire it into Cursor / Claude Code / your agent's MCP config with a single "tessera" entry (recipes in [recipes/](recipes/)), and every `tools/call` flows through 25 bundled defensive policies — `prod-protection`, `cost-cap`, `secret-leak-block`, `prompt-injection-heuristic`, `aws-mcp-passrole-guard`, plus 20 others. Tessera fans out tool discovery across all configured upstreams and namespaces tools as `<upstream>__<tool>` in the unified catalog.

---

## What this protects against

Concrete categories the 25 bundled policies cover out of the box:

- **Cost spikes** — `cost-cap`, `aws-bedrock-cost-ceiling-EXAMPLE`, `aws-cost-runaway-stop-EXAMPLE`, `aws-ec2-cost-cap-EXAMPLE`. Per-call ceiling, daily cumulative ceiling, model-specific Bedrock ceiling.
- **IAM blast-radius expansion** — `aws-mcp-passrole-guard`, `aws-mcp-admin-policy-deny`, `aws-mcp-create-access-key-deny`, `aws-iam-blast-radius-EXAMPLE`. PassRole approval gate, AWS-managed-admin attach hard-deny, access-key creation deny, principal-count guard.
- **Destructive operations on production** — `prod-protection`, `non-prod-only`, `write-action-approval`. Block by tag or name pattern, default-deny writes on prod, require human approval for delete-class actions.
- **Secret / credential / PII exfiltration** — `secret-leak-block`, `pii-block`, `aws-mcp-secrets-bulk-read-approval`, `aws-mcp-ec2-keymaterial-exfil-deny`. Regex bench for API keys + tokens + SSN + credit-card numbers in tool-call args, approval gate on bulk secrets/parameter reads, hard-deny on SSH-keypair and snapshot/AMI exfil primitives.
- **Prompt injection signals** — `prompt-injection-heuristic`. Regex bench for common jailbreak strings (`ignore previous`, `system: you are now`, etc.).
- **Region / data-residency violations** — `data-residency-eu`, `aws-region-allowlist-EXAMPLE`. Block ops outside permitted regions.
- **Public exposure / audit tampering** — `aws-mcp-rds-public-deny`, `aws-mcp-ec2-imdsv1-deny`, `aws-mcp-kms-deletion-approval`, `aws-mcp-s3-public-access-deny`, `aws-mcp-sg-open-ingress-deny`, `aws-mcp-lambda-public-invoke-deny`, `aws-mcp-cloudtrail-logging-disable-deny`. RDS public-access block, EC2 IMDSv1 deny, KMS deletion approval gate, S3/security-group/Lambda public-exposure hard-denies, CloudTrail disable hard-deny.

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

The engine is pure Python — no OPA, no LLM round-trip, no cloud credentials. Policy outcomes are deterministic. (The optional `tessera analyze` and `tessera policy author` commands call an LLM to *draft* policies offline — a design-time convenience, never in the enforcement path.)

## Limitations — what Tessera does *not* see

Tessera is a policy boundary at the MCP layer, not an OS- or network-level sandbox. Its edges, stated plainly:

- **It only sees calls routed through it.** An agent that reaches a cloud MCP server — or a raw SDK / HTTP endpoint — directly, bypassing the proxy, is invisible to Tessera. Making Tessera the *default* route is a prompt-level convention (see [Routing pattern](#routing-pattern--make-tessera-the-agents-default)), not a technical guarantee; a compromised or adversarial agent can call upstream directly.
- **Intent is self-declared.** The `_meta.tessera_intent` field is supplied by the caller and only shape-validated. Tessera does not independently verify that a declared intent matches the tool being called or its arguments.
- **Cost ceilings fail open on missing data.** When no price data is available for an operation, `predicted_cost` conditions evaluate to "allow" rather than block — Tessera will not block a call it cannot price. Pair cost policies with name/tag-based `block` rules for hard stops.

Tessera raises the cost of a mistake and leaves a tamper-evident record of every decision it *does* see. It is one enforcement layer, not a complete containment boundary.

## Tier levels

| Tier | Engine + 25 bundled policies | Premium packs |
|---|---|---|
| **Free** | Yes (local enforcement, hash-chain audit, multi-token scoping) | None |
| **Developer** | Yes | `aws-cost-aware-defaults`, `vendor-mcp-protection` |
| **Team** | Yes | + `hipaa-guardrails`, `pci-dss-controls` |
| **Enterprise** | Yes | All 15 production packs (tri-cloud AWS+Azure+GCP), custom-pack authoring |

Premium packs are fetched from the Tessera Cloud CDN, Ed25519-signature-verified, and cached locally. Free-tier installs continue to enforce 25 bundled policies with no network calls.

## Installation

```bash
# Local development / CLI use
pip install cloudmorph-tessera

# With optional extras
pip install "cloudmorph-tessera[aws,gemini,intelligence,infracost,observability]"

# Production deploy (recommended)
docker pull ghcr.io/cloudmorphai/tessera:1.0.0
```

After install: `tessera version` prints `tessera 1.0.0`. Full install matrix + supported Python versions: [docs/INSTALL.md](docs/INSTALL.md).

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

27 condition primitives shipped (`arg_equals`, `arg_matches_regex`, `arg_path_matches_regex`, `arg_in_set`, `predicted_cost`, `blast_radius`, `affected_resource_count`, `cumulative_spend_today`, `sts_chain_depth_greater_than`, `time_of_day_outside`, `any_of`, `none_of`, plus 15 more). Full catalog + fixture format: [docs/POLICIES.md](docs/POLICIES.md).

## What ships

- **25 bundled default policies** (enabled out of the box) — 13 generic (7 original + 6 Batch 8: intent / business-hours / oversized-payload / tool-allowlist / prompt-injection / non-prod-only) + 12 AWS-MCP security defaults. Plus **5 AWS-illustrative example policies** (opt-in templates, not enabled by default) demonstrating cost/blast-radius conditions.
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
