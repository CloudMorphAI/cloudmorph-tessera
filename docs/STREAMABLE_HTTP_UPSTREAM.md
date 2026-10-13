# Streamable HTTP upstream: supported pilot scope

Tessera's upstream adapter supports bounded MCP request/response exchanges over
HTTP POST, with either a JSON response or an SSE response stream. This is a
documented subset, not a claim of compatibility with every MCP server or feature.
The reference is the [MCP 2025-11-25 transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [initialization lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

## Endpoint and lifecycle

Configure `kind: mcp_streamable_http`. An origin-only `url`, such as
`http://127.0.0.1:8000`, retains the legacy `/mcp` suffix. A URL containing a path,
such as `http://127.0.0.1:8000/custom/mcp`, is the complete endpoint, including its
trailing slash and query if supplied. Redirects are not followed. URL userinfo and
fragments are rejected. Configure the final endpoint directly.

The adapter independently sends `initialize`, validates its matching result and
negotiated version, then sends `notifications/initialized` and requires empty
HTTP 202 before forwarding the original message. It offers `2025-11-25` and
accepts `2025-11-25`, `2025-06-18`, or `2025-03-26`. Subsequent POSTs include the
negotiated `MCP-Protocol-Version` and, when supplied by the server, the validated
`Mcp-Session-Id`. No client capabilities are advertised.

Downstream initialize/initialized belong to the proxy, which terminates that
lifecycle locally. They must not be forwarded through this adapter. Upstream
session identifiers and negotiated versions are private to the adapter; they are
not downstream session identifiers.

`forward(body, *, session_scope='default')` preserves the direct-call default.
The proxy must pass a trusted key derived from its authenticated principal and
context for every forwarded message, including discovery. Never derive that key
from a caller-supplied MCP session header. Each configured client/auth context
holds its own records; no name-global cache exists. Direct consumers using the
default scope intentionally share that one scope within the instance.

There are at most 128 records per adapter. Initialization and forwarding within
one scope are serialized; different scopes can run concurrently. Active or
waiting records cannot be evicted. Idle records are pruned after
`session_timeout_s`; if no record can expire, a new scope fails closed at capacity.
Idle pruning discards local state without a synchronous remote DELETE. Orderly
shutdown attempts DELETE for retained sessions under one bounded deadline;
servers may refuse DELETE, and successful remote cleanup is not guaranteed.

## Responses and failures

- Requests require HTTP 200 with `application/json` or `text/event-stream`.
  Notifications and client responses require empty HTTP 202 and return `{}` to
  the proxy. The inbound handler emits an empty 202 for accepted notifications.
- JSON-RPC version, object shape, result/error exclusivity and error fields are
  checked. MCP IDs must be strings or integers and match in both type and value.
  Unrelated SSE responses are never substituted for the requested response.
- SSE is decoded incrementally as UTF-8, including CR, LF, CRLF, comments,
  multiline data and empty priming events. Bounded server notifications are
  consumed without forwarding. A server-initiated request fails explicitly.
- A session-bearing HTTP 404 invalidates only that scoped session. The failed
  request is not replayed. A later caller request performs a fresh handshake.
  No authorization, network, timeout, parsing or application error triggers a
  replay. Application JSON-RPC errors are returned as received, without treating
  words such as “session” or “expired” as transport expiry.
- Network/timeout outcomes may be unknown after the upstream received a request.
  Callers must reconcile mutations rather than assuming they did not execute.
  Local errors expose fixed diagnostic reasons, not raw exceptions, credentials,
  session IDs or scope keys. Valid upstream results/errors remain upstream data.

## Resource limits and unsupported features

`request_timeout_s` is a positive finite timeout for HTTP phases and the entire
forward operation, including scope-lock waiting, initialization and notification.
Responses are closed on success, failure, timeout and caller cancellation.
Decoded input is limited to 1 MiB per response; SSE is additionally limited to
65,536 characters per line, 262,144 characters per data event and 256 event
boundaries. Scope keys and upstream session IDs are limited to 1,024 characters.
These limits are fixed in the adapter, not additional configuration fields.
Compressed responses are rejected so decompression cannot bypass byte accounting.

GET subscriptions, event-ID resumption, reconnection/polling, server-initiated
requests, sampling, elicitation and roots are unsupported. A stream ending before
the matching response produces an explicit error rather than resending the POST.
No cancellation notification is synthesized on timeout. A supported pilot server
must complete each POST exchange within these limits without requiring those
features. This transport does not certify a server, authenticate an external
signer, or establish that a tool action is safe; the policy gate is separate.

## Verification status

Local verification uses injected synthetic HTTP transports only. Historical AWS
dogfood captures are not current protocol-conformance evidence. See the Sprint 3
Aegis checkpoint for exact offline scenarios, results and outstanding limits.
