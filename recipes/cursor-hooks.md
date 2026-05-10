# Recipe: Cursor + Tessera via Cursor Hooks (intent-aware)

**Time:** 10 minutes
**Prerequisite:** Tessera running, Cursor v1.7+ installed

> **60-second demo:** [screencast placeholder — examples/cursor_hooks_demo/SCREENCAST.md]

> **Note:** Cursor Hooks v1.7 beta has known bugs on the `allow` and `ask` paths.
> The `deny` path is reliable. Tessera uses the `beforeMCPExecution` hook for intent
> enrichment only — final enforcement happens at the proxy level.

## Overview

This recipe wires Tessera into Cursor's hook system. The hook fires before every
MCP tool call, POSTs to Tessera's `/intent` endpoint to enrich the audit envelope,
and returns the intent metadata to Cursor. The proxy enforces policies on the actual
MCP request.

This is more capable than the [cursor-mcp-json.md](cursor-mcp-json.md) recipe:
intent strings from the agent are captured and stored in every audit event, giving
you richer policy conditions and more actionable audit logs.

## Quick demo with the worked example

The `examples/cursor_hooks_demo/` directory contains a complete runnable example.

```bash
cd examples/cursor_hooks_demo

# Terminal 1: mock MCP server
python mock_mcp_server.py

# Terminal 2: Tessera with demo policy (blocks write.delete)
tessera serve --config tessera.yaml

# Terminal 3: run demo
bash test.sh
```

Expected output:

```text
[PASS] List buckets: allowed
[PASS] Delete bucket: blocked by Tessera
```

The demo uses the `mock` upstream defined in `examples/cursor_hooks_demo/tessera.yaml`.
The policy in `examples/cursor_hooks_demo/policies/` blocks `write.delete` actions.

## Full installation

### Step 1 — Start Tessera

```bash
export TESSERA_BEARER_TOKEN="tk_cursor_$(openssl rand -hex 16)"

tessera serve --config tessera.yaml
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

audit:
  path: /tmp/tessera-audit.db
  also_stdout: true
```

### Step 2 — Install the Cursor hook

```bash
tessera install-cursor-hooks \
  --tessera-url http://localhost:8080 \
  --token "$TESSERA_BEARER_TOKEN"
```

This copies `tessera_hook.py` into `~/.cursor/hooks/` and writes `hooks.json`.

Verify:

```bash
cat ~/.cursor/hooks/hooks.json
```

Expected content:

```json
{
  "hooks": [
    {
      "command": "tessera_hook.py",
      "events": ["beforeMCPExecution", "afterMCPExecution"],
      "env": {
        "TESSERA_URL": "http://localhost:8080"
      }
    }
  ]
}
```

### Step 3 — Update Cursor's mcp.json

Point Cursor at Tessera (same as the cursor-mcp-json.md recipe):

Locate your Cursor MCP config:

- **macOS / Linux:** `~/.cursor/mcp.json`
- **Windows:** `%APPDATA%\Cursor\mcp.json`

```json
{
  "mcpServers": {
    "github": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

The path `/mcp/github` matches the `name: github` upstream in `tessera.yaml`.
Replace `<your-token>` with the value you set in `TESSERA_BEARER_TOKEN`.

### Step 4 — Restart Cursor

Cursor loads hook configuration at startup. Restart Cursor after installing hooks.

### Step 5 — Verify

Use Cursor's Composer to invoke a GitHub MCP tool. Check the Tessera logs for
`event=intent_derivation` and `event=decision` entries.

```bash
# Tail the audit log
tessera audit list --db /tmp/tessera-audit.db --limit 10
```

You should see one `intent_derivation` event per tool call, followed by a `decision`
event showing `allow` or `block`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Hook not firing | Restart Cursor after installing; verify `hooks.json` exists under `~/.cursor/hooks/` |
| `TESSERA_URL` not set | Set `TESSERA_URL` in your shell before running `tessera install-cursor-hooks` |
| Cursor v1.7 beta: allow path not working | Known bug; enforcement is at proxy level, not hook level |
| `/intent` returns 401 | Check `--token` matches `TESSERA_BEARER_TOKEN` env var |
| 404 on `/mcp/github` | Confirm `name: github` in `tessera.yaml` upstreams matches the URL path segment |
| Policy not loading | Run `curl http://localhost:8080/healthz` and check `policy_state.loaded > 0` |

## What's next

- [Cursor mcp.json recipe](cursor-mcp-json.md) — simpler, no hooks needed
- [Claude Code recipe](claude-code.md) — same wiring for Claude Code
- [Policy library](../policies/README.md) — reference policies to add
