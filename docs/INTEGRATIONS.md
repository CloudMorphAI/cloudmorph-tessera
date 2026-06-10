# Tessera Integrations

For step-by-step click-through walkthroughs, see [`recipes/cursor-mcp-json.md`](../recipes/cursor-mcp-json.md), [`recipes/cursor-hooks.md`](../recipes/cursor-hooks.md), and [`recipes/claude-code.md`](../recipes/claude-code.md).

---

## Quick-reference table

| Agent client | Config file | How Tessera plugs in (v0.8 unified) | Recipe |
|---|---|---|---|
| **Cursor** | `~/.cursor/mcp.json` or `.cursor/mcp.json` | One `"tessera"` entry at `http://localhost:8080/mcp` | [`recipes/cursor-mcp-json.md`](../recipes/cursor-mcp-json.md) |
| **Claude Code** | `~/.claude.json` (global) or `.mcp.json` (project) | Same unified `"tessera"` entry | [`recipes/claude-code.md`](../recipes/claude-code.md) |
| **Claude Desktop** | `claude_desktop_config.json` | Same unified `"tessera"` entry | — |

**v0.8 one-liner install:** `tessera install-claude-code` (or `install-cursor`, `install-claude-desktop`) writes the unified entry automatically. Use `--upgrade` to migrate existing per-upstream v0.7.x entries. Use `--legacy-per-upstream` to keep the old per-upstream behavior.

**v0.7.x installs (per-upstream routes):** `POST /mcp/<upstream_name>` routes are kept alive. Existing entries continue to work; re-run `tessera install-claude-code --upgrade` to migrate to unified.

Bearer token background: see [`docs/CONFIGURATION.md`](CONFIGURATION.md).

---

## Worked snippets

### v0.8 unified entry (recommended)

One entry in the IDE config, regardless of how many upstreams Tessera proxies. Tessera fans out `tools/list` internally and namespaces tools as `<upstream>__<tool>` (e.g. `aws__s3_PutObject`, `github__create_pull_request`).

**Cursor — `~/.cursor/mcp.json`**

