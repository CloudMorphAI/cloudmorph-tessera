# 01 — MCP Server Audit (`cloudmorph-mcp/`)

_The wedge. 1,241 LoC of TypeScript that today is a stateless gateway. To ship the firewall MVP, this is where ~70% of the 14-day work lands._

Reading order:
- §1.1 — what each file does today, line by line
- §1.2 — tool surface gap (existing 3, missing 9)
- §1.3 — transport gap
- §1.4 — auth & multi-tenancy gap
- §1.5 — policy engine (does not exist)
- §1.6 — intent capture (does not exist)
- §1.7 — audit log (skeletal)
- §1.8 — latency budget (currently impossible)
- §1.9 — operational gaps
- §1.10 — severity table
- §1.11 — what stays, what goes

---

## 1.1 Current state — line by line

### [src/index.ts](../../cloudmorph-mcp/src/index.ts) (111 LoC, 2026-04-16 worktree)

The bootstrap. Reads `CONTROL_CENTER_API_URL` from env and exits if missing (`index.ts:8-12`). Builds a JSON-line logger keyed by `MCP_LOG_LEVEL` (`:14-32`). Computes CORS origins from `MCP_ALLOWED_ORIGINS` (or legacy `CONTROL_CENTER_ALLOWED_ORIGINS`) — supports `*` wildcard but the resolver returns the first allowlist entry as the `Access-Control-Allow-Origin` value when the request's `Origin` header doesn't match anything (`:34-47`). That's a **subtle bug**: a request from `evil.com` will get back `Access-Control-Allow-Origin: <first-trusted-origin>` which most browsers will then simply reject — but the server has emitted a misleading header. Fix: return empty string when no match.

Mounts express with `json({limit: "1mb"})` (`:50`). One CORS middleware (`:51-68`) that responds 204 to OPTIONS. One request-completion logger (`:69-80`). Constructs a `RateLimiter` from three `MCP_RATE_LIMIT_*` env vars (`:81-85`). Mounts `healthRouter` (`:87`), `wsHub` (`:88-92`), and `buildRouter` with publish/wait callbacks bound to the WS hub (`:93-102`). Listens on `PORT || 8080`, attaches `wsHub.handleUpgrade` to the HTTP server `upgrade` event (`:104-111`).

**Findings:**
- **P1 bug:** CORS origin resolver returns first allowlist entry as fallback — should return empty (`index.ts:43-47`).
- **P1:** No graceful shutdown. SIGTERM kills the process mid-request, mid-WS, mid-waiter. Need a `server.close()` + `wsHub.close()` + `rateLimiter.close()` chain on signals.
- **P1:** No `process.on('uncaughtException', ...)` or `unhandledRejection` handler. Crashes are silent.
- **P2:** `1mb` body limit hardcoded — should be env-tunable for tenants pushing larger payloads.

### [src/routes.ts](../../cloudmorph-mcp/src/routes.ts) (583 LoC)

The router and JSON-RPC dispatcher. **The whole file is one `buildRouter` factory.** Walking it:

