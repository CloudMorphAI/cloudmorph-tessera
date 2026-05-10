# Tessera Roadmap

This document lists features that are **not in v0.1** and explains why. The goal is to ship a small, coherent, verifiable firewall — not to solve every possible deployment topology on day one. Features deferred here are not forgotten; they have a clear path and rationale.

Where a version is listed the feature is on the roadmap. Where it says "not planned" the feature is intentionally out of scope for the OSS distribution; it may exist in Tessera Cloud.

---

## Deferred features

### 1. OAuth 2.1 PKCE — v0.2

Bearer tokens are sufficient for v0.1; OAuth 2.1 PKCE is deferred because it is required for SaaS deployments (where users authenticate with an identity provider) but adds no safety value in the single-tenant OSS model.

In v0.1 Tessera authenticates callers with long-lived bearer tokens. This is appropriate for the primary v0.1 use case: an operator who owns the deployment, mounts a `tokens.yaml` file they control, and rotates tokens on their own schedule. OAuth 2.1 PKCE is the correct mechanism when the identity issuing the token is a third-party identity provider — a browser-based agent UI, a SaaS control plane, or a CI system that exchanges an OIDC token for a scoped Tessera credential. None of those scenarios are in the OSS deployment model. Implementing PKCE in v0.1 would add an `authlib` or `python-jose` dependency, introduce an authorization server configuration surface, and require redirect-URI handling — all for a feature the OSS deployer cannot use without running additional infrastructure. When v0.2 ships PKCE support it will be added behind the `Authenticator` Protocol, so the existing bearer-token path is unaffected and all current configurations continue to work.

---

### 2. Rego escape hatch — v0.2

Inline Rego policies alongside YAML are deferred; they add an OPA dependency and are gated on a concrete customer request, because the YAML condition catalog covers the wedge use cases.

The v0.1 policy engine is pure Python. It evaluates YAML rules, resolves conditions from a catalog, and applies them in a single sorted pass — no external runtime, no subprocess. Adding a Rego escape hatch means either vendoring the OPA Go binary (increases the Docker image from ~150 MB to ~250+ MB and introduces a cross-compiled binary with its own CVE surface) or running `opa eval` via subprocess (adds process-spawn latency to the hot path). Neither trade-off is justified until a specific customer brings a policy that cannot be expressed in the condition catalog. The catalog covers argument comparisons, regex patterns, verb classification, intent matching, region restrictions, time-of-day windows, and arbitrary `_meta` field equality — the realistic set of firewall rules. If a customer brings a use case that genuinely requires Rego, the `PolicyLoader` Protocol makes it possible to add a `RegoEvaluatedPolicy` subtype without breaking existing YAML policies. That work belongs in v0.2 and should be driven by a real issue with a real policy body.

---

### 3. Multi-tenant in OSS — not planned

Multi-tenant policy isolation (separate policy sets, separate audit chains, separate credentials per organizational tenant) is a Cloud feature and is not planned for the OSS distribution.

The OSS model is single-deployment-single-policy-set. One `tessera.yaml`, one `policies/` directory, one audit database. The multi-token feature gives per-token scope, which produces isolated audit chain streams — that is the extent of isolation in OSS. Full multi-tenancy (tenant-scoped policy loading, tenant-scoped `PolicyLoader` resolution, tenant-specific rate limits, per-tenant credential vaulting) is architectural scope that belongs in a hosted product where the operator is managing infrastructure on behalf of customers. Building it into OSS would make the configuration surface significantly more complex for the common case — a single team deploying a single firewall for their own agents — without adding safety value for that deployer. Organizations that need multi-tenant isolation should use Tessera Cloud, which wraps the same Protocols with a `DynamoDBPolicyLoader` and tenant-scoped `AuthContext` resolution.

---

### 4. ML intent inference — not planned

Automatic inference of agent intent from tool call content using a language model is intentionally out of scope for a deterministic firewall.

Tessera is a deterministic policy engine. Given the same tool call input and policy set, it produces the same decision every time. That property is the core safety guarantee: operators can reason about what will be blocked, write fixture tests that verify decisions, and trust that a passing `tessera policy test` run means the firewall will behave the same in production. Adding an LLM judge to infer intent from arguments destroys that property — the same argument payload may produce different intent classifications across model versions, context windows, or rate-limit-triggered fallbacks. It also adds per-request latency (a round-trip to an inference endpoint), a new failure mode (inference timeout or error), a new cost center, and a new privacy surface (argument payloads sent to a third-party API). Tessera's answer to intent is structural: agents that declare intent in `_meta.tessera_intent` get intent-aware policy evaluation; agents that do not declare intent are handled by policies that match on tool name and arguments alone. That separation is explicit, operator-controlled, and deterministic.

