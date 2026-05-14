# System Overview

`cloudmorph-tessera` is the OSS half of the Tessera product. It ships a Python package (PyPI: `cloudmorph-tessera`, import name `tessera`) and a container image (`ghcr.io/cloudmorphai/tessera`) that customers run between their AI agent and every MCP server the agent calls. Every `tools/call` is intercepted, evaluated against a YAML policy set, written to a hash-chained audit log, and either forwarded to the upstream or rejected with a JSON-RPC error. This document situates the package within the three-repo product split, names the subsystems under `tessera/`, and traces the request lifecycle.

## What this repo produces

Two artifacts, customer-installed:

1. **The PyPI distribution `cloudmorph-tessera`** — a wheel + sdist containing the `tessera` package, the bundled `tessera/intelligence/public_key.pem` trust anchor, the 12 reference policies (`tessera/policies_default/`), and the `tessera` CLI entry point.
2. **The OCI image `ghcr.io/cloudmorphai/tessera:<version>`** — a multi-stage `python:3.12-slim` build pinned to a SHA-256 base digest, running as UID 10001, signed via Sigstore cosign keyless, with a CycloneDX SBOM attested alongside. Mirrored to a private ECR repo (`cloudmorph/tessera-cloud-prod`) for the Fargate consumers in `cloudmorph-mono-repo`.

Both artifacts are produced from a single tag-push by `.github/workflows/release.yml`. See `packaging-and-release.md` for the full pipeline.

## Three-repo product split

```
┌────────────────────────┐       ┌────────────────────────┐
│   cloudmorph-tessera   │       │  tessera-intelligence  │
│ ─────────────────────  │       │ ─────────────────────  │
│ OSS Python firewall    │       │ Signed policy packs    │
│ Customer-installed     │       │ AWS cost mappings      │
│ Pure-Python engine     │       │ Blast-radius rules     │
│ Hash-chain audit log   │       │ Build + publish to CDN │
│ public_key.pem bundled │       │ Ed25519 signing key    │
└──────────┬─────────────┘       └────────────┬───────────┘
           │ fetches + verifies              │ produces + signs
           │                                  │
           ▼                                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │       intelligence.tessera.cloudmorph.ai (CDN)          │
   │       catalogs / packs / mappings / blast-radius        │
   └─────────────────────────────────────────────────────────┘
                       ▲
                       │ issues license JWTs
                       │
              ┌────────────────────────┐
              │  cloudmorph-mono-repo  │
              │ ─────────────────────  │
              │ License server         │
              │ Admin console          │
              │ Stripe + tenant mgmt   │
              │ Fargate CDK constructs │
              └────────────────────────┘
```

The split is deliberate. `cloudmorph-tessera` is Apache-2.0-licensed and customer-installed; it must contain only client logic, the public verification key, and no proprietary secrets. `tessera-intelligence` is the proprietary content surface — private signing key never leaves the founder's machine. `cloudmorph-mono-repo` is the proprietary backend (admin console, license issuance, tenant management, the Fargate stack that runs this image at scale).

## Package layout under `tessera/`

```
tessera/
├── __init__.py            # __version__
├── cli.py                 # Typer CLI: serve, audit, policy, init, install-*, pricing
├── proxy.py               # FastAPI app — the MCP interception layer
├── pluggable.py           # importlib resolver for the 3 Protocol extension points
├── config.py              # TesseraConfig (pydantic) + YAML loader + env overrides
├── errors.py              # TesseraError hierarchy
├── intent.py              # _meta.tessera_intent extraction + validation
│
├── auth/                  # Authenticator implementations
│   ├── base.py            # AuthContext, Authenticator Protocol
│   ├── bearer.py          # BearerTokenAuthenticator (multi-token)
│   ├── jwt_mcp.py         # JWTAuthenticator (MCP-traffic mode)
│   ├── oidc.py            # OIDCAuthenticator (management-plane)
│   └── _jwks.py           # Shared JWKS cache + JWT validation
│
├── audit/                 # Hash-chain audit log
│   ├── chain.py           # HashChain — per-scope rolling SHA-256
│   ├── emitter.py         # AuditEmitter — fan-out to sinks
│   ├── canonical_json.py  # RFC 8785 JCS
│   ├── verifier.py        # Chain-walk integrity check
│   └── sinks/             # AuditSink Protocol + SqliteSink, StdoutSink, _buffered
│
├── policy/                # Pure-Python policy engine
│   ├── engine.py          # First-match-wins evaluator
│   ├── schema.py          # Policy + 21 condition pydantic models
│   ├── conditions.py      # Per-condition evaluators + dispatch table
│   ├── matchers.py        # upstream / tool / tool_pattern match
│   ├── loader.py          # FilesystemPolicyLoader (watch + reload isolation)
│   ├── action_verbs.py    # Tool-name → intent-verb registry
│   └── regex_safety.py    # ReDoS corpus validator
│
├── policies_default/      # 12 bundled YAMLs (7 generic + 5 AWS-illustrative)
│
├── intelligence/          # Consumer of tessera-intelligence content
│   ├── client.py          # IntelligenceClient — fetch + verify + cache
│   ├── license.py         # LicenseValidator — JWT check + 7-day fallback
│   └── public_key.pem     # Ed25519 trust anchor (byte-coupled to producer)
│
├── cost/                  # AWS cost evaluation
│   ├── aws_mapping.py     # 10 builtin tool → Infracost-query mappings + extended loader
│   └── infracost.py       # GraphQL client (200ms timeout, 300s cache)
│
├── integrations/          # External-system glue
│   ├── cursor_hooks.py    # beforeMCPExecution / afterMCPExecution hook script
│   └── aws/
│       ├── upstream.py    # AWSMcpUpstream — kind: aws_mcp, IAM-signed routing
│       └── blast_radius.py # BlastRadiusBackend — boto3-driven principal counter
│
├── state/                 # Local stateful backends
│   └── daily_spend.py     # DailySpendState — SQLite per-scope spend tracker
│
└── llm/                   # Opt-in LLM-driven policy authoring (off the hot path)
    ├── base.py            # PolicyAuthor + ToolCatalogAnalyzer Protocols
    ├── _shared.py         # Schema-driven system prompt builder
    └── anthropic.py | openai.py | azure_openai.py | bedrock.py | gemini.py
```

