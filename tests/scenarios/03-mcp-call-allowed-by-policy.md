# 03 — MCP call allowed by policy

## Starting state
- Fresh install per scenario 01
- Tessera configured with default bundled policies (no intelligence sync required for this scenario — uses the policies shipped inside the package at `tessera/policies_default/`)
- Mock or real upstream MCP server reachable at `127.0.0.1:8401` (use `benchmarks/mock_upstream.py` for the cheapest path)
- Bearer token configured: `TESSERA_BEARER_TOKEN=smoke-token-1234567890abcdef`

## User actions (step-by-step)
1. Start mock upstream: `python benchmarks/mock_upstream.py &`
2. Start Tessera: `tessera serve --config benchmarks/bench-tessera.yaml --log-level info &`
3. Wait ~5 seconds for startup
4. Issue a benign MCP `tools/call` for an operation NOT in any deny / cost-cap / require-approval policy. Use `aws_s3_GetObject` with a small key as the canonical safe-read example.
5. Check the audit log: `tessera audit tail --last 5`

## Expected observable result
- HTTP 200 from Tessera; response body contains the upstream's `{"jsonrpc":"2.0","result":...}` payload
- Audit log shows a new row with `decision=allow`, `tool=aws_s3_GetObject`, `policy_matches=[]` (or only `observed`-mode matches)
- No `decision=block` or `decision=require_approval` in the row
- Tessera process log shows `event=mcp_call_allowed` at INFO level
- Latency p99 below 30ms on loopback (per `benchmarks/results/v0.4.0-production.md`); not a hard contract for this scenario, but a regression-detection signal

## Failure modes to watch for
- HTTP 401 from Tessera → owner: bearer token validation; check `TESSERA_BEARER_TOKEN` env matches the YAML config
- HTTP 403 with `policy_decision=block` → owner: policy engine; a bundled policy is matching unexpectedly. Run `tessera policy explain aws_s3_GetObject` to see which one
- HTTP 502 → owner: upstream proxy; the mock isn't running or the URL is wrong
- Audit row missing → owner: async audit emit queue; check `tessera/audit/async_emit.py` and the configured audit backend
- Audit row decision differs from observed log line → owner: audit-vs-decision wiring; the engine made one decision but logged another

## How to verify manually

```bash
# in shell 1
python benchmarks/mock_upstream.py

# in shell 2
export TESSERA_BEARER_TOKEN="smoke-token-1234567890abcdef"
tessera serve --config benchmarks/bench-tessera.yaml --log-level info

# in shell 3
curl -s -X POST http://127.0.0.1:8400/mcp/bench-upstream \
  -H "Authorization: Bearer smoke-token-1234567890abcdef" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"aws_s3_GetObject","arguments":{"Bucket":"test-bucket","Key":"small.txt"}}}'

tessera audit tail --last 5
```

## Owner on failure
Policy engine + proxy hot path — `tessera/engine/`, `tessera/proxy/`, `tessera/audit/async_emit.py`

## Related code
- `tessera/policies_default/` (the 24 bundled policies)
- `tessera/engine/evaluator.py`
- `tessera/proxy/middleware.py`
- `tessera/audit/async_emit.py`
