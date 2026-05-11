# Tessera Integrations

For step-by-step click-through walkthroughs, see [`recipes/cursor-mcp-json.md`](../recipes/cursor-mcp-json.md), [`recipes/cursor-hooks.md`](../recipes/cursor-hooks.md), and [`recipes/claude-code.md`](../recipes/claude-code.md).

---

## Quick-reference table

| Agent client | Config file | How Tessera plugs in | Recipe |
|---|---|---|---|
| **Cursor** | `~/.cursor/mcp.json` or `.cursor/mcp.json` | Replace upstream URL with `http://localhost:8080/mcp/<name>`, add `headers.Authorization` | [`recipes/cursor-mcp-json.md`](../recipes/cursor-mcp-json.md) |
| **Claude Code** | `~/.claude.json` (global) or `.mcp.json` (project) | Same `mcpServers` structure as Cursor | [`recipes/claude-code.md`](../recipes/claude-code.md) |

Bearer token background: see [`docs/CONFIGURATION.md`](CONFIGURATION.md).

---

## Worked snippets

### Cursor — `~/.cursor/mcp.json`

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

Replace `tk_your_token_here` with your Tessera bearer token. Replace `aws` / `github` with upstream names from `tessera.yaml`. Restart Cursor or reload via the MCP panel after saving.

### Claude Code — `~/.claude.json` or `.mcp.json`

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

Project-scoped `.mcp.json` takes precedence over global `~/.claude.json` for servers with the same key. Claude Code reads `.mcp.json` fresh each session — no restart required.

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