- `:34-92` — declares 3 hard-coded MCP tools as plain JSON: `cloudmorph_request`, `cloudmorph_request_status`, `cloudmorph_job_status`. Each has an `inputSchema` (`additionalProperties: false`, modest validation: `action: string`, `targets: string[]`, `payload: object`, `waitSeconds: 0..55`).
- `:94-108` — `jsonRpcResult` and `jsonRpcError` helpers. **Hand-rolled JSON-RPC 2.0**, not `@modelcontextprotocol/sdk`.
- `:110-179` — `forwardToControlCenterRequest` and `forwardToControlCenter` — the proxy core. Plain `fetch()` to `${CONTROL_CENTER_API_URL}${path}` with bearer token. JSON-parses the response if possible, falls through to text. Catches network errors as 502 with `{error:"control_center_unavailable", message:...}`. **No retry, no connection pooling, no circuit breaker, no tracing span.** Every `fetch` re-resolves DNS and re-handshakes TLS to the upstream host — typically 20-100ms before a single packet of useful work flows.
- `:181-188` — `requireToken` extracts bearer or 401s.
- `:190-456` — the actual `POST /mcp` handler:
  - Rate-limits via `rateLimiter.checkRequest(hashToken(token))` (`:216-237`).
  - Validates JSON-RPC envelope (`:239-247`).
  - Handles `initialize` (returns `protocolVersion: "2024-11-05"`, `serverInfo`, empty `capabilities.tools`) (`:248-256`).
  - Handles `notifications/initialized` (204 no-content) (`:259-262`).
  - Handles `tools/list` (returns the 3 hard-coded tools) (`:264-267`).
  - Handles `tools/call` (`:269-453`):
    - Routes by `name` to one of three branches.
    - For `cloudmorph_request`: extracts `action`, `targets`, `payload`, computes `waitSeconds` (capped at `MCP_WAIT_MAX_SECONDS`), proxies to `POST /controlcenter/mcp/requests`. **Then if `waitTimeout > 0` and the upstream returned a non-blocked decision**, awaits `options.waitForEvent({requestId, jobId, requireTerminal: true}, waitTimeout)` from the WS hub. **Then if no output is in the response yet**, does an *additional* `GET /controlcenter/mcp/requests/${requestId}` to grab the latest status and merges it in. So a single `tools/call cloudmorph_request{wait:true}` can trigger up to **three sequential round trips to upstream + WS coordination**. Latency budget for this is fundamentally hostile.
    - For `cloudmorph_request_status`: simple GET proxy.
    - For `cloudmorph_job_status`: simple GET proxy.
    - Wraps response in MCP `content: [{type:"text", text: JSON.stringify(payload)}], isError: !ok` shape.
  - Returns `method_not_found` for anything else (`:455`).
- `:458-510` — REST shim: `POST /controlcenter/mcp/requests` proxies through with a tiny input validator (action length ≤200, blocks `;&|backtick$`). The blocklist is **purely lexical** — does not block path traversal, parameterized injection, or anything beyond the listed five chars. Defense-in-depth at best, not a security boundary.
- `:512-542` — REST shims for `GET /controlcenter/mcp/requests/:id`, `GET /controlcenter/mcp/jobs/:id`.
- `:544-562` — `POST /controlcenter/mcp/requests/:requestId/cancel`.
- `:564-580` — `POST /mcp/events` — inbound webhook from upstream. Auth is a shared bearer secret (`MCP_EVENT_SECRET`). Calls `options.publishEvent(payload)` to push the event into the WS hub.

**Findings:**
- **P0:** Zero local evaluation. Every decision is a 1-3 round-trip remote call. Ships ~50-300ms p99 today; firewall target is p99 < 50ms cold / < 10ms cached.
- **P0:** Zero policy concept. No bundle, no rules, no decision objects. The word "policy" appears once and only in a tool description string.
- **P0:** Zero intent concept. No `cloudmorph_declare_intent`. No mismatch detection.
- **P1:** Hand-rolled JSON-RPC = spec-drift risk. Every minor MCP spec update is a manual port. Migrate to `@modelcontextprotocol/sdk`.
- **P1:** Three of three tool definitions live as inline literals. Refactor to a tool registry (`tools/registry.ts`) so adding `cloudmorph_declare_intent` etc. is a one-file change.
- **P1:** No HTTP keep-alive / agent reuse on `fetch`. Default Node fetch creates a new connection each time. Fix: shared `Agent` with `keepAlive: true`.
- **P1:** Token-handling antipattern: passing the full bearer string to `hashToken` then storing the *first 8 chars* in the hash output (`ratelimit.ts:222`) leaks token *prefix* into in-memory map keys and (via cleanup logs) potentially elsewhere.
- **P1:** Input validation in `:458-480` is so narrow it's basically theater — drop it or make it real.
- **P2:** The `waitForEvent`-then-fallback-poll pattern in `:325-403` is hard to reason about. Rewrite as one explicit state machine with named states.

### [src/ws.ts](../../cloudmorph-mcp/src/ws.ts) (302 LoC)

`createWsHub` factory returning `{handleUpgrade, publish, close, waitForEvent}`. Internals:

- A `Set<ClientState>` of connected clients.
- Two `Map<string, Set<Waiter>>` — one keyed by `requestId`, one by `jobId`.
- A 30s heartbeat `setInterval` that pings all clients and terminates non-responsive ones (`ws.ts:120-130`).
- `extractToken(req)` — bearer header **OR** query string `?token=...` fallback (`:47-64`). Query-string fallback is a leak vector; tokens land in access logs, proxy logs, and `referer` headers. **P1.**
- `validateToken(controlCenterUrl, token)` — does a GET to `/controlcenter/mcp/requests?limit=1` with the bearer; treats `resp.ok` as valid (`:66-79`). Three problems:
  1. Any 4xx other than 401/403 will mark the token invalid (e.g., 404 if the route changed).
  2. No caching — every WS connection costs an upstream round trip.
  3. Authn-by-side-effect of an authz-protected list endpoint is brittle; needs a dedicated `/controlcenter/auth/verify` endpoint upstream.
