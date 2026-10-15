# Tessera

<!-- mcp-name: io.github.CloudMorphAI/tessera -->

**Deterministic policy enforcement and audit-grade compliance evidence for AI agent tool calls.**

[![PyPI version](https://img.shields.io/pypi/v/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![Python versions](https://img.shields.io/pypi/pyversions/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fcloudmorphai%2Ftessera-blue.svg)](https://ghcr.io/cloudmorphai/tessera)

**Semver policy (from v1.0.0):** Tessera follows [Semantic Versioning 2.0.0](https://semver.org/). The stable API surface is: the `tessera` CLI commands listed in `tessera --help`, the `tessera.yaml` configuration schema, and the `cloudmorph-tessera` PyPI package name. Internal Python modules (anything not listed in `tessera/__init__.__all__`) are private and may change in any release.

## Repository role and current maintenance

The local source targets **1.0.0**. This is a staged release, with acceptance
work still open; the versioned install examples below describe that target.
A local wheel build does not establish PyPI or container publication. Before
publication, review the exact source set, bundled policies, selected intelligence
schema and price inputs, and named-client compatibility evidence. The full
CloudMorph workspace records local evidence and open gates under
workspace-support/architecture/tessera-runtime/; those documents are workspace-only
and are not included in standalone clones.

This is the public customer-installed Python runtime, CLI, policy engine and MCP
proxy. Premium authored content belongs in the sibling `tessera-intelligence/`
repository. The application monorepo's `tessera-cloud-wrapper/` is an optional
hosted adapter, currently dormant; it is not required for the synthetic pilot.

For current bounded MCP behavior use [the transport contract](docs/STREAMABLE_HTTP.md)
and [synthetic pilot instructions](examples/synthetic_pilot/README.md). Offline
acceptance does not establish every desktop client's compatibility. Premium packs
need runtime-schema, native-tool matching and input verification before use;
missing price data and unsupported predicates remain explicit content gates.
Amplify deployment does not release this Python package or signed intelligence.

## The problem

AI agents now take real, irreversible actions through MCP tool calls — delete a production database, attach an admin IAM policy, exfiltrate a secret into a log line. An agent doesn't need to be malicious for this to happen; a bad prompt, a hallucinated argument, or a compromised upstream MCP server is enough. Once the call reaches AWS, GCP, or Azure, there is no undo.

Teams reviewing agent controls need to trace which routed calls were allowed or blocked and why. Tessera records policy decisions and supports integrity checks on exported records. That evidence is one input to a security or compliance review; it does not establish legal compliance.

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

## Auditable decision exports

Tessera emits allow and block decisions to its configured audit sink, with SHA-256 hash chaining for persisted events. Delivery and persistence depend on sink configuration and health; a returned decision is not itself proof of durable storage. On demand, `tessera audit export` turns a selected time range of persisted records into a self-contained, Ed25519-signed **evidence bundle**:

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

`summary.html` renders the events against built-in mappings for **EU AI Act**, **India DPDP**, and **SOC 2**. These mappings are informational evidence links; an export does not certify compliance, establish legal compliance, or prove that calls outside Tessera were governed. Local or synthetic output is not production or customer acceptance evidence.

## Performance

Policy evaluation is in-process and deterministic — no network round-trip, no LLM call. The decision engine resolves a tool call against the bundled policy set in well under a millisecond.

| Bench (`engine.evaluate()`) | p50 | p99 |
|---|---|---|
| Safe tool call — full ruleset walk, no match | 120.6 µs | 136.1 µs |
| Blocked — IAM PassRole | 63.0 µs | 76.0 µs |
| Blocked — admin-policy attach | 25.1 µs | 34.9 µs |

Measured on v0.7.2 over the 18-policy default set, GitHub Actions `ubuntu-latest`, Python 3.12.13 — CI-reproducible developer-hardware numbers, not inflated production claims. Full per-bench methodology: [benchmarks/results/v0.7.2.md](benchmarks/results/v0.7.2.md).

End-to-end HTTP throughput (sustained RPS, full-cycle latency) has **not** yet been formally benchmarked on production-spec hardware; earlier loopback figures were withdrawn pending a clean production run.

## Local source quickstart

```bash
python -m pip install -e .
tessera init                                        # writes tessera.yaml + policies/ in cwd
```

The checkout's package metadata declares version `1.0.0` and Python `>=3.12`; neither value confirms a published PyPI package or container image. The local `init` scaffold creates a `log_only` configuration and two reference guards. Configure and review an upstream and its policies before serving. For a Linux/WSL offline synthetic run, follow [the local quickstart](QUICKSTART.md); that run starts no listener and does not verify a live desktop client or deployment.

---

## What this protects against

The repository includes policy templates for the categories below. They are not all active by default: `tessera init` writes only `secret-leak-block` and `prompt-injection-heuristic`; review and explicitly configure any other policy before relying on it.

- **Cost spikes** — `cost-cap`, `aws-bedrock-cost-ceiling-EXAMPLE`, `aws-cost-runaway-stop-EXAMPLE`, `aws-ec2-cost-cap-EXAMPLE`. Per-call ceiling, daily cumulative ceiling, model-specific Bedrock ceiling.
- **IAM blast-radius expansion** — `aws-mcp-passrole-guard`, `aws-mcp-admin-policy-deny`, `aws-mcp-create-access-key-deny`, `aws-iam-blast-radius-EXAMPLE`. PassRole approval gate, AWS-managed-admin attach hard-deny, access-key creation deny, principal-count guard.
- **Destructive operations on production** — `prod-protection`, `non-prod-only`, `write-action-approval`. Block by tag or name pattern, default-deny writes on prod, require human approval for delete-class actions.
- **Secret / credential / PII exfiltration** — `secret-leak-block`, `pii-block`, `aws-mcp-secrets-bulk-read-approval`, `aws-mcp-ec2-keymaterial-exfil-deny`. Regex bench for API keys + tokens + SSN + credit-card numbers in tool-call args, approval gate on bulk secrets/parameter reads, hard-deny on SSH-keypair and snapshot/AMI exfil primitives.
- **Prompt injection signals** — `prompt-injection-heuristic`. Regex bench for common jailbreak strings (`ignore previous`, `system: you are now`, etc.).
- **Region / data-residency violations** — `data-residency-eu`, `aws-region-allowlist-EXAMPLE`. Block ops outside permitted regions.
- **Public exposure / audit tampering** — `aws-mcp-rds-public-deny`, `aws-mcp-ec2-imdsv1-deny`, `aws-mcp-kms-deletion-approval`, `aws-mcp-s3-public-access-deny`, `aws-mcp-sg-open-ingress-deny`, `aws-mcp-lambda-public-invoke-deny`, `aws-mcp-cloudtrail-logging-disable-deny`. RDS public-access block, EC2 IMDSv1 deny, KMS deletion approval gate, S3/security-group/Lambda public-exposure hard-denies, CloudTrail disable hard-deny.

The source includes an optional client for verifying and caching signed policy artifacts; this checkout does not establish current CDN or pack availability. To configure a compatible published pack, set `intelligence.enabled: true` and list its exact `pack-name@version` in `intelligence.selected_packs` in `tessera.yaml`; the default list is empty. Each selection requires a signed production manifest, a verified archive, runtime-compatible policies and an eligible current license. Archived incompatible packs and unsigned drafts cannot be activated.

Selections are required protection: an unavailable selection prevents startup; failed refresh or expired entitlement blocks governed calls, including in `log_only` and observation modes. Local policy IDs take precedence over selected-pack collisions. The CLI has no `tessera intelligence pull` command. Local policy files remain supported and are validated with `tessera policy lint`.

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

## Local policies and optional content

Policies are loaded from the configured local policy directory. `tessera init`
writes two starter policies; other source-tree policy files are not activated
automatically. The optional signed-content client requires a compatible verified
catalog, archive and eligible license. This repository does not establish which
commercial packs or tier entitlements are currently published and available.

## Installation

```bash
# Editable install from this checkout (source version 1.0.0)
python -m pip install -e .

# With optional extras
python -m pip install -e ".[aws,gemini,intelligence,infracost,observability]"
```

The distribution and OCI image names are shown for reference; publication of the `1.0.0` PyPI package and container tag has not been verified. `tessera version` reports installed package metadata, which is not proof of a public release. See [the staged install notes](docs/INSTALL.md) for the limited local environment evidence.

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

- **Policy templates** - `tessera init` writes two starter policies. Other templates in `tessera/policies_default/`, including AWS-illustrative examples, are not enabled by that scaffold; review and configure them explicitly.
- **Hash-chained audit log** — SQLite-backed; per-token scope isolation; `tessera audit verify` detects gap or tamper.
- **Three pluggable Protocols** — `Authenticator`, `PolicyLoader`, and `AuditSink` resolved via importlib at startup.
- **Three enforcement modes** — `enforcement`, `log_only`, `observation`.
- **Multi-token bearer auth** + JWT mode (Entra / Okta / Cognito).
- **OAuth 2.1 PKCE + DCR + introspection** for management-plane SSO.
- **Multi-stage Docker image** — runs as UID 10001 (non-root).
- **Observability** — Prometheus metrics + optional OpenTelemetry tracing (off by default).
- **Optional extras** — `[aws]` (AWS-MCP routing), `[gemini]` (policy authoring), `[infracost]` (real-time cost), `[intelligence]` (premium-pack CDN client).

## Tessera Cloud

Tessera Cloud is a separate hosted product surface. The optional hosted runtime wrapper in the application monorepo is dormant; local runtime and synthetic evidence in this repository do not establish hosted feature parity, service availability, or deployment behavior. See the hosted product documentation for its current status.

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
