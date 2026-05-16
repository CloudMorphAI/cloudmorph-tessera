# Example: VS Code MCP Config (Copilot / Continue / Cline)

Route GitHub MCP calls from any VS Code extension that consumes MCP servers
through Tessera for policy enforcement and audit. Takes 5 minutes.

This works for **GitHub Copilot Chat** (VS Code 1.99+), **Continue**, **Cline**,
and any other VS Code extension that respects the workspace-level MCP server
config at `.vscode/settings.json`.

## Prerequisites

- VS Code 1.99 or later (earlier versions do not support the `mcp.servers`
  workspace setting)
- Python 3.11+ with `pip install cloudmorph-tessera`
- A running GitHub MCP server (e.g. `node github-mcp-server.js` on port 7000)

## Step 1 — Start Tessera

```bash
# Generate a bearer token and export it so tessera and VS Code both see it
export TESSERA_BEARER_TOKEN="tk_vsc_$(openssl rand -hex 16)"

# Start Tessera pointing at this example's policy directory
tessera serve --config examples/wrap_vscode_copilot/tessera.example.yaml
```

Tessera is now listening on `http://localhost:8080`.

## Step 2 — Open this directory as a VS Code workspace

```bash
code examples/wrap_vscode_copilot/
```

The `.vscode/settings.json` file already declares a Tessera MCP server entry
under `mcp.servers`. VS Code reads it automatically on workspace open.

The `TESSERA_BEARER_TOKEN` environment variable must be set in the terminal
session that launches VS Code so that VS Code inherits it.

On Windows, set the variable in the same PowerShell session before running
`code`:

```powershell
$env:TESSERA_BEARER_TOKEN = "tk_vsc_<your-token>"
code examples/wrap_vscode_copilot/
```

## Step 3 — Confirm the server appears in VS Code

Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run:

- **GitHub Copilot Chat**: `MCP: List Servers` — `tessera-github` should be
  listed as connected.
- **Continue / Cline**: check the extension's sidebar panel; the server list
  shows connected MCP providers.

## Step 4 — Verify policy enforcement

In Copilot Chat (or Continue / Cline), ask:

> "Delete GitHub issue #42 from my-org/my-repo"

Tessera will evaluate the call against `policies/block-destructive-issues.yaml`
and return a block response. The extension will surface the JSON-RPC error
message from Tessera:

```
Issue deletion is blocked by policy. Use the GitHub UI for destructive operations.
```

Watch the live audit stream:

```bash
tessera audit tail --follow --audit-path ./tessera-audit.db
```

## Step 5 — Switch to enforcement vs. log-only

`tessera.example.yaml` defaults to `mode: enforcement`. To observe calls
without blocking, change it to `mode: log_only` and restart Tessera. Tessera
reloads policies automatically on config change when `reload: watch` is set.

## Config note — VS Code MCP namespace

VS Code 1.99 introduced the `mcp.servers` workspace setting. Some older
documentation refers to extension-specific keys like `github.copilot.chat.mcp.servers`
(pre-1.99 Copilot preview). The canonical key as of VS Code 1.99+ is
`mcp.servers` in `.vscode/settings.json` and that is what this example uses.

If your workspace shows the server as "unsupported" or missing, check your VS
Code version and consult the [VS Code MCP documentation](https://code.visualstudio.com/docs/copilot/chat/mcp-servers).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Server does not appear in VS Code | VS Code version < 1.99; update VS Code |
| 401 Unauthorized in audit log | `TESSERA_BEARER_TOKEN` in the environment does not match the value VS Code is sending; restart VS Code from the same terminal where the variable is set |
| 404 on `/mcp/github` | The `upstreams[].name` field in `tessera.example.yaml` must match the URL path segment (`github`) |
| Tool calls pass when expecting block | Confirm `mode: enforcement` in `tessera.example.yaml` |
| Upstream connection refused | Start the GitHub MCP server on the port listed under `upstreams[].url` |
| `${env:TESSERA_BEARER_TOKEN}` not expanded | VS Code only expands `${env:VAR}` in settings when VS Code itself was launched from a shell where the variable is set |

## Related

- [examples/wrap_claude_code/](../wrap_claude_code/) — same pattern for Claude Code (`~/.claude.json`)
- [recipes/cursor-hooks.md](../../recipes/cursor-hooks.md) — Cursor hooks-based wiring
- [recipes/generic-shell-hook.md](../../recipes/generic-shell-hook.md) — fallback for editors without MCP support
- [docs/INTEGRATIONS.md](../../docs/INTEGRATIONS.md) — per-client config reference
