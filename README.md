# Tessera

**The open-source MCP firewall for AI agents**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue.svg)](pyproject.toml)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fcloudmorph--ai%2Ftessera-blue.svg)](https://ghcr.io/cloudmorph-ai/tessera)

---

## Why Tessera

AI agents calling MCP tools can delete production data, exfiltrate secrets, and exceed cost caps — all in a single tool call your code never explicitly authorized. Tessera is an HTTP proxy that sits between the agent and every MCP server; every `tools/call` request is evaluated against a YAML policy set before it reaches the upstream. Decisions are written to a hash-chain audit log so tampering is detectable. The engine is pure Python — no OPA, no ML, no cloud credentials — so the policy outcome for a given input is always the same. The 7 reference policies ship with the container and are the same ones sold separately by other vendors.

---

## 5-minute quickstart

```bash
# Step 1: Pull
docker pull ghcr.io/cloudmorph-ai/tessera:0.1.0

# Step 2: Scaffold
docker run --rm -v "$PWD:/out" ghcr.io/cloudmorph-ai/tessera:0.1.0 tessera init --dir /out
# Creates tessera.yaml (mode: log_only), policies/, .env.example

# Step 3: Edit tessera.yaml — change upstreams[].url to your real MCP server URL

# Step 4: Start Tessera (log_only by default — safe to try, nothing is blocked yet)
docker run -d --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" \
  ghcr.io/cloudmorph-ai/tessera:0.1.0

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
- **7 reference policies** — `read-only-mode`, `prod-protection`, `secret-leak-block`, `pii-block`, `cost-cap`, `write-action-approval`, `data-residency-eu`. See [docs/POLICIES.md](docs/POLICIES.md).
- **Per-file reload error isolation** — a bad YAML file is skipped and logged; the rest of the policy set remains live. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Regex safety (ReDoS defense)** — all regex conditions are evaluated via the `regex` library with a 100 ms timeout; a timeout returns `false` and tags the audit event. See [docs/POLICIES.md](docs/POLICIES.md).
- **Intent-blind agent support** — agents that do not declare intent in `_meta.tessera_intent` are handled by tool-name and argument policies. `intent.required: false` is the default. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
- **CLI** — `tessera serve`, `tessera audit verify`, `tessera policy test`, `tessera policy lint`, `tessera version`, `tessera init`. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Multi-stage Docker image** — builder + slim runtime; runs as UID 10001 (non-root). See [docs/INSTALL.md](docs/INSTALL.md).
- **Three pluggable Protocols** — `Authenticator`, `PolicyLoader`, `AuditSink` are resolved via importlib at startup; swap implementations without modifying core code. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## How it works

```
Agent --> [Tessera: auth --> engine --> audit] --> Upstream MCP
```

Every inbound `POST /mcp/{upstream}` is:

1. Authenticated — bearer token matched against configured tokens; `AuthContext.scope` assigned.
2. Evaluated — policy engine walks the sorted policy set (first-match-wins) and returns `allow`, `block`, `log_only`, or `require_approval`.
3. Audited — the decision (and response, if forwarded) is written to the hash-chain.

In `enforcement` mode a `block` decision returns HTTP 403 to the agent and does not touch the upstream. In `log_only` mode the upstream is always called and the decision is returned in response headers (`X-Tessera-Decision`, `X-Tessera-Policy-Id`, `X-Tessera-Reason`).

Full component breakdown: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

## Handbook

Read [the Tessera handbook](handbook/README.md) — how we build, our roadmap, team, funding, and security commitment.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `pip install -e ".[dev]"` and `pre-commit install` to get started.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Security

Report vulnerabilities privately via [SECURITY.md](SECURITY.md).
