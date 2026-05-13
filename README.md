# Tessera

<!-- mcp-name: io.github.CloudMorphAI/tessera -->

**The open-source MCP firewall for AI agents**

**See Tessera block a destructive Cursor action in 60 seconds → [cursor-hooks recipe](recipes/cursor-hooks.md)**

[![PyPI version](https://img.shields.io/pypi/v/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![Python versions](https://img.shields.io/pypi/pyversions/cloudmorph-tessera.svg)](https://pypi.org/project/cloudmorph-tessera/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/CloudMorphAI/cloudmorph-tessera/blob/main/LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fcloudmorphai%2Ftessera-blue.svg)](https://ghcr.io/cloudmorphai/tessera)
[![Docker pulls](https://img.shields.io/docker/pulls/cloudmorphai/tessera.svg)](https://github.com/CloudMorphAI/cloudmorph-tessera/pkgs/container/tessera)

---

## Why Tessera

AI agents calling MCP tools can delete production data, exfiltrate secrets, and exceed cost caps — all in a single tool call your code never explicitly authorized. Tessera is an HTTP proxy that sits between the agent and every MCP server; every `tools/call` request is evaluated against a YAML policy set before it reaches the upstream. Decisions are written to a hash-chain audit log so tampering is detectable. The engine is pure Python — no OPA, no ML, no cloud credentials — so the policy outcome for a given input is always the same. The 14 policies (7 core reference + 7 integration-specific) ship with the container; core policies are the same ones sold separately by other vendors.

---

## Installation

### Option 1: Docker (recommended for production)

```bash
docker pull ghcr.io/cloudmorphai/tessera:0.1.1
```

### Option 2: Python package (for local development and CLI use)

```bash
pip install cloudmorph-tessera
```

After install, verify:

```bash
tessera version
# tessera 0.1.1
```

Docker is the primary path for users running Tessera as a service. PyPI is the path for users who want to author policies locally or run `tessera policy lint` / `tessera policy test` in CI.

---

## 5-minute quickstart

```bash
# Step 1: Pull
docker pull ghcr.io/cloudmorphai/tessera:0.1.1

# Step 2: Scaffold
docker run --rm -v "$PWD:/out" ghcr.io/cloudmorphai/tessera:0.1.1 tessera init --dir /out
# Creates tessera.yaml (mode: log_only), policies/, .env.example

# Step 3: Edit tessera.yaml — change upstreams[].url to your real MCP server URL

# Step 4: Start Tessera (log_only by default — safe to try, nothing is blocked yet)
docker run -d --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" \
  ghcr.io/cloudmorphai/tessera:0.1.1

# Step 5: Wire your agent — add to ~/.cursor/mcp.json:
# {
#   "mcpServers": {
#     "aws-via-tessera": {
#       "url": "http://localhost:8080/mcp/aws",
#       "headers": {"Authorization": "Bearer <your-token>"}
#     }
#   }
# }
# See docs/INTEGRATIONS.md for Claude Code, Claude Desktop, Windsurf.

# Step 6: Verify a tool call was logged
docker exec tessera tessera audit verify --scope default

# Step 7: When ready, switch to enforcement
# Edit tessera.yaml: change mode: log_only -> mode: enforcement
# Restart Tessera. Now block decisions fire.

# IMPORTANT: If exposing Tessera beyond localhost, put it behind nginx/Caddy
# with a rate-limit rule. Native rate limiting is on the v0.2 roadmap.
```

---

## What ships

- **Multi-token bearer auth** — inline env var, YAML file, or single legacy token; per-token scope isolates audit streams. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Three enforcement modes** — `enforcement` (blocks fire), `log_only` (advisory, always forwards), `observation` (engine skipped). See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **16-condition pure-Python policy engine** — `arg_equals`, `arg_greater_than`, `arg_less_than`, `arg_matches_regex`, `arg_in_set`, `arg_contains_pattern`, `arg_size_greater_than`, `tool_name_in`, `action_class_in`, `intent_class_in`, `intent_purpose_matches`, `region_in`, `time_of_day_outside`, `meta_field_equals`, `any_of`, `none_of`. See [docs/POLICIES.md](docs/POLICIES.md).
- **Hash-chain audit log** — every event is chained to the previous via SHA-256; `tessera audit verify` detects any gap or tamper. Per-token scope isolation. See [docs/AUDIT.md](docs/AUDIT.md).
- **14 policies** — 7 core reference policies (`read-only-mode`, `prod-protection`, `secret-leak-block`, `pii-block`, `cost-cap`, `write-action-approval`, `data-residency-eu`) and 7 integration-specific protection policies (`github-mcp-protection`, `jira-mcp-protection`, `owasp-mcp-prompt-injection`, `owasp-mcp-tool-poisoning`, `postgres-mcp-protection`, `salesforce-mcp-protection`, `slack-mcp-protection`). See [docs/POLICIES.md](docs/POLICIES.md).
- **Per-file reload error isolation** — a bad YAML file is skipped and logged; the rest of the policy set remains live. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Regex safety (ReDoS defense)** — all regex conditions are evaluated via the `regex` library with a 100 ms timeout; a timeout returns `false` and tags the audit event. See [docs/POLICIES.md](docs/POLICIES.md).
- **Intent-blind agent support** — agents that do not declare intent in `_meta.tessera_intent` are handled by tool-name and argument policies. `intent.required: false` is the default. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
- **CLI** — `tessera serve`, `tessera audit verify`, `tessera policy test`, `tessera policy lint`, `tessera version`, `tessera init`. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Multi-stage Docker image** — builder + slim runtime; runs as UID 10001 (non-root). See [docs/INSTALL.md](docs/INSTALL.md).
- **Three pluggable Protocols** — `Authenticator`, `PolicyLoader`, `AuditSink` are resolved via importlib at startup; swap implementations without modifying core code. See [tessera/pluggable.py](tessera/pluggable.py).

---

## How it works

```
Agent --> [Tessera: auth --> engine --> audit] --> Upstream MCP
```

Every inbound `POST /mcp/{upstream}` is:

1. Authenticated — bearer token matched against configured tokens; `AuthContext.scope` assigned.
2. Evaluated — policy engine walks the sorted policy set (first-match-wins) and returns `allow`, `block`, `log_only`, or `require_approval`.
3. Audited — the decision (and response, if forwarded) is written to the hash-chain.

In `enforcement` mode a `block` decision returns a JSON-RPC error (code `-32603`) over HTTP 200 to the agent and does not touch the upstream. In `log_only` mode the upstream is always called and the decision is returned in response headers (`X-Tessera-Decision`, `X-Tessera-Policy-Id`, `X-Tessera-Reason`).

Source code is under [tessera/](tessera/); contributor notes in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Configuration at a glance

```yaml
listen:
  host: 0.0.0.0
  port: 8080

auth:
  type: bearer

policies:
  dir: /etc/tessera/policies
  reload: watch          # watch | sighup | none
  mode: log_only         # enforcement | log_only | observation
  default_action: block

upstreams:
  - name: aws
    url: https://mcp.aws.example.com
    credentials:
      header: Authorization
      value: "Bearer ${AWS_MCP_TOKEN}"   # resolved from environment at startup
```

Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Annotated example: [tessera.example.yaml](tessera.example.yaml).

---

## Authoring policies

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

Policies are one YAML file per rule in the directory set by `policies.dir`. Files prefixed with `_` are skipped. The engine evaluates policies in descending `priority` order; the first matching policy wins. `when: []` (empty) matches every call.

Test before deploying:

```bash
tessera policy lint --policy-dir policies/
tessera policy test --policy-dir policies/ --fixture-dir tests/fixtures/
```

Full condition catalog and fixture format: [docs/POLICIES.md](docs/POLICIES.md).

---

## Routing your AI agent through Tessera (CLAUDE.md / system-prompt pattern)

Adding Tessera as one of many available MCP servers isn't enough — your agent will often prefer the direct cloud MCP server because it sees that one too. To make Tessera the default route for cloud tool calls, instruct the agent in its system context.

For **Claude Code**, drop a `CLAUDE.md` at your project root (or `~/.claude/CLAUDE.md` for global default):

```markdown
# Tool routing — use Tessera as the MCP firewall

When this project calls MCP tools that touch cloud resources (AWS, GCP, Azure,
Databricks, Snowflake, GitHub, Slack, Postgres, Kubernetes):

- **Always prefer the `tessera` MCP server** if the same tool is reachable
  through it. Tessera enforces deterministic policy + writes a hash-chained
  audit log of every call.
- If a tool is only available via a direct cloud MCP server, **stop and ask
  the user before proceeding** — don't silently bypass the firewall.
- Read-only operations (list, describe, get) typically pass through normally.
- Destructive operations (delete, terminate, drop, force-push) WILL be blocked
  by the shipped reference policies if they touch prod resources. When the
  block response carries `error.data._meta.tessera_audit_event_id`, surface
  the policy reason to the user verbatim.
```

For **Cursor**, equivalent goes in `.cursorrules` at project root, or in user-level Cursor settings. For **Claude Desktop**, put it in the global system prompt via Settings → "Personalization".

This pattern is the difference between "a firewall the user must remember to use" and "a firewall the agent uses by default." Combined with the 14 reference policies, it gives you defense-in-depth without per-call vigilance.

See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for per-client config recipes (Cursor, Claude Code, Claude Desktop).

---

## Tessera Cloud

Want hosted? Multi-tenant? SSO? Compliance evidence export? Tessera Cloud is the same engine with hosted orchestration. The same `Authenticator`, `PolicyLoader`, and `AuditSink` Protocols are used — the implementations are swapped (e.g., `DynamoDBPolicyLoader` instead of `FilesystemPolicyLoader`). Your existing `tessera.yaml` and policy files work without changes when you migrate. https://cloudmorph.ai

---

## Roadmap

Deferred from v0.1; detail and rationale in [docs/ROADMAP.md](docs/ROADMAP.md).

- **OAuth 2.1 PKCE** — v0.2; needed for SaaS/CI deployments where the identity issuer is a third-party IdP.
- **Native rate limiting** — v0.2; per-token token bucket; workaround in v0.1 is nginx/Caddy in front.
- **Postgres audit sink** — v0.2; the `AuditSink` Protocol is already designed for it; SQLite covers v0.1 write volume.
- **stdio transport** — v0.2; for Claude Desktop and agent runtimes that launch MCP servers as subprocesses.
- **Rego escape hatch** — v0.2; gated on a concrete use case the YAML condition catalog cannot express.
- **Multi-tenant isolation** — not planned for OSS; available in Tessera Cloud.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `pip install -e ".[dev]"` and `pre-commit install` to get started.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Security

Report vulnerabilities privately via [SECURITY.md](SECURITY.md).
