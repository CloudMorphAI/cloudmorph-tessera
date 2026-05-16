# Example: Wrap Claude Code with Tessera

Route Claude Code's GitHub MCP calls through Tessera for policy enforcement and
audit. Takes 5 minutes.

## Prerequisites

- Python 3.11+ with `pip install cloudmorph-tessera`
- A running GitHub MCP server (e.g. `node github-mcp-server.js` on port 5000)
- Claude Code installed

## Option A — Auto-install via CLI

```bash
# 1. Generate a bearer token and start Tessera
export TESSERA_BEARER_TOKEN="tk_cc_$(openssl rand -hex 16)"
tessera serve --config tessera.example.yaml

# 2. In a second terminal, register the upstream with Claude Code
tessera install-claude-code \
  --upstream-name github \
  --tessera-url http://localhost:8080 \
  --token "$TESSERA_BEARER_TOKEN"
```

`tessera install-claude-code` writes an `mcpServers.github` entry into
`~/.claude.json` (macOS/Linux) or `%APPDATA%\Claude\claude.json` (Windows).
Pass `--upgrade` if an entry already exists and you want to replace it.

## Option B — Manual edit

If you prefer not to run the CLI subcommand, paste the contents of
`claude-config-example.json` into your `~/.claude.json` (creating the file if
it does not exist):

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

Replace `<your-token>` with the value you set in `TESSERA_BEARER_TOKEN`.

## Step 3 — Restart Claude Code

Restart the Claude Code application so it picks up the updated `claude.json`.
The MCP status indicator in the bottom status bar should show `github` as
connected.

## Step 4 — Verify with a live tool call

In Claude Code, ask:

> "List my GitHub issues for repo my-org/my-repo"

Watch Tessera's audit log in real time:

```bash
tessera audit tail --follow --audit-path /tmp/tessera-claude-code-audit.db
```

You should see a `passthrough` event (for `tools/list`) followed by a
`decision` event (for the `tools/call`). The `decision` event records the
policy outcome (`allow` / `block`) and an audit event ID.

## Policy in this example

`policies/block-destructive-issues.yaml` blocks any GitHub tool that performs a
`write.delete` action. To test enforcement, ask Claude Code to delete a GitHub
issue — Tessera will block the call and Claude Code will receive a JSON-RPC
error response.

To switch from enforcement to dry-run (log only), change `mode: enforcement` to
`mode: log_only` in `tessera.example.yaml`. Tessera reloads policies
automatically when the file changes.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Claude Code shows server as disconnected | Confirm `tessera serve` is running; check `curl http://localhost:8080/healthz` |
| 401 Unauthorized in audit log | Bearer token in `claude.json` does not match `TESSERA_BEARER_TOKEN` |
| 404 on `/mcp/github` | The `name:` field under `upstreams` in `tessera.yaml` must match the URL path segment (`github`) |
| Tool calls pass when expecting block | `mode` must be `enforcement`, not `log_only` or `observation` |
| Upstream connection refused | Start the GitHub MCP server on the port listed under `upstreams.url` |

## Related

- [recipes/claude-code.md](../../recipes/claude-code.md) — in-depth recipe with curl test steps
- [recipes/generic-shell-hook.md](../../recipes/generic-shell-hook.md) — fallback for CLIs without MCP support
- [policies/](./policies/) — example policies for this demo
