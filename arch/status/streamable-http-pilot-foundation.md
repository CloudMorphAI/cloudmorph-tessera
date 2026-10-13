# Streamable HTTP and synthetic pilot foundation

Implemented locally on 13 October 2026; not published or deployed. Founder requested
staged Sprint3 development with no PR/push. The public panel demo remains on its
existing deployed package. Source in this checkout is staged 1.0.0.

## Current implementation

- `tessera/mcp_http.py`: stateless JSON-response MCP endpoint boundary,
  local initialization, empty real-notification202, validated Origin/protocol/
  Accept/MIME, bounded UTF-8 object parsing and common metadata validation.
- `tessera/proxy.py`: authenticated GET/DELETE405, local downstream lifecycle,
  preserved unified discovery request IDs and principal+scope forwarding keys.
- `tessera/integrations/streamable_http/upstream.py`: independent handshake,
  bounded JSON/SSE response correlation, scoped sessions/locks/TTL and no
  automatic original-call replay. Expired404 clears the local session only.
- `tessera/config.py`: explicit browser Origin allowlist, positive Streamable
  HTTP timeouts and environment interpolation for upstream auth_header.
- Catalog verifier: actual outer fields must match signed canonical bytes;
  current producer-null and legacy omitted-envelope forms are supported.

Transport limits and legacy concessions are in `docs/STREAMABLE_HTTP.md` and
`docs/STREAMABLE_HTTP_UPSTREAM.md`. Server push, server-initiated requests,
resumption and broad client certification remain unsupported/unverified.

## Pilot and verification

`examples/synthetic_pilot/run.py` exercises real policy/proxy/transport code with
fictional tools, denied network and isolated environment. It preserves loose
unsigned audit, contact and acceptance records plus per-stage journal/progress.
Receipt checks match the unique actual decision, policy, tool, arguments and
principal. `docs/pilot/` provides partner, compatibility, external-review,
procurement and milestone records; those external outcomes are pending.

Existing suite: 950 passed, 8 skipped with external DNS/nonloopback sockets disabled.
Additional offline verification: 46 upstream scenarios, 28 inbound scenarios,
15 synthetic pilot checks, and signed-catalog tamper checks. This is internal
verification, not the proposed independent 500-case review or real-client matrix.
Private commands/receipts/checkpoints are in workspace-support's 13 October Sprint 3
audit directory. No new test files or product dependencies were added.

## Content readiness remains limited

Local reconciliation reports84 integrity checks pass and18 missing-price
references fail (nine distinct advertised files, checked from both catalogs).
119 of 130 inspected archived policies reject the current runtime schema. No
signed artifact, pack or catalog was rewritten or published. Valid signatures
do not establish runtime compatibility or price availability. Review selected
content and required inputs before any pilot; see intelligence repository
`arch/status/runtime-reconciliation.md` and `scripts/reconcile_runtime.py`.
