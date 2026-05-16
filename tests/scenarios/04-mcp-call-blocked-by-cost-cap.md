# 04 — MCP call blocked by cost cap

## Starting state
- Same as scenario 03 (Tessera running with bundled policies, mock upstream up, bearer token configured)
- A loaded paid pack with cost caps (developer-tier or higher; use `bedrock-claude-opus-cap.yaml` from `aws-cost-aware-defaults` as the canonical example)
- Either: (a) live CDN fetch via scenario 02 done first, OR (b) copy the pack manually into the policy directory for this smoke

## User actions (step-by-step)
1. Confirm the pack is loaded: `tessera policy list | grep bedrock-claude-opus-cap`
2. Issue an MCP `tools/call` for `bedrock_InvokeModel` targeting `anthropic.claude-3-opus-20240229-v1:0` with a prompt that would exceed the configured threshold (the pack ships with a default per-call cap; the smoke prompt should be deliberately oversize, e.g., 100K-token input)
3. Inspect the response code and body
4. Check the audit log for the new row: `tessera audit tail --last 1`

## Expected observable result
- HTTP 403 from Tessera (the engine's default block response code; configurable)
- Response body contains `policy_decision: "block"`, `reason: "cost_cap_exceeded"` or similar, and the policy ID that triggered (`aws_bedrock_claude_opus_cap` or whatever the pack names it)
- Audit log row has `decision=block`, `tool=bedrock_InvokeModel`, `policy_matches=[<the cost-cap policy id>]`
- Upstream MCP server received NO request (the block happened pre-upstream — verify by checking the mock upstream's request log if available)
- Tessera process log shows `event=mcp_call_blocked` at WARN level with the policy id

## Failure modes to watch for
- HTTP 200 instead of 403 → owner: cost-cap policy YAML did not load OR the request didn't trigger the threshold → check `tessera policy explain bedrock_InvokeModel`
- 403 with WRONG policy id → owner: policy ordering; a higher-priority deny policy matched first. Check `tessera policy list --order-by-priority`
- Audit row decision says `allow` but HTTP was 403 → owner: decision/log divergence; check `tessera/engine/evaluator.py` decision-to-emit wiring
- Upstream DID receive the call → owner: middleware ordering; the block happened post-upstream which means the proxy short-circuit is wrong

## How to verify manually

```bash
# Issue an oversize Bedrock call
curl -s -X POST http://127.0.0.1:8400/mcp/bench-upstream \
  -H "Authorization: Bearer smoke-token-1234567890abcdef" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"bedrock_InvokeModel","arguments":{"modelId":"anthropic.claude-3-opus-20240229-v1:0","body":"{\"prompt\":\"<paste-100k-tokens-here>\",\"max_tokens_to_sample\":100000}"}}}'

tessera audit tail --last 1
tessera policy explain bedrock_InvokeModel
```

## Owner on failure
Cost-cap policy primitive — `tessera/engine/evaluator.py` (the `cost_cap` condition), `aws-cost-aware-defaults/v1.0.0/policies/bedrock-claude-opus-cap.yaml`

## Related code
- `tessera/engine/conditions/cost_cap.py`
- `tessera/engine/evaluator.py`
- `tessera-intelligence/packs/aws-cost-aware-defaults/v1.0.0/policies/bedrock-claude-opus-cap.yaml`
- `tessera/cost/` (per-series price tables)
