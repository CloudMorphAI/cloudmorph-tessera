# Cursor Hooks Demo — Tessera in 60 Seconds

This demo shows Tessera blocking a destructive MCP tool call via Cursor Hooks.

## What you'll see

An AI agent tries to call `aws_s3_delete_bucket`. Tessera blocks it in enforcement mode
and returns a JSON-RPC error with the policy reason. The Cursor hook receives the intent
envelope before the call and logs the audit event after.

## Prerequisites

- Python 3.12+
- Tessera installed: `pip install cloudmorph-tessera`
- This directory: `cd examples/cursor_hooks_demo`

## Quickstart — automated

```bash
bash test.sh
```

`test.sh` starts the mock upstream + Tessera in the background, runs two MCP `tools/call`
requests through Tessera (one expected to allow, one expected to block), then cleans up.
No setup required.

Expected output:

```
[ALLOW]  aws_s3_list_buckets → 200 OK
[BLOCK]  aws_s3_delete_bucket → JSON-RPC -32603 (blocked by demo policy)
=== Demo passed ===
```

## Manual mode (3 terminals)

If you'd rather see each piece running individually:

**Terminal 1 — mock MCP server (port 9999):**

```bash
python mock_mcp_server.py
```

**Terminal 2 — Tessera (port 8080):**

```bash
tessera serve --config tessera.yaml
```

**Terminal 3 — fire the requests:**

```bash
# Should allow
curl -s -X POST http://localhost:8080/mcp/mock \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"aws_s3_list_buckets","arguments":{}}}'

# Should block
curl -s -X POST http://localhost:8080/mcp/mock \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"aws_s3_delete_bucket","arguments":{"bucket":"my-bucket"}}}'
```

## Wiring Cursor Hooks (optional)

To make Cursor itself drive this demo:

```bash
tessera install-cursor-hooks \
  --cursor-config-dir . \
  --tessera-url http://localhost:8080
```

This installs `tessera_hook.py` + `hooks.json` into `./` so Cursor (when pointed at this
directory) routes its `beforeMCPExecution` and `afterMCPExecution` events through Tessera.

## Inspect the audit chain

```bash
tessera audit verify --audit-path /tmp/tessera-demo-audit.db
```

You should see two decision events plus a `startup` event in a verified hash chain.
