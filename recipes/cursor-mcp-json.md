# Recipe: Cursor to Tessera via mcp.json (intent-blind)

**Time:** 5 minutes
**Prerequisite:** Tessera running at `http://localhost:8080`

> **60-second demo:** [screencast placeholder — coming soon]

## Overview

This recipe replaces Cursor's direct MCP server URL with Tessera's proxy URL.
No Cursor Hooks required. Tessera evaluates policies in **intent-blind** mode —
enforcement is based on tool name and argument patterns only, with no access to
the agent's intent string.

## Step 1 — Start Tessera

```bash
export TESSERA_BEARER_TOKEN="tk_cursor_$(openssl rand -hex 16)"

tessera start --config tessera.yaml
```

Create a minimal `tessera.yaml` pointing at your existing MCP server:

```yaml
deployment_id: cursor-dev

auth:
  type: bearer

policies:
  dir: ./policies
  mode: enforcement
  default_action: allow

upstreams:
  - name: github
    url: http://localhost:5000   # your existing GitHub MCP server
    timeout_seconds: 10
```

## Step 2 — Update Cursor's mcp.json

Locate your Cursor MCP config:

- **macOS / Linux:** `~/.cursor/mcp.json`
- **Windows:** `%APPDATA%\Cursor\mcp.json`

**Before** — Cursor points directly at the upstream:

```json
{
  "mcpServers": {
    "github": {
      "url": "http://localhost:5000"
    }
  }
}
```

**After** — Cursor points at Tessera's proxy URL for that upstream:

```json
{
  "mcpServers": {
    "github": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_cursor_<your-token>"
      }
    }
  }
}
```

The path `/mcp/github` matches the `name: github` upstream in `tessera.yaml`.
Replace `<your-token>` with the value you set in `TESSERA_BEARER_TOKEN`.

## Step 3 — Add a demo policy

Copy the production protection policy to your policies directory:

```bash
cp policies/prod-protection.yaml my-policies/
```

Or create a minimal block policy from scratch:

```yaml
# my-policies/block-deletes.yaml
id: block-deletes
name: Block all deletions
match:
  upstream: "*"
  tool: "*"
when:
  - condition: action_class_in
    values: ["write.delete"]
action: block
reason: "Deletions require manual approval"
priority: 90
```

Place the file in the directory referenced by `policies.dir` in `tessera.yaml`.
Tessera reloads policies automatically when `reload: watch` is set (the default).

## Step 4 — Verify

```bash
# Health check — confirms Tessera is up and policies loaded
curl http://localhost:8080/healthz
```

Expected response shape:

```json
{
  "status": "ok",
  "policy_state": { "loaded": 1, "errors": 0 }
}
```

Test that a destructive tool call is blocked:

```bash
curl -s -X POST http://localhost:8080/mcp/github \
  -H "Authorization: Bearer $TESSERA_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "delete_repository",
      "arguments": { "owner": "my-org", "repo": "my-repo" }
    }
  }' | jq .error.code
```

Expected output: `-32603` (blocked by Tessera policy).

## Troubleshooting

| Problem | Fix |
|---|---|
| 401 Unauthorized | Verify the `Authorization: Bearer <token>` header matches `TESSERA_BEARER_TOKEN` |
| 404 on `/mcp/github` | Confirm `name: github` in `tessera.yaml` upstreams matches the URL path segment |
| Policy not loading | Run `curl http://localhost:8080/healthz` and check `policy_state.loaded > 0` |
| Upstream connection refused | Verify your MCP server is running on the URL configured under `upstreams` |
| Tool call passes when expecting block | Check `policies.mode` is `enforcement`, not `log_only` or `observation` |

## Next steps

- [Cursor Hooks recipe](cursor-hooks.md) — intent-aware enforcement via Cursor Hooks
- [Claude Code recipe](claude-code.md) — wiring Claude Code to Tessera
- [Policy library](../policies/README.md) — reference policies to copy
