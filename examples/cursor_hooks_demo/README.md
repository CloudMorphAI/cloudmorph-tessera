# Cursor Hooks Demo — Tessera in 60 Seconds

This demo shows Tessera blocking a destructive MCP tool call via Cursor Hooks.

## What you'll see

An AI agent tries to call `aws.s3.delete_bucket`. Tessera blocks it in enforcement mode
and returns a JSON-RPC error with the policy reason. The Cursor hook receives the intent
envelope before the call and logs the audit event after.

## Prerequisites

- Python 3.12+
- Tessera installed: `pip install cloudmorph-tessera`
- This directory: `cd examples/cursor_hooks_demo`

## Step 1 — Install the hook

```bash
tessera install-cursor-hooks \
  --cursor-config-dir . \
  --tessera-url http://localhost:8080
```

## Step 2 — Start the mock MCP server

```bash
# In terminal 1
python mock_mcp_server.py
```

## Step 3 — Start Tessera

```bash
# In terminal 2
tessera serve --config tessera.yaml
```

## Step 4 — Run the demo

```bash
# In terminal 3
bash test.sh
```

Expected output:

```
[ALLOW]  aws_s3_list_buckets → 200 OK (Tessera: allow, policy: no_match)
[BLOCK]  aws_s3_delete_bucket → JSON-RPC -32603 (Tessera: block, reason: Destructive operation blocked by demo policy)
```

## Step 5 — Check the audit log

```bash
tessera audit list --db /tmp/tessera-demo-audit.db
```

You should see two events: one passthrough (list_buckets) and one block (delete_bucket).