```json
{
  "mcpServers": {
    "tessera": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Claude Code — `~/.claude.json` or `.mcp.json`**

```json
{
  "mcpServers": {
    "tessera": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

Replace `tk_your_token_here` with your Tessera bearer token. Claude Code reads `.mcp.json` fresh each session — no restart required.

**CLI install (writes the entry automatically):**

```bash
tessera install-claude-code --token tk_your_token_here
tessera install-cursor --token tk_your_token_here
tessera install-claude-desktop --token tk_your_token_here
# Migrate from v0.7.x per-upstream entries:
tessera install-claude-code --token tk_your_token_here --upgrade
```

---

### v0.7.x per-upstream entries (legacy — still supported)

The `POST /mcp/<upstream_name>` routes remain active. Existing entries keep working without change.

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    },
    "github-via-tessera": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

Project-scoped `.mcp.json` takes precedence over global `~/.claude.json` for servers with the same key.

---

## Intent-aware vs intent-blind agents

Tessera policies can optionally use **intent** — a structured declaration the agent sends inside `_meta.tessera_intent` explaining what it intends to do and why. Example:

```json
{
  "_meta": {
    "tessera_intent": {
      "verbs": ["read.list"],
      "purpose": "Listing S3 buckets to build a cost-attribution report."
    }
  }
}
```

This lets policies reason about *why* a tool is called, not just *what* tool was called — for example, blocking a `write.delete` call that declares a `read.list` intent.

Standard clients (Cursor, Claude Code in current versions) are **intent-blind**: they do not populate `_meta.tessera_intent`. Tessera handles this gracefully — policies with `match.require_intent: true` are skipped for intent-blind requests; all other policies evaluate normally on tool name and arguments. No special configuration is needed.

---

## Per-server config templates

For each server: add the `upstreams[]` block to `tessera.yaml`, add the `mcpServers` entry to your client config, and set the referenced env var before starting Tessera.

### AWS MCP

```yaml
# tessera.yaml
upstreams:
  - name: aws
    url: https://mcp.aws.example.com
    timeout_seconds: 30
    credentials:
      header: Authorization
      value: "Bearer ${AWS_MCP_TOKEN}"
```

Example tool call routed through Tessera:

```
POST http://localhost:8080/mcp/aws
→ tools/call { name: "s3_list_buckets", arguments: {} }
```

Reference policies: `cost-cap.yaml`, `data-residency-eu.yaml`, `prod-protection.yaml`, `pii-block.yaml`. See [`docs/POLICIES.md`](POLICIES.md).

---

### GitHub MCP

```yaml
# tessera.yaml
upstreams:
  - name: github
    url: https://mcp.github.example.com
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${GITHUB_MCP_TOKEN}"
```

Set `GITHUB_MCP_TOKEN` to a GitHub personal access token (classic or fine-grained) with the scopes your agent needs.

Reference policies: `write-action-approval.yaml`, `read-only-mode.yaml`, `prod-protection.yaml`. See [`docs/POLICIES.md`](POLICIES.md).

---

### Slack MCP

```yaml
# tessera.yaml
upstreams:
  - name: slack
    url: https://mcp.slack.example.com
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${SLACK_MCP_TOKEN}"
```

Set `SLACK_MCP_TOKEN` to your Slack bot token (`xoxb-...`).

Reference policies: `pii-block.yaml`, `secret-leak-block.yaml`. See [`docs/POLICIES.md`](POLICIES.md).

---

### Linear MCP

```yaml
# tessera.yaml
upstreams:
  - name: linear
    url: https://mcp.linear.example.com
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${LINEAR_MCP_TOKEN}"
```

Set `LINEAR_MCP_TOKEN` to your Linear API key.

Reference policies: `write-action-approval.yaml`. See [`docs/POLICIES.md`](POLICIES.md).

---

## AWS MCP Server

Tessera supports AWS-hosted MCP servers secured with IAM SigV4 signing via the
`kind: aws_mcp` upstream type. Requests are signed automatically using the
`mcp-proxy-for-aws` library; credentials come from the standard boto3 credential
chain (no Tessera config needed for credentials).

### Prerequisites

Install the `aws` extra:

```bash
pip install "cloudmorph-tessera[aws]"
```

This pulls in `mcp-proxy-for-aws==0.2.0` and `boto3>=1.34.0`.

### tessera.yaml configuration

```yaml
upstreams:
  - name: aws-bedrock
    kind: aws_mcp          # required — activates IAM-signed client
    url: https://mcp.bedrock.us-east-1.amazonaws.com
    aws_region: us-east-1  # required for aws_mcp kind
    aws_service: aws-mcp   # default; override if the service name differs
    # aws_endpoint_override: http://localhost:4566  # optional — LocalStack / custom
    timeout_seconds: 30
    # No `credentials` block needed — boto3 chain resolves credentials
```

AWS credentials are resolved in boto3's standard priority order:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
2. AWS config file (`~/.aws/credentials`)
3. IAM role attached to the EC2 instance / ECS task / Lambda function
4. AWS SSO / IAM Identity Center

Tessera never reads or stores AWS credentials — they stay inside the boto3 client.

### Agent client config (unchanged)

The agent client configuration is identical to the bearer upstream case. Tessera's
`/mcp/{name}` route is the proxy; the client does not need to know that the upstream
is AWS-signed.

```json
{
  "mcpServers": {
    "aws-bedrock": {
      "url": "http://localhost:8080/mcp/aws-bedrock",
      "headers": {
        "Authorization": "Bearer tk_your_tessera_token_here"
      }
    }
  }
}
```

### Audit enrichment

When an AWS MCP response includes the `aws:ViaAWSMCPService` or `aws:CalledViaAWSMCP`
headers, Tessera captures them in the audit event payload under `_aws_context`:

```json
{
  "eventType": "passthrough",
  "payload": {
    "method": "tools/list",
    "_aws_context": {
      "via_aws_mcp_service": "bedrock-agent-runtime",
      "called_via_aws_mcp": "true"
    }
  }
}
```

This gives compliance teams a clear signal that traffic transited the AWS MCP
service tier.

### AWS Activate

If you are a startup, check eligibility for [AWS Activate](https://aws.amazon.com/activate/)
credits ($5,000–$100,000 in AWS credits) — these can cover the cost of AWS MCP
service API calls during your evaluation period.

---

## FastMCP Streamable-HTTP MCP Servers

Tessera v0.5.1 adds `kind: mcp_streamable_http` for MCP servers that implement the
MCP 2025-06-18 streamable-HTTP transport — including `awslabs.aws-api-mcp-server` and
any server built with the FastMCP framework.

### When to use which upstream kind

| Kind | Use case |
|---|---|
| `bearer` (default) | Plain HTTP JSON-RPC servers that accept a POST and return JSON directly (no session handshake, no SSE). |
| `mcp_streamable_http` | FastMCP servers: `awslabs.aws-api-mcp-server`, Anthropic example MCP servers, community FastMCP servers. Handles `Mcp-Session-Id` handshake and SSE response streams. |
| `aws_mcp` | AWS-hosted MCP service endpoints behind IAM SigV4 signing (e.g. `mcp.bedrock.us-east-1.amazonaws.com`). Requires the `[aws]` extra. |

### tessera.yaml configuration

```yaml
upstreams:
  - name: awslabs_local
    kind: mcp_streamable_http
    url: http://127.0.0.1:8000
    # optional — omit when AUTH_TYPE=no-auth
    # auth_header: "Bearer <token>"
    session_timeout_s: 300   # optional, default 300
    request_timeout_s: 10    # optional, default 10
```

The `auth_header` field carries the full Authorization header value (e.g.
`"Bearer mytoken"`). Omit it entirely for unauthenticated local servers
(`AUTH_TYPE=no-auth`).

### Starting awslabs.aws-api-mcp-server in streamable-http mode

```bash
pip install awslabs.aws-api-mcp-server
AWS_API_MCP_TRANSPORT=streamable-http AUTH_TYPE=no-auth AWS_REGION=us-east-1 \
  awslabs.aws-api-mcp-server
# Binds on 127.0.0.1:8000 by default
```

Then point Tessera at it:

```yaml
upstreams:
  - name: aws
    kind: mcp_streamable_http
    url: http://127.0.0.1:8000
```

### Known limits

- **One session per upstream URL** — Tessera maintains one `Mcp-Session-Id` per
  upstream name. Concurrent tenants share the session. This is safe for stateless
  MCP servers (like `aws-api-mcp-server`) but may not be correct for session-stateful
  servers in future MCP revisions.
- **No session resumption across restarts** — The session cache is in-process memory.
  A Tessera restart triggers a fresh `initialize` handshake on the next call.
- **SSE notifications discarded** — SSE events without an `id` matching the request
  (i.e. MCP notifications) are logged at DEBUG level and dropped. This is correct for
  `tools/call` traffic; future MCP notification subscription support is v0.6+ work.

---

## Verifying the connection

```bash
# 1. Health check
curl -s http://localhost:8080/healthz | python -m json.tool
# Expect: { "status": "ok", "policy_state": { "loaded": N, "errored": [] } }

# 2. Audit log (confirm a call was logged)
tessera audit verify --scope default
# Docker: docker exec tessera tessera audit verify --scope default
```

After wiring a client, make any tool call from it and check the audit log. In `log_only` mode (the default after `tessera init`), responses carry `X-Tessera-Mode: log_only` and `X-Tessera-Decision: would_allow|would_block` — traffic is never blocked in this mode.

For detailed troubleshooting (401, 404, policies not firing), see [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