---

### 5. Native rate limiting — v0.2

Per-token and global request rate limiting are not built into Tessera v0.1; the workaround is to deploy behind nginx, Caddy, or Cloudflare, and native rate limiting is on the v0.2 roadmap.

v0.1 does not bound how many requests a single bearer token or the proxy as a whole can receive per second. A misbehaving or compromised agent can hammer the proxy, exhaust upstream MCP server concurrency, and run up API costs. This is a real operational risk and operators should mitigate it before exposing Tessera to untrusted callers. The mitigation in v0.1 is straightforward: put Tessera behind a reverse proxy (nginx `limit_req_zone`, Caddy `rate_limit`, Cloudflare rate limiting rules) which enforces an inbound rate cap before requests reach the FastAPI process. The README quickstart includes an explicit callout: "If exposing Tessera beyond localhost, put it behind nginx or Caddy with a rate-limit rule." Native rate limiting is deferred rather than abandoned because the `Authenticator` Protocol already provides the per-token identity (`AuthContext.scope`) that a built-in rate limiter would key on, and the async proxy architecture (FastAPI + `asyncio`) makes an in-process token bucket straightforward to add. It was cut from v0.1 to keep the initial scope focused on the core policy evaluation path and avoid prematurely coupling the proxy to a specific storage backend for rate counters.

---

### 6. Shadow MCP discovery via MDM — v0.2+

Automatic discovery of MCP servers in use across an organization through MDM or endpoint management tooling is out of OSS scope and is tracked for v0.2+.

Shadow MCP discovery addresses the organizational problem of finding out which MCP servers agents are actually connecting to — not just the ones IT has approved. Solving it requires integration with endpoint management platforms (Jamf, Intune, CrowdStrike Falcon, etc.), access to agent configuration files across a fleet of developer machines, or a phoning-home mechanism in the client tooling. None of these integration surfaces exist in a self-hosted OSS firewall. The OSS model assumes the operator knows which MCP servers they are proxying — that is why `upstreams[]` is an explicit list in `tessera.yaml`. Organizational tool inventory and shadow-IT discovery are fleet management concerns that require either a SaaS control plane (where all deployments report home) or direct MDM integration (where the MDM agent reads agent config files). Both require infrastructure that OSS Tessera does not provide and should not attempt to provide.

---

### 7. Postgres sink — v0.2

A native PostgreSQL audit sink is not included in v0.1; SQLite covers the expected write volume, and the `AuditSink` Protocol is designed so that a Postgres implementation can be added without breaking changes.

v0.1 ships one audit sink: SQLite with WAL mode. For a single-deployment firewall handling up to a few hundred requests per second, SQLite in WAL mode is durable, fast, and operationally simple — no separate database process, no connection string management, no migrations beyond the single `CREATE TABLE IF NOT EXISTS` statement. Postgres becomes relevant when the audit database needs to be accessed by multiple reader processes simultaneously (dashboards, SIEM integrations, compliance exporters), when the volume of events exceeds what SQLite handles comfortably on a network-mounted volume, or when the operator already runs Postgres and wants to consolidate. The `AuditSink` Protocol (`emit`, `close`, `head_hash`, `iter_events`) was specified with Postgres in mind — the column layout maps directly to a Postgres table, and `head_hash` is a single indexed point-read that performs equally well in both engines. Adding `tessera/audit/sinks/postgres.py` in v0.2 is a matter of implementing the four Protocol methods with `psycopg3`, adding `asyncpg` or `psycopg3` to optional dependencies, and documenting the connection string env var. No changes to the proxy, engine, or chain are needed.

---

### 8. stdio transport — v0.2

HTTP is the only supported transport in v0.1; stdio transport (used by some Claude Desktop and local agent configurations) is deferred to v0.2.