- On `connection`: if `validateTokens` is true, calls `validateToken`; else accepts unconditionally. Accepts `ping`, `subscribe`, `unsubscribe` JSON messages; emits `pong`, `subscribed`, `unsubscribed`.
- `publish(event)` (`:212-251`): finds clients that have subscribed to the event's `requestId` or `jobId`, sends them the event payload. **Then** scans the two waiter maps and resolves any waiter whose criteria match. Uses `isTerminalEvent` to gate the resolution if `requireTerminal` is set. Heuristic-based terminal detection: status in `{completed, failed, cancelled, canceled, blocked, block}` for `job.status`; status in same set OR `decision === "block"` for `request.status` (`:96-104`).
- `waitForEvent(criteria, timeoutMs)` (`:253-294`): registers a waiter under `requestId` and/or `jobId` keys, returns a Promise that resolves to the matching event or `null` on timeout.

**Findings:**
- **P1:** Query-string token fallback (`:55-62`). Remove or gate behind a dev flag.
- **P1:** No cap on number of waiters (`requestWaiters`, `jobWaiters`) — a malicious or buggy client could register thousands.
- **P1:** No cap on subscriptions per client. A client could subscribe to all-the-things and exhaust memory.
- **P1:** WS server is per-process; horizontal scaling broken (no pub/sub layer). For two MCP replicas, an event published to instance A is invisible to a waiter on instance B. Document this as a single-instance constraint until Redis pub/sub lands.
- **P1:** `validateToken` does an unauthenticated upstream call masquerading as a list — fragile. Need a dedicated upstream endpoint.
- **P2:** Heartbeat is a fixed 30s; should be configurable.
- **P2:** No backpressure; `socket.send(payload)` returns boolean indicating if buffer was full but is ignored.

### [src/ratelimit.ts](../../cloudmorph-mcp/src/ratelimit.ts) (223 LoC)

In-memory token-bucket per token. Three counters: `dailyCount` (resets at midnight UTC), `minuteCount` (resets at minute boundary), `concurrentJobs`. Periodic cleanup (`cleanupInterval`, 5 min) deletes buckets older than 1h with no in-flight jobs (`:65-73`).

`hashToken` (`:214-223`) is a djb2-style hash that concatenates `<first-8-chars-of-token>:<base36-djb2-hash>`. **The first-8-chars prefix is the leaky bit** — if you can read the in-process hash map, you have a fingerprint that survives token rotation length-prefix matching. For an MVP, fine. Pre-prod, replace with `crypto.createHash('sha256').update(token).digest('hex').slice(0,16)`.

**Findings:**
- **P0 (for scale):** In-memory only. Replace with Redis token-bucket before a second instance is provisioned.
- **P1:** `hashToken` exposes token prefix. Use SHA-256.
- **P1:** Comment at `:9` says "For production, swap with DynamoDB atomic counters" — DDB IA1 latency is 5-15ms which torches the latency budget. Redis with `INCR + EXPIRE` is the right shape.
- **P2:** Cleanup interval is fixed 5 min; configurable.

### [src/auth.ts](../../cloudmorph-mcp/src/auth.ts) (13 LoC)

`getBearerToken(req)` — exactly that. No validation, no claims parsing, no tenant lookup, no rotation hooks. **All multi-tenant logic is upstream.** That's the architectural problem, condensed to 13 lines.

**Findings:**
- **P0:** Need a `tokenResolver` abstraction that maps `token → {tenantId, scopes, planLimits, policyBundleId}` with a short-lived cache (30s TTL). Eliminates the 1-3 upstream round trips per request.
- **P1:** No support for JWT-format tokens. If we want OIDC for human approvers, this needs to grow.

### [src/health.ts](../../cloudmorph-mcp/src/health.ts) (9 LoC)

`GET /health` returns `{status: "ok"}`. Doesn't check upstream, doesn't check WS hub, doesn't expose policy bundle version, doesn't include build SHA, doesn't include tenant count.

