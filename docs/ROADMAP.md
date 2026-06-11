# Tessera Roadmap

This document describes the current feature state and planned work for the OSS
distribution. It is updated on each minor release.

Current stable: **v1.0.0** (released YYYY-MM-DD).
Next milestone: **v1.1.0** (planned — post-GA improvements; see plan/1.1.0/ in the workspace).

Where a version is listed the feature is on the roadmap. Where it says
"not planned" the feature is intentionally out of scope for the OSS
distribution; it may exist in Tessera Cloud.

---

## What is new in v1.0.0

- **API surface frozen** — the `tessera` CLI commands, `tessera.yaml` config schema, and
  `cloudmorph-tessera` PyPI package name are the stable public contract. Semver policy
  is now in effect (see README).
- **`Production/Stable` PyPI classifier** — Development Status bumped from Beta.
- **Durable SQLite-backed revocation store** — token revocations survive process
  restarts (persisted next to the audit DB).
- **Realm-aware price-field selection** — `fixed_monthly` reads `price_usd_per_month`;
  `per_tb_scanned` reads `price_usd_per_tb_scanned`.
- **`resources/read` + `sampling/createMessage` now policy-evaluated by default** — opt
  out with `policies.engine_eval_data_methods: false`.
- **Per-bundle sibling `.signed.json` Ed25519 verification** — mapping / blast-radius /
  combination bundles verified client-side before tarball hash check.
- **`audit verify-chain` is a deprecated alias** for `audit verify`; removed in 2.0.

## What shipped in v0.9.0 / v0.8.0

> v0.8.0 was never published to PyPI; all features below shipped together in v0.9.0.

- **Unified MCP entry point** (`POST /mcp`). Single-entry-point proxy that
  fans out `tools/list` to every configured upstream and returns a merged
  catalog. Tool names are namespaced `<upstream>__<tool>` to avoid collisions.
  `tools/call` parses the namespace and dispatches through the same
  policy+audit+forward pipeline as the per-upstream route.
- **Tool namespacing helpers** (`namespace_tool`, `parse_namespaced_tool`).
- **`install-claude-desktop` unified mode**. Matches `install-claude-code` and
  `install-cursor`: default URL is `/mcp`, default upstream_name is `tessera`,
  `--legacy-per-upstream` flag available.
- Per-upstream routes (`POST /mcp/<upstream_name>`) remain unchanged — all
  v0.7.x IDE configs continue to work.

---

## Deferred features

### 1. OAuth 2.1 PKCE — v0.9 / v1.0

Bearer tokens remain the default for self-hosted deployments. The OAuth 2.1
PKCE flow shipped in v0.7.0 behind `auth.type: jwt` and the
`tessera login` / `tessera config sync` CLI commands. The remaining gap is
the `TESSERA_OAUTH_JWKS_FALLBACK` URL (fixed in v0.7.2) and the standalone
OAuth smoke test.

---

### 2. Rego escape hatch — v0.9+

Inline Rego policies alongside YAML are deferred pending a concrete customer
request. The YAML condition catalog covers the realistic set of firewall rules.
The `PolicyLoader` Protocol accepts a `RegoEvaluatedPolicy` subtype without
breaking existing YAML policies when the time comes.

---

### 3. Multi-tenant in OSS — not planned

Multi-tenant policy isolation (separate policy sets, separate audit chains,
separate credentials per organizational tenant) is a Tessera Cloud feature.

---

### 4. ML intent inference — not planned

Automatic inference of agent intent from tool call content using a language
model is intentionally out of scope for a deterministic firewall.

---

### 5. Native rate limiting — v0.9+

DCR per-IP rate limiting shipped in v0.7.0. Per-token request rate limiting
(throttling the proxy itself) is still deferred. The mitigation in the interim:
put Tessera behind nginx / Caddy / Cloudflare with a `limit_req_zone` rule.

---

### 6. Shadow MCP discovery via MDM — v1.0+

Automatic discovery of MCP servers across a developer fleet via MDM (Jamf,
Intune, CrowdStrike) is out of OSS scope. The OSS model assumes the operator
explicitly lists upstreams in `tessera.yaml`.

---

### 7. Postgres sink — v0.9+

SQLite (WAL mode) covers the expected write volume for single-instance
deployments. A `tessera/audit/sinks/postgres.py` is straightforward to add
via the `AuditSink` Protocol when concurrency or volume requires it.

---

### 8. stdio transport — v1.0

HTTP is the only supported transport. `tessera serve --transport stdio` is
needed for agent runtimes that launch MCP servers as child processes. It is on
the v1.0 roadmap.

---

### 9. Per-policy version pinning and signed bundles — v0.9+

Per-bundle Ed25519 signing shipped for packs (`manifest_url` path) and is now
extended to mapping/blast-radius/combination bundles via the sibling
`.signed.json` (P0-9 in v0.9.0). Pinning individual policies in the `policies/`
directory to a specific hash is still deferred — OSS users control that
directory themselves.

---

### 10. Policy composition (chaining) — v1.0

The flat, sorted, first-match-wins evaluation strategy is intentional. Policy
composition via `include` / chain references adds graph complexity and
ambiguous precedence. Use `priority` + `none_of` / `any_of` combinators for
complex logic within a single policy.

---

## Timeline summary

| Feature | Target |
| --- | --- |
| Realm-aware price fields | shipped v0.9.0 / v1.0.0 |
| SQLite RevocationStore | shipped v0.9.0 / v1.0.0 |
| Data-method enforcement by default (D5) | shipped v0.9.0 / v1.0.0 |
| Per-bundle sibling verify (P0-9) | shipped v0.9.0 / v1.0.0 |
| CLI audit verify dedup | shipped v0.9.0 / v1.0.0 |
| API surface freeze + semver policy | shipped v1.0.0 |
| Production/Stable classifier | shipped v1.0.0 |
| OAuth 2.1 PKCE polish | v1.1.0 |
| Native per-token rate limiting | v1.1.0+ |
| Postgres sink | v1.1.0+ |
| Per-policy version pinning | v1.1.0+ |
| stdio transport | v1.1.0+ |
| Policy composition (chaining) | v1.1.0+ |
| Shadow MCP discovery via MDM | v1.1.0+ |
| Multi-tenant in OSS | not planned |
| ML intent inference | not planned |

---

Want a feature here moved up? Open an issue with the use case.
