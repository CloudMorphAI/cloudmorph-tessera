# Quickstart — Tessera Cloud in 10 minutes

This path wires the local Tessera proxy to your Tessera Cloud dashboard so you can author a policy in the browser, watch it block a live tool call in your IDE, then export and independently verify the audit-grade evidence. Every step is a real command — copy, paste, go.

## 1. Install

```bash
pip install cloudmorph-tessera
```

Confirm it's on your PATH:

```bash
tessera version
```

## 2. Authenticate

```bash
tessera login
```

Opens a browser to `tessera.cloudmorph.ai`, completes an OAuth 2.1 PKCE handshake, and writes a token to `~/.tessera/oauth.json` (mode `0600`). Nothing else touches this file.

## 3. Pull your dashboard policies

```bash
tessera config sync
```

Fetches the policy set from your Tessera Cloud dashboard and caches it locally at `~/.tessera/policy-cache.db`. Prints the number of policies cached. Local enforcement never depends on a live network call after this point — `tessera serve --cloud` reads the cache.

## 4. Start the proxy in cloud mode

```bash
TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" tessera serve --cloud
```

`--cloud` layers your dashboard-authored policies on top of the 25 bundled defaults and re-syncs on the periodic timer (force a refresh anytime with `tessera config sync`). Tessera is now listening on `http://127.0.0.1:8080/mcp`.

## 5. Author a policy in the dashboard

In the Tessera Cloud dashboard, add a policy — for example, block any `delete`-class call against a resource tagged `production`. Save it, then run `tessera config sync` again (or wait for the next auto-refresh) to pull it into the local cache.

## 6. Wire Tessera into Cursor or Claude Code

Add to `~/.cursor/mcp.json` (or the equivalent Claude Code / Claude Desktop MCP config):

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

Restart the IDE. Every tool call your agent makes now routes through Tessera.

## 7. Watch the block happen

Ask your agent to perform the action your new policy targets — e.g. delete a production resource. The agent's tool call is evaluated locally, in-process, against your policy. If it matches, the agent gets back a final `POLICY_BLOCK` result (not a retryable error) naming the `policy_id` and the reason. The call never reaches the upstream MCP server.

## 8. Export the evidence bundle

```bash
tessera audit export --from 2026-07-01 --to 2026-07-10 --out ./evidence
```

Produces `./evidence/` containing `events.jsonl`, `manifest.json`, `summary.html`, and `VERIFY.md` — every decision in range, Ed25519-signed as one self-contained bundle.

## 9. Verify it offline

```bash
tessera audit verify --dir ./evidence
```

Recomputes the hash chain and the signature independently from `events.jsonl` — no network call, no trust in `manifest.json`'s stated values. Exit code `0` means the bundle is intact.

Open `./evidence/summary.html` to see the same events rendered against the EU AI Act / India DPDP / SOC 2 compliance mappings.

## Next

- Full policy authoring reference: [docs/POLICIES.md](docs/POLICIES.md)
- Configuration schema: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- IDE-specific wiring (Claude Desktop, VS Code Copilot, Continue, Cline): [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)
- Environment diagnostics if something isn't wired correctly: `tessera doctor`