**Findings:**
- **P1:** Need richer health: `{status, version, sha, policyBundleId, policyBundleVersion, upstreamReachable, wsClients, uptimeSeconds}`. Liveness vs readiness split (`/healthz/live` vs `/healthz/ready`).

### [package.json](../../cloudmorph-mcp/package.json)

```json
"scripts": {
  "build": "tsc -p tsconfig.json",
  "start": "node dist/index.js",
  "lint": "tsc --noEmit",
  "test": "echo \"No tests yet\" && exit 0"
}
"dependencies": { "express": "^4.19.2", "ws": "^8.17.0" }
"devDependencies": { "@types/express", "@types/ws", "typescript": "^5.4.5" }
"engines": { "node": ">=18" }
```

**Findings:**
- **P0:** `npm test` is a no-op. CI green = no signal.
- **P0:** No `@modelcontextprotocol/sdk`.
- **P0:** No `@open-policy-agent/opa-wasm` (or equivalent).
- **P1:** No test runner (`vitest`).
- **P1:** No structured logger (`pino`).
- **P1:** No metrics lib (`prom-client`).
- **P1:** No tracing (`@opentelemetry/sdk-node`).
- **P1:** Express 4 is fine; if migrating, move to fastify for ~3x throughput in pure JSON workloads — but not in MVP scope.

### [Dockerfile](../../cloudmorph-mcp/Dockerfile)

Three-stage `node:18-alpine`. `npm install` (not `npm ci`!) in deps stage. `npm run build` in build stage. Runtime stage copies node_modules + dist, runs as **root** (no `USER node`), no `HEALTHCHECK`, no `LABEL`s, no signal forwarding (`CMD ["node", ...]` is fine for SIGTERM since node passes it through).

**Findings:**
- **P1:** `npm install` should be `npm ci` for reproducibility (lockfile is now present in worktree).
- **P1:** Runs as root. Add `USER node`.
- **P1:** No `HEALTHCHECK` directive.
- **P1:** No multi-arch build instructions in CI (Apple Silicon devs + ARM Graviton hosts both deserve native).
- **P2:** Consider distroless final stage for smaller surface (`gcr.io/distroless/nodejs18-debian12`).
- **P2:** Add OCI labels (source, revision, version).

### [.env.example](../../cloudmorph-mcp/.env.example) (88 lines)

Well-documented env vars. Notably `MCP_ALLOWED_ORIGINS=*` as the default — should be empty to force explicit configuration. `MCP_WS_VALIDATE_TOKENS=true` default is correct. `MCP_EVENT_SECRET` empty default is "not recommended for production" per the comment but accepted — should be **mandatory** in prod (fail-fast on empty secret if `NODE_ENV=production`).

### [.github/workflows/ci.yml](../../cloudmorph-mcp/.github/workflows/ci.yml) (34 lines)

Node 18, `npm ci`, lint (`tsc --noEmit`), build, test (`echo`). No matrix. No coverage upload. No security scan. No Docker build. No release on tag.

---

## 1.2 Tool surface gap

The product needs a wide, opinionated tool set. Today: 3 of 12. Score:

| Tool | Status | Effort | Severity | Block | Purpose |
|---|---|---:|---|---|---|
| `cloudmorph_request` | ✅ exists | — | — | — | Submit policy request for an action |
| `cloudmorph_request_status` | ✅ exists | — | — | — | Poll request by id |
| `cloudmorph_job_status` | ✅ exists | — | — | — | Poll job by id |
| `cloudmorph_declare_intent` | ❌ missing | 6h | **P0** | D | Declare intent before any tool call (THE differentiator) |
| `cloudmorph_revoke_intent` | ❌ missing | 1h | P1 | D | Revoke an intent mid-session |
| `cloudmorph_explain_decision` | ❌ missing | 4h | **P0** | D | Return matched rules + eval trace (debugging + compliance) |
| `cloudmorph_proxy` | ❌ missing | 12h | **P0** | D | Transparent proxy for ANY downstream MCP server (THE killer tool) |
| `cloudmorph_session_start` | ❌ missing | 3h | P1 | D | Begin a session (binds intents + decisions) |
| `cloudmorph_session_end` | ❌ missing | 1h | P1 | D | End a session, finalize audit |
| `cloudmorph_list_sessions` | ❌ missing | 2h | P2 | D | Operator/observability tool |
| `cloudmorph_list_policies` | ❌ missing | 2h | P1 | D | Introspection — what bundle is loaded, what rules apply |
| `cloudmorph_approve` | ❌ missing | 4h | P1 | D | Human approver (or approver agent) decides |
| `cloudmorph_deny` | ❌ missing | 1h | P1 | D | Same, deny path |
| `cloudmorph_replay` | ❌ missing | 4h | P2 | E | Re-evaluate a past requestId against new bundle (dry-run) |
| `cloudmorph_redact_preview` | ❌ missing | 3h | P2 | E | Show what a `mutate`/`redact` rule would change before commit |