MCP supports two transports: HTTP (server-sent events or direct POST) and stdio (process stdin/stdout, used when the MCP server is launched as a child process by the client). Tessera v0.1 is an HTTP proxy — it binds a port, and the MCP client is configured to send requests to `http://localhost:8080/mcp/<upstream>`. This covers the majority of deployment scenarios: Docker, Kubernetes, and any network-accessible deployment. stdio is needed specifically when the agent runtime (Claude Desktop, some Cursor configurations) expects to launch the MCP server as a subprocess rather than connect to a running HTTP server. Supporting stdio would require Tessera to also function as a process that reads JSON-RPC from stdin and writes to stdout, with no HTTP server, while still proxying through to an upstream (which would itself need to be either HTTP or a sub-subprocess). The added complexity is not justified for v0.1, where the Docker quickstart and HTTP model cover the common cases. v0.2 will add a `tessera serve --transport stdio` mode for users who need local subprocess integration.

---

### 9. Per-policy version pinning and signed bundles — v0.2

Version-pinning individual policies to a specific hash and signing the policy bundle as a unit are deferred to v0.2 because OSS users own and control their policy directory.

In v0.1, the policy directory is a filesystem path that the operator mounts into the Tessera container. The operator is responsible for the integrity of that directory — the same way they are responsible for their `tessera.yaml` and `tokens.yaml`. There is no adversary model in v0.1 where a policy file is tampered with in transit, because the policy directory is a local mount, not a remote download. Version pinning and signed bundles become relevant in a remote-distribution scenario: a central policy repository publishes versioned, signed policy bundles, and each Tessera deployment downloads and verifies them before loading. That model is a Cloud feature (the policy repository is the control plane) or an enterprise self-hosted feature. Adding cryptographic verification of local filesystem files would impose key management overhead on every OSS user without providing a real security benefit for their threat model.

---

### 10. Inline Rego files alongside YAML — v0.2

Placing `.rego` files in the `policies/` directory alongside `.yaml` files for mixed-mode evaluation is deferred for the same reason as the Rego escape hatch: it requires the OPA dependency, and the YAML condition catalog covers the wedge use cases.

This is a more specific formulation of item 2. A user might want to drop a `deny_if_cost_exceeds.rego` file next to `cost-cap.yaml` and have Tessera evaluate both. The `FilesystemPolicyLoader` in v0.1 ignores all files that are not `*.yaml` (and not `_*.yaml`). Adding inline Rego evaluation would require the loader to detect `.rego` files, call OPA, and merge Rego decisions with YAML decisions in the engine's first-match-wins pass — adding OPA startup overhead, a new failure mode (OPA bundle load error), and per-request IPC latency. The condition catalog in v0.1 is expressive enough that no reference policy requires Rego. If a user's use case genuinely cannot be expressed in YAML, the right path is to file an issue with the policy body, which will either reveal a missing condition (add it to the catalog) or confirm Rego is truly needed (implement the escape hatch in v0.2).

---

### 11. Policy composition (chaining) — v0.2

Composing policies by referencing one policy from another, building policy chains or graphs, is deferred to v0.2 because the feature adds significant complexity to the evaluation model.

v0.1 uses a flat, sorted, first-match-wins evaluation strategy. Every `*.yaml` file in `policies/` is an independent policy. The engine walks the sorted list and returns the first matching decision. This model is simple to reason about, simple to test with fixtures, and simple to explain: "the first policy that matches wins." Policy composition — where a policy can include another, override parts of it, or chain into it — introduces a graph that can contain cycles, ambiguous precedence, and emergent behavior that is hard to trace back to a specific YAML file. The `policies/README.md` documents the workaround: use `priority` to control evaluation order, and use `none_of` and `any_of` condition combinators to express complex logic within a single policy. If a use case requires composition that cannot be expressed with priority + combinators, file an issue with the concrete policy bodies so the design can be done right in v0.2 rather than patched in incrementally.

---

## Timeline summary

| Feature | Target |
| --- | --- |
| OAuth 2.1 PKCE | v0.2 |
| Rego escape hatch | v0.2 |
| Native rate limiting | v0.2 |
| Shadow MCP discovery via MDM | v0.2+ |
| Postgres sink | v0.2 |
| stdio transport | v0.2 |
| Per-policy version pinning / signed bundles | v0.2 |
| Inline Rego files alongside YAML | v0.2 |
| Policy composition (chaining) | v0.2 |
| Multi-tenant in OSS | not planned |
| ML intent inference | not planned |

---

Want a feature here moved up? Open an issue with the use case.
