# Streamable HTTP in the staged Tessera checkout

This describes local development, not a published release or verified desktop
client integration. MCP reference:
https://modelcontextprotocol.io/specification/2025-11-25/basic/transports .

## Two independent transport boundaries

Clients connect to `/mcp` for the namespaced catalog, or `/mcp/<upstream>`
for a selected upstream. Tessera offers **stateless JSON-response mode**.
Initialize locally negotiates `2025-11-25`, `2025-06-18` or `2025-03-26`, advertises
tools support, and issues no downstream session ID. Use the negotiated
`MCP-Protocol-Version` on later requests. Real `notifications/initialized`
receives empty HTTP 202. Authenticated GET/DELETE return 405: there is no
server-initiated event stream or downstream session to terminate.

An upstream configured as `kind: mcp_streamable_http` has its own initialization,
version and session. It accepts either JSON replies or bounded SSE replies,
correlates response IDs strictly and isolates sessions by authenticated
principal and scope. Tessera consumes SSE and returns the correlated JSON
response to its client. Upstream session IDs never become downstream identities.
See [upstream limits](STREAMABLE_HTTP_UPSTREAM.md).

The existing default `bearer` upstream is a simple JSON transport. Select the
Streamable HTTP kind explicitly for a server that requires MCP initialization
or returns SSE. Origin-only upstream URLs gain `/mcp`; an explicit URL path
is honored as the complete endpoint. Supply an exact endpoint URL.

## Configuration

```yaml
listen:
  host: 127.0.0.1
  port: 8080
  allowed_origins: []
upstreams:
  - name: partner
    kind: mcp_streamable_http
    url: http://127.0.0.1:9000/mcp
    auth_header: "Bearer ${UPSTREAM_MCP_TOKEN}"
    session_timeout_s: 300
    request_timeout_s: 10
policies:
  dir: /path/to/reviewed/policies
  reload: none
  mode: enforcement
  default_action: block
```

This is a configuration example; no server or secret is supplied. If the
upstream has no authentication, omit auth_header. When supplied, environment
references must resolve or configuration fails. Configure downstream bearer/JWT
authentication separately; upstream authentication does not authenticate users
of Tessera. Fresh defaults remain log-only until the operator enables enforcement.

Non-loopback browser origins must appear explicitly in allowed_origins as
scheme/host/optional-port, without path, wildcard, credentials, query or fragment.
Absent Origin is accepted for native clients. Loopback origins are accepted;
an arbitrary Host header never adds a trusted origin. This allowlist does not
enable CORS: browser deployments need a separate CORS/authentication review.

## Input and compatibility limits

POST uses a single UTF-8 JSON-RPC object, at most 1 MiB; batches, duplicate members,
nonfinite numbers, invalid request IDs, malformed params/metadata and unsolicited
responses are rejected before dispatch. JSON replies require an Accept range
that permits application/json; normal MCP clients should advertise both
application/json and text/event-stream. Invalid protocol versions return400,
untrusted origins403, wrong request MIME415 and unacceptable response formats406.

Older JSON proxy callers may omit initialization fields or a protocol header;
the missing-version fallback is2025-03-26. Provided initialization fields are
validated. Legacy id-bearing initialized notifications get a local JSON result;
real notifications have no id and get empty202. These are deliberate compatibility
concessions, not a claim of exhaustive protocol conformance.

No automatic original-call replay follows an upstream failure. Session404 drops
local session state so a **new caller request** can initialize again. Timeout or
disconnect may leave execution outcome unknown; investigate before repeating a
mutation. Server-to-client requests, SSE resumability, legacy HTTP+SSE endpoints,
pagination completeness and broad desktop-client certification are not supplied
by this stage. Proxy policy enforcement applies only to routed methods according
to the existing policy configuration; protocol compatibility does not remove
the documented passthrough-method and bypass boundaries.

## Collect acceptance evidence

Run [the synthetic pilot](../examples/synthetic_pilot/README.md) to exercise the
actual engine/adapter without any external system. Save named client/version
transcripts separately using [pilot compatibility records](pilot/COMPATIBILITY_RECORD.md).
An offline synthetic run does not establish Cursor, Claude Desktop or another
application's compatibility. Review selected intelligence content before use:
valid signatures alone do not establish runtime schema/input/pricing support.
