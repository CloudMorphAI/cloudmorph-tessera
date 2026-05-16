# Wrap the OpenAI SDK — Tessera as an MCP Firewall

This example shows how to route OpenAI tool calls through Tessera before they reach
an MCP upstream. The model decides which tool to call; your code dispatches that
decision through Tessera, which applies your policies and audits the call.

## Why is this example longer than the Anthropic SDK example?

The Anthropic Python SDK has a native MCP tool type: you pass `{"type": "mcp", "url":
"http://localhost:8080/mcp/github", ...}` and the SDK handles the dispatch internally.
Tessera sits transparently in that path.

The OpenAI API has no equivalent MCP-native tool type today. The model returns a
`tool_calls` object in its response; your code must read it and POST the call to
Tessera yourself. `client.py` shows that manual dispatch loop — it is ~15 lines of
boilerplate that you copy once per project.

## Prerequisites

- Python 3.12+
- `pip install cloudmorph-tessera openai httpx`
- An OpenAI API key: `export OPENAI_API_KEY=sk-...`
- A Tessera bearer token: `export TESSERA_BEARER_TOKEN=<token>`

## Step-by-step

**Step 1 — start the mock MCP server (Terminal 1)**

```bash
python ../cursor_hooks_demo/mock_mcp_server.py
```

This listens on port 9999 and responds to any `tools/call` with a success payload.

**Step 2 — start Tessera (Terminal 2)**

```bash
cp tessera.example.yaml tessera.yaml
tessera serve --config tessera.yaml
```

Tessera binds on `http://localhost:8080` and loads `policies/block-destructive-issues.yaml`.

**Step 3 — run the example (Terminal 3)**

```bash
python client.py
```

## Expected output

```
{'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': 'ok'}]}}
```

The model asks to create an issue; Tessera allows it (create is not a destructive
operation); the mock upstream returns success.

To see a block, change the tool name in `tools[0]["function"]["name"]` to
`github_delete_issue` and re-run. Tessera returns a JSON-RPC error:

```
{'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32603, 'message': 'blocked', 'data': {'reason': 'Destructive GitHub operation blocked by policy'}}}
```

## Inspect the audit log

```bash
tessera audit tail --audit-path /tmp/tessera-openai-demo-audit.db
```

You should see one `decision` event per tool call with `action: allow` or
`action: block` and the matched policy ID.

## How the dispatch works

```
OpenAI API
  └─ returns tool_calls[0].function.name + .arguments
        └─ client.py POSTs to http://localhost:8080/mcp/github
              └─ Tessera policy engine evaluates the call
                    ├─ ALLOW → forwards to mock MCP server, returns result
                    └─ BLOCK → returns JSON-RPC error, nothing forwarded
```

## Files in this example

| File | Purpose |
|------|---------|
| `client.py` | Minimal OpenAI → Tessera dispatch loop |
| `mock_github_mcp.py` | Pointer to the reusable mock MCP server |
| `tessera.example.yaml` | Tessera config for this example |
| `policies/block-destructive-issues.yaml` | Sample policy blocking destructive ops |

## Next steps

- `examples/wrap_langchain/` — LangChain `MCPToolNode` routed through Tessera
- `examples/wrap_claude_code/` — Claude Code `~/.claude.json` config pointing at Tessera
- `docs/POLICIES.md` — full policy condition reference
