# Recipe: Claude Code to Tessera via claude.json

**Time:** 5 minutes
**Prerequisite:** Tessera running at `http://localhost:8080`

> **60-second demo:** [screencast placeholder — coming soon]

## Overview

This recipe replaces Claude Code's direct MCP server command with Tessera's
HTTP proxy URL. Tessera evaluates policies in **intent-blind** mode — enforcement
is based on tool name and argument patterns only.

Claude Code supports both a `command`-based transport (spawning a local process)
and a `url`-based HTTP transport. Tessera uses the HTTP transport, so you switch
the server entry from `command`/`args` to `url`/`headers`.

## Step 1 — Start Tessera

```bash
export TESSERA_BEARER_TOKEN="tk_claude_$(openssl rand -hex 16)"

tessera start --config tessera.yaml
```

Create a minimal `tessera.yaml` pointing at your existing MCP server:

```yaml
deployment_id: claude-dev

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

## Step 2 — Update Claude Code's claude.json

Locate your Claude Code MCP config:

- **macOS / Linux:** `~/.claude.json`
- **Windows:** `%APPDATA%\Claude\claude.json`

> **Note on `/mcp add`:** Claude Code's `/mcp add` command registers servers
> interactively, but it does not yet support setting custom HTTP headers.
> Direct JSON editing is the simplest path for adding the `Authorization` header
> Tessera requires.

**Before** — Claude Code spawns the MCP server as a local process:

```json
{
  "mcpServers": {
    "github": {
      "command": "node",
      "args": ["path/to/github-mcp-server.js"]
    }
  }
}
```

**After** — Claude Code calls Tessera's HTTP proxy instead:

```json
{
  "mcpServers": {
    "github": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_claude_<your-token>"
      }
    }
  }
}
```

The path `/mcp/github` matches the `name: github` upstream in `tessera.yaml`.
Replace `<your-token>` with the value you set in `TESSERA_BEARER_TOKEN`.

You can remove the original `command`/`args` entry entirely — Tessera forwards
the call to the configured upstream on your behalf.

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

Restart Claude Code after editing `claude.json` to pick up the new server
configuration. You can confirm the server is reachable via the Claude Code MCP
status indicator in the bottom status bar.

## Troubleshooting

| Problem | Fix |
|---|---|
| 401 Unauthorized | Verify the `Authorization: Bearer <token>` header matches `TESSERA_BEARER_TOKEN` |
| 404 on `/mcp/github` | Confirm `name: github` in `tessera.yaml` upstreams matches the URL path segment |
| Policy not loading | Run `curl http://localhost:8080/healthz` and check `policy_state.loaded > 0` |
| Upstream connection refused | Verify your MCP server is running on the URL configured under `upstreams` |
| Tool call passes when expecting block | Check `policies.mode` is `enforcement`, not `log_only` or `observation` |
| Claude Code does not show the server | Restart Claude Code after editing `claude.json` |

## Next steps

- [Cursor recipe](cursor-mcp-json.md) — wiring Cursor to Tessera
- [Policy library](../policies/README.md) — reference policies to copy
- [prod-protection.yaml](../policies/prod-protection.yaml) — block destructive writes in production