The package is import-clean: every optional dependency (boto3, anthropic, google-genai, gql, cryptography, python-jose) lives behind a pip extras group and behind a lazy import. A minimal `pip install cloudmorph-tessera` brings in only the core FastAPI/uvicorn/httpx/pydantic/typer/regex/watchdog/PyYAML/jmespath set. Detailed list: `pyproject.toml:dependencies`.

## Top-level request flow

```
agent → POST /mcp/<upstream> → Tessera
                                  ├─ authenticate (bearer | JWT)            → AuthContext
                                  ├─ parse JSON-RPC
                                  ├─ branch on method:
                                  │    notifications/* + 11 pass-through → forward + audit
                                  │    tools/call → continue
                                  ├─ extract intent from _meta.tessera_intent
                                  ├─ pre-fetch cost estimate (if Infracost backend present)
                                  ├─ build evaluation context
                                  ├─ lockdown check (runtime.lockdown) → block if set
                                  ├─ mode branch:
                                  │    observation → forward + audit (engine skipped)
                                  │    log_only → engine + forward + audit (X-Tessera-* headers)
                                  │    enforcement → engine result is honored
                                  ├─ engine.evaluate(context) → Decision
                                  ├─ for block / require_approval → JSON-RPC error
                                  ├─ for allow / log_only → forward to upstream
                                  └─ audit event written, eventId injected into response _meta
```

Implementation is a single FastAPI endpoint at `tessera/proxy.py:proxy` (line 538). The pre-fetch step is what allows the synchronous `predicted_cost` condition to query an async cost backend without an in-engine event-loop dance — the proxy populates `context["cost_cache"]` before calling `engine.evaluate()`. Detailed design: `proxy-enforcement-and-audit.md` and `policy-engine.md`.

## Trust model in one sentence

The customer trusts intelligence content because every signed artifact verifies against `tessera/intelligence/public_key.pem`, which is bundled into the wheel they `pip install`; the CDN's license JWT gating is opportunistic tier enforcement, not a security boundary.

The same wedge applies inside the firewall: enforcement is **deterministic** — no ML, no LLM in the hot path, no remote calls except the optional Infracost backend (which has a 200ms timeout and fails-closed). The policy outcome for a given input is byte-identical across runs. Detailed design: `proxy-enforcement-and-audit.md` (deterministic-block-at-call-time as the architectural wedge).

## What this repo does not contain

- **The Ed25519 private key.** It lives only in `tessera-intelligence` and on the founder's machine. The OSS package holds only the public half.
- **License-server JWT issuance logic.** That lives in `cloudmorph-mono-repo`. The OSS package validates incoming license JWTs by Ed25519-verifying their signature against the bundled public key (when present) and reading their tier claim.
- **The CloudFront distribution config.** Manually managed (see `tessera-intelligence/arch/status/distribution-cdn.md`).
- **The Fargate CDK constructs.** Live in `cloudmorph-mono-repo/amplify/backend/`; pull this image by digest from ECR.

## How to read the rest of this directory

Start with `proxy-enforcement-and-audit.md` for the hot path and the deterministic-firewall wedge. Read `policy-engine.md` next to understand how YAML becomes a runtime decision. `intelligence-and-licensing.md` covers the consumer side of the signing chain (cross-references `tessera-intelligence/arch/status/signing-and-trust.md` for the producer side). `integrations-and-cost.md` covers the AWS-specific surface and the consumer side of cost resolution (cross-references `tessera-intelligence/arch/status/aws-mappings.md` and `blast-radius.md`). `llm-policy-authoring.md` is the off-hot-path LLM subsystem. `packaging-and-release.md` is the distribution architecture (PyPI + GHCR + ECR via OIDC).

Planned future work lives in `arch/improvements/` and is version-scoped — each file describes the work needed to ship a specific named version (v0.2.1, v0.3.0, v0.4.0). When a version ships, its improvement files merge into the status docs and disappear.