**14-day MVP scope:** declare_intent, explain_decision, proxy, session_start/end, list_policies, approve/deny. Replay and redact_preview slip to post-MVP. revoke_intent and list_sessions are 1-2h adds — do them while you're touching the registry.

### 1.2a `cloudmorph_proxy` — the killer tool, in depth

This is the play that moves Control Centre from "another agent gateway" to "the firewall the entire MCP ecosystem uses". Sketch:

```typescript
{
  name: "cloudmorph_proxy",
  description: "Wrap a downstream MCP server with policy enforcement. Every tools/call through this tool is evaluated by Control Centre.",
  inputSchema: {
    type: "object",
    additionalProperties: false,
    required: ["downstreamUrl", "downstreamAction", "downstreamArguments"],
    properties: {
      downstreamUrl: { type: "string", format: "uri" },
      downstreamTransport: { enum: ["http", "stdio", "sse", "ws"], default: "http" },
      downstreamAction: { type: "string", description: "MCP tool name on the downstream server" },
      downstreamArguments: { type: "object", additionalProperties: true },
      sessionId: { type: "string" },
      intentId: { type: "string" }
    }
  }
}
```

Server-side flow:
1. Resolve `(tenantId, sessionId, intentId)` via token + arg.
2. Call policy engine with synthesized `ToolCallRequest = {action: "mcp.proxy.<downstreamUrl>.<downstreamAction>", payload: downstreamArguments, intentId, sessionId, runtimeContext}`.
3. If decision is `deny` → return MCP `tools/call` result with `isError: true` and the deny reason.
4. If decision is `mutate` → swap in mutated arguments.
5. If decision is `approve` → register an approval-required job, return `{status: "pending_approval", approvalId}` to the caller (or block-and-wait if `wait=true`).
6. If decision is `allow` → forward `tools/call` to downstream MCP server with `downstreamArguments`.
7. On response: if `redact` rule applies, filter response. Emit `AuditEvent` with the full chain.

**Why this matters:** every other MCP server is a single-vendor tool. `cloudmorph_proxy` is the one MCP tool that adds value to *every other* MCP tool. That's distribution.

**Effort:** 12h to v1 (HTTP transport only). Add stdio (8h) and SSE (4h) post-MVP. Arrive on a downstream-MCP capability discovery path (`tools/list` proxying with optional filtering by policy) — 4h add-on.

---

## 1.3 Transport gap

Today: HTTP POST `/mcp` + WebSocket `/mcp/ws`. Stdio missing, SSE missing.

| Transport | Status | Why we need it | Effort |
|---|---|---|---:|
| HTTP POST | ✅ | Cursor, hosted dashboards | — |
| WebSocket | ✅ | Streaming events, long-poll-style waits | — |
| **stdio** | ❌ | Local Cursor / Claude Desktop / Codex / self-hosted dev — the *primary* MCP transport in spec | 8h |
| **SSE** | ❌ | Some clients prefer it; stable streaming over HTTP/2 | 4h |

Migration path: introduce `@modelcontextprotocol/sdk`'s `Server` abstraction, register the same tool registry against `StdioServerTransport`, `SSEServerTransport`, and a HTTP/WS adapter for backwards compatibility. **Block D — 14h total** for the transport unification + SDK migration.

---

## 1.4 Auth & multi-tenancy gap

Today: bearer extraction (13 LoC). All tenant resolution upstream. **Per-request, the MCP server does not know which tenant it is serving.** That's why every request has to round-trip — there's no local `(token, tenant)` index to evaluate against.

Required additions (severity in parens):

