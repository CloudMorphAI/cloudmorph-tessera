# Recipe: Generic Shell Hook for IDEs and CLIs without MCP Support

**Module:** P1-17  
**Time:** 15 minutes  
**Use case:** Bespoke CLIs or internal orchestrators that cannot speak MCP directly

## What this is

A fallback integration for tools and CLIs that do not speak MCP but still want
Tessera's policy gating and audit trail. Instead of routing through the full MCP
proxy (`/mcp/<upstream>`), this 20-line bash wrapper posts a raw tool call to
Tessera and derives a policy decision without contacting an upstream.

Suitable for internal pipelines, custom deployment scripts, or any orchestrator
that invokes "tool-like" operations and wants a consistent audit log.

## Important: /intent vs /mcp endpoint distinction

Tessera exposes two relevant endpoints:

- **`POST /intent`** — derives verbs and purpose from a tool name and arguments,
  emits an `intent_derivation` audit event, and returns intent metadata. It does
  **not** evaluate policies and does not return a `decision`. Use this to
  pre-annotate a call before forwarding it manually.
- **`POST /mcp/<upstream>`** — the full proxy path. Evaluates policies, enforces
  the configured mode (`enforcement` / `log_only` / `observation`), and
  optionally forwards to the upstream. Use this for policy gating.

The hook below uses `/mcp/<upstream>` wrapped in a JSON-RPC envelope so that
policy evaluation fires. It does **not** actually reach an upstream because no
upstream is configured for the dummy name — it blocks at the policy layer and
returns the audit event ID. For real upstream forwarding, configure the upstream
in `tessera.yaml` and remove the dummy endpoint.

## The hook

```bash
#!/usr/bin/env bash
# tessera-hook.sh — submit a tool call to Tessera, return policy decision
# Usage: ./tessera-hook.sh <tool_name> '<args_json>'
# Exit: 0=allow  1=block  2=approval-required  3=unknown

set -euo pipefail

TOOL_NAME="${1:?tool name required}"
ARGS_JSON="${2:-{}}"

TESSERA_BASE="${TESSERA_BASE:-http://localhost:8080}"
TESSERA_BEARER_TOKEN="${TESSERA_BEARER_TOKEN:?set TESSERA_BEARER_TOKEN}"
UPSTREAM="${TESSERA_UPSTREAM:-internal}"

RESPONSE=$(curl -s -X POST "${TESSERA_BASE}/mcp/${UPSTREAM}" \
  -H "Authorization: Bearer ${TESSERA_BEARER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc \
        --arg tool  "${TOOL_NAME}" \
        --argjson args "${ARGS_JSON}" \
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":$tool,"arguments":$args}}')")

# A Tessera block/approval response has an "error" key; an allow has "result".
ERROR_CODE=$(printf '%s' "${RESPONSE}" | jq -r '.error.code // empty')
REASON=$(printf '%s' "${RESPONSE}"     | jq -r '.error.data.reason // empty')
EVENT_ID=$(printf '%s' "${RESPONSE}"   | jq -r '.result._meta.tessera_audit_event_id // .error.data._meta.tessera_audit_event_id // empty')

if [ -z "${ERROR_CODE}" ]; then
  # No error block — allowed
  echo "TESSERA: allow (event ${EVENT_ID})"
  exit 0
fi

case "${REASON}" in
  approval_required*) echo "TESSERA: approval required — ${REASON} (event ${EVENT_ID})" >&2; exit 2 ;;
  *blocked*|blocked)  echo "TESSERA: blocked — ${REASON} (event ${EVENT_ID})" >&2; exit 1 ;;
  *)                  echo "TESSERA: error ${ERROR_CODE} — ${REASON} (event ${EVENT_ID})" >&2; exit 3 ;;
esac
```

Save as `tessera-hook.sh` and make it executable:

```bash
chmod +x tessera-hook.sh
```

## Usage

```bash
export TESSERA_BASE="http://localhost:8080"
export TESSERA_BEARER_TOKEN="tk_your_token"
export TESSERA_UPSTREAM="internal"   # must match an upstream name in tessera.yaml

./tessera-hook.sh github_create_issue '{"title":"test","body":"hello"}'
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Tessera allowed the call |
| 1 | Tessera blocked the call (policy match) |
| 2 | Tessera requires manual approval |
| 3 | Unexpected error code or unreachable |

## Minimal tessera.yaml for this pattern

```yaml
deployment_id: shell-hook-demo

auth:
  type: bearer

policies:
  dir: ./policies
  mode: enforcement
  default_action: allow

# No upstreams required if the hook is only used for policy gating.
# Add an upstream entry when you want Tessera to also forward the call.
upstreams: []

audit:
  path: /tmp/tessera-hook-audit.db
  also_stdout: true
```

When `upstreams` is empty, Tessera returns a JSON-RPC error with
`reason: "unknown upstream: 'internal'"` on allow decisions — the hook still
returns exit 0 only if the policy decision is allow and no block fires. If you
need actual forwarding, add the upstream:

```yaml
upstreams:
  - name: internal
    url: http://your-actual-mcp-server:9000
    timeout_seconds: 10
```

## Integrating into a CI pipeline

```bash
# deploy.sh — gate each destructive step through Tessera
./tessera-hook.sh aws_delete_stack '{"stack_name":"prod"}' || {
  echo "Deployment blocked by Tessera policy. Check audit log."
  exit 1
}

# Proceed with deployment
aws cloudformation delete-stack --stack-name prod
```

## Caveats

- The hook POSTs a full `tools/call` JSON-RPC body, so policies that match on
  `tool` name and `arguments` fields work as expected.
- `/intent` is not used here. If you want to pre-derive verbs for richer audit
  context, POST to `/intent` first (`{"tool_name": "...", "tool_input": {...}}`)
  and annotate the `/mcp` call with the returned `tessera_intent` metadata under
  `params._meta`.
- The audit event ID is embedded in the JSON-RPC response under
  `result._meta.tessera_audit_event_id` (allow path) or
  `error.data._meta.tessera_audit_event_id` (block/approval path). Pass it to
  downstream systems for end-to-end tracing.
- `curl` and `jq` must be available on the host. No Python or Node dependency.

## Related

- [examples/wrap_claude_code/](../examples/wrap_claude_code/) — Claude Code MCP integration
- [recipes/claude-code.md](./claude-code.md) — full HTTP proxy recipe for Claude Code
- [recipes/cursor-hooks.md](./cursor-hooks.md) — Cursor Hooks integration