- (**P0**) `TokenResolver` interface — `resolve(token) → {tenantId, sessionId?, scopes, planLimits, policyBundleId, expiresAt}` with TTL cache (default 30s, configurable). Backed by upstream call on miss; in-process LRU on hit. Effort 6h.
- (**P1**) JWT support: parse `Authorization: Bearer <jwt>` claims locally for the resolver fast path. Effort 4h.
- (**P1**) mTLS option for self-hosted: read peer cert, map cert subject to tenant. Effort 6h, post-MVP.
- (**P1**) OIDC for human approvers (Google / GitHub / Okta) on a separate `/approve/oidc/callback` route — the approver identity ≠ the agent token. Effort 8h, post-MVP.
- (**P0**) Per-tenant policy namespace — bundle keyed on `tenantId`, decision cache keyed on `(tenantId, ...)`, audit log namespaced. Effort wrapped into Block E.
- (**P1**) API key rotation flow — admin endpoint to mint/revoke. Effort 6h, post-MVP.

---

## 1.5 Policy engine

**Does not exist.** This is the largest single piece of work in the 14-day plan. Detailed design lives in [../policy/05_policy_engine_design.md](../policy/05_policy_engine_design.md). MCP-server-relevant integration points:

- New directory: `cloudmorph-mcp/src/policy/`
  - `engine.ts` — wraps `@open-policy-agent/opa-wasm`, exposes `evaluate(input: PolicyInput) → PolicyDecision`
  - `bundle.ts` — load+verify bundle from filesystem path or signed URL, hot-reload, blue-green swap
  - `cache.ts` — LRU keyed on `hash(bundleId, tenantId, action, intentId, sha256(payload))`, TTL 10s
  - `types.ts` — re-export contract types (`PolicyDecision`, `RuntimeContext`, etc.)
- Extend `routes.ts → tools/call cloudmorph_request`:
  1. Resolve `(tenantId, scopes, ...)` via `TokenResolver`.
  2. Build `PolicyInput = {action, payload, targets, intent: ..., runtimeContext: ...}`.
  3. `decision = engine.evaluate(input)`.
  4. Branch on decision outcome (allow/deny/approve/mutate/redact/throttle/audit_only).
  5. For `allow` and `mutate` — proceed to forward (or to executor lifecycle); for `deny`/`approve` — return immediately; for `redact` — proceed but filter response post-execution.
- Bundle distribution: env vars `POLICY_BUNDLE_PATH=/etc/cloudmorph/bundles/active.tar.gz` (file mode) or `POLICY_BUNDLE_URL=https://bundles.cloudmorph.io/...&hmac=...` (pull mode). HMAC verification mandatory.
- Hot reload: filesystem watcher (`fs.watch`) for path mode; periodic poll (default 60s) for URL mode. Blue-green swap — maintain two engines, swap atomic ref pointer; in-flight evaluations finish on the old engine.

**Effort:** 24h for v1 (basic OPA WASM, single bundle, in-process cache, allow/deny/mutate outcomes). +12h for the rest of the rule taxonomy. Block E.

---

## 1.6 Intent capture

Also does not exist. Detailed design in [../intent/06_intent_system_design.md](../intent/06_intent_system_design.md). MCP-server integration:

- New tool: `cloudmorph_declare_intent` (see §1.2).
- Session storage: in-process `Map<sessionId, Session>` with TTL (default 1h sliding). Add Redis backend post-MVP for multi-instance.
- Every `cloudmorph_request` (and `cloudmorph_proxy`) accepts an optional `intentId` arg; if present, joined to the session's intent during policy eval; if absent, decision falls through to default rules (typically more restrictive).
- Mismatch detection: see policy doc §2.4 for the lexical→semantic→LLM-judge cascade. The MCP server invokes a `intentMatcher.match(intent, action) → MatchScore` helper on every request and includes the score in `PolicyInput`.

**Effort:** 16h Block E (intent declaration + lexical matching). Semantic matching (embeddings) is +8h post-MVP. LLM judge stub in MVP (returns "ambiguous" → defers to bundle); real LLM judge call post-MVP.

---

## 1.7 Audit log

Today: `console.log` JSON lines via `logEvent` in `routes.ts:197-201`. Three calls (`mcp.request.submit`, `mcp.request.event`, `mcp.request.status`) plus the global `mcp.request` access log in `index.ts`. No durability, no tamper-evidence, no per-tenant retention, no sink pluggability.

Required:

- New directory `cloudmorph-mcp/src/audit/`
  - `emitter.ts` — `AuditEmitter.emit(event: AuditEvent) → Promise<void>`
  - `chain.ts` — maintains the running `prevEventHash` per tenant, computes `eventHash = sha256(prev + canonicalJson(event))`
  - `sinks/stdout.ts`, `sinks/s3.ts`, `sinks/buffered.ts` (disk-backed bounded queue for sink failures)
  - `signing.ts` — optional Ed25519 signature per event for the highest-assurance tenants
- Per-tenant retention policy: `auditRetention: 7d|30d|365d|forever` from the resolver
- Customer-owned sinks: S3 bucket in customer account (cross-account role) — `sinks/s3-customer-owned.ts`. Critical for compliance buyers who insist audit data live in their account.
- Verification CLI shipped separately: `npx @cloudmorph/audit-verify --bundle s3://customer-bucket/audit/...` re-walks the chain and proves no gap.

**Effort:** 18h MVP (chain + stdout + S3 sinks + verification CLI). Customer-owned sinks +8h post-MVP. Kafka/ClickHouse +12h post-MVP. Block D.

---

## 1.8 Latency budget

Target (from product framing):
- p99 < 10ms for **cached** policy decisions
- p99 < 50ms for **cold** policy decisions (excluding network to downstream MCP)

Current p99 (estimated, no benchmarks exist):
- `tools/call cloudmorph_request{wait:false}` — ~50-150ms (one upstream round trip).
- `tools/call cloudmorph_request{wait:true}` — anywhere from 200ms to `MCP_WAIT_MAX_SECONDS` (55s) depending on whether the upstream fires the WS event in time. If the WS fires but the cached output is missing, **another** upstream GET is added.

Single-instance synthetic test plan (Block H bench):
- `npx autocannon -c 100 -d 30 http://localhost:8080/mcp -m POST -H "Authorization: Bearer cm_test" -b @initialize.json`
- `npx autocannon -c 100 -d 30 http://localhost:8080/mcp -m POST -H "Authorization: Bearer cm_test" -b @tools-call-allow.json` (with stub policy engine returning `allow` immediately)
- Same at `-c 1000` to validate burst behavior.

**Required to hit the budget:**
1. **Local policy eval** (§1.5) — removes the upstream round trip from the hot path.
2. **Decision cache** (§1.5) — 10s TTL on `hash(...)`. Real-world cache hit rate for hot agent loops (same tool, same payload shape, same intent): expected 60-90%.
3. **TokenResolver cache** (§1.4) — 30s TTL.
4. **HTTP keep-alive** for the upstream calls that *do* still happen (executor lifecycle, audit batch flush) — `import { Agent } from 'undici'; const agent = new Agent({ keepAliveTimeout: 30_000 });`.
5. **Optional HTTP/2** to upstream (if upstream supports it).
6. **Bench harness** in `cloudmorph-mcp/bench/` — autocannon scripts, JSON output, CI gate that fails on regression > 20%.

**Effort:** 6h for keep-alive + bench harness. Cache effort is in §1.5. Block D + Block H.

---

## 1.9 Operational gaps

| Gap | Severity | Effort | Block |
|---|---|---:|---|
| In-memory rate limiter — replace with Redis | P1 | 8h | H |
| Graceful shutdown (drain WS + in-flight HTTP) | P1 | 4h | H |
| `/metrics` Prometheus endpoint | P1 | 4h | H |
| OpenTelemetry tracing (one span per decision) | P1 | 6h | H |
| `/healthz/live` + `/healthz/ready` split | P1 | 2h | H |
| Real test suite (Vitest) replacing `echo` | **P0** | 12h | D-I (continuous) |
| Structured logging (`pino` or own JSON) | P2 | 3h | D |
| Sentry / error tracker integration | P2 | 2h | I |
| Multi-arch Docker build in CI | P1 | 3h | H |

---

## 1.10 Severity table — combined

P0 = MVP blocker; P1 = MVP blocker (stretch); P2 = post-MVP.

| Gap | Severity | Effort | Block |
|---|---|---:|---|
| No local policy eval (every req → upstream) | P0 | (in §1.5) | E |
| No `cloudmorph_declare_intent` tool | P0 | 6h | D |
| No `cloudmorph_explain_decision` tool | P0 | 4h | D |
| No `cloudmorph_proxy` tool (killer feature) | P0 | 12h | D |
| No policy engine (OPA WASM) | P0 | 24h | E |
| No intent capture / mismatch detection | P0 | 16h | E |
| Audit log = console.log only | P0 | 18h | D |
| `npm test` is no-op echo | P0 | 12h | D-I |
| Hand-rolled JSON-RPC (vs MCP SDK) | P1 | 8h | D |
| Stdio transport missing | P1 | 8h | D |
| Local TokenResolver missing | P0 | 6h | D |
| Per-tenant policy namespace missing | P0 | (in policy) | E |
| In-memory rate limiter | P1 | 8h | H |
| Graceful shutdown | P1 | 4h | H |
| `/metrics` Prometheus | P1 | 4h | H |
| OTel tracing | P1 | 6h | H |
| Cors fallback origin bug (`index.ts:43`) | P1 | 1h | A |
| WS query-string token leak (`ws.ts:55`) | P1 | 1h | A |
| `hashToken` exposes prefix | P1 | 2h | A |
| `Dockerfile` runs as root | P1 | 1h | H |
| `Dockerfile` uses `npm install` not `npm ci` | P1 | 1h | A |
| Health endpoint too thin | P1 | 2h | H |
| `validateToken` brittle (uses list endpoint) | P1 | 4h | (post-MVP) |
| SSE transport missing | P2 | 4h | (post-MVP) |
| `cloudmorph_replay` missing | P2 | 4h | (post-MVP) |
| `cloudmorph_redact_preview` missing | P2 | 3h | (post-MVP) |
| mTLS option | P2 | 6h | (post-MVP) |
| OIDC for approvers | P2 | 8h | (post-MVP) |

**Block totals (rough):**
- Block A (truth & foundation, 1-2 days): ~6h MCP-touching items
- Block D (MCP server core, 4 days): ~80h of MCP work
- Block E (policy engine depth, 2 days): ~36h
- Block H (hardening + ops, 1 day): ~30h

That's ~152h of MCP work. With 1 engineer at 8 productive hours/day, that is 19 person-days — **realistic for the 14-day MVP only because not every item is on the critical path.** The critical path is: TokenResolver → tool registry → declare_intent → policy engine v1 → audit chain → cloudmorph_proxy. Everything else can ship as stubs or slip to days 12-14 hardening.

---

## 1.11 What stays, what goes

**Keeps (with light touch):**
- `index.ts` express bootstrap (good shape)
- `ratelimit.ts` shape (replace backend, keep API)
- `ws.ts` waiter pattern (still relevant for long-poll on approvals)
- `health.ts` (extend, don't replace)
- The 3 existing tools (`cloudmorph_request`, `cloudmorph_request_status`, `cloudmorph_job_status`) — wire them through the new policy kernel; keep upstream proxy as the executor-side path.

**Refactors (substantial rewrites):**
- `routes.ts` 583 LoC monolith → `router.ts` (express mounts) + `tools/registry.ts` + `tools/<name>.ts` per tool + `policy/engine.ts` + `audit/emitter.ts` + `auth/resolver.ts`. Target: no file > 200 LoC after Block D.
- `auth.ts` 13 LoC → `auth/bearer.ts` (existing) + `auth/resolver.ts` (new) + `auth/jwt.ts` (new).

**Adds:**
- `policy/`, `intent/`, `audit/`, `tools/`, `bench/`, `transports/` (stdio + SSE adapters), `tests/` (real Vitest suite).

**Removes:**
- WS query-string token fallback (`ws.ts:55-62`).
- The CORS fallback-to-first-allowlist behavior in `index.ts:46`.
- Lexical input validator in `routes.ts:464-480` (replace with proper validation via contract enforcement).

---

## 1.12 Source links

All code references in this audit:
- [src/index.ts](../../cloudmorph-mcp/src/index.ts)
- [src/routes.ts](../../cloudmorph-mcp/src/routes.ts)
- [src/ws.ts](../../cloudmorph-mcp/src/ws.ts)
- [src/ratelimit.ts](../../cloudmorph-mcp/src/ratelimit.ts)
- [src/auth.ts](../../cloudmorph-mcp/src/auth.ts)
- [src/health.ts](../../cloudmorph-mcp/src/health.ts)
- [package.json](../../cloudmorph-mcp/package.json)
- [Dockerfile](../../cloudmorph-mcp/Dockerfile)
- [.env.example](../../cloudmorph-mcp/.env.example)
- [.github/workflows/ci.yml](../../cloudmorph-mcp/.github/workflows/ci.yml)

Implementation order in [BUILD_PLAN.md](../BUILD_PLAN.md): Block A → C → D → E → H.
