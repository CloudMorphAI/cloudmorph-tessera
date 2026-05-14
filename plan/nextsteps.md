# Next steps — cloudmorph-tessera

State of the source as of 2026-05-15, after three audit iterations (commits
`7d1ff5d`, `f295727`, `fd81998`). Tests green (529 pass, 0 fail).

## Open

The big things still on the table, ordered by risk-to-correctness:

### A. PyJWT undeclared dependency
- `tessera/auth/oauth_rs.py` imports `jwt` (PyJWT) for `/introspect`; PyJWT is installed only as a transitive of `mcp`/`msal`. If either drops it, `introspect` silently returns `{"active": false}` on every call.
- Pick one: (a) add `PyJWT>=2.x` to `pyproject.toml`; (b) switch `oauth_rs.py` to `python-jose` (matches the rest of the codebase); (c) document the implicit dependency.

### B. `jwt_mcp.py` blocks the event loop on JWKS cache miss
- Same class of bug as the DCR-proxy ASYNC212 fix in iter 3, but on the MCP-traffic JWT validator. First request after JWKS cache rotation does a synchronous httpx call inside an async handler.
- Path A: lift JWKS prefetch into the lifespan startup; pre-populate `JWKSCache`. Path B: make `validate_jwt`/`fetch_jwks` async. Path A is smaller.

### C. Decorator-typed FastAPI routes in `make_metadata_route`
- 4 mypy `untyped-decorator` errors at `oauth_rs.py:194/200/211/297`. Inner functions registered via `@app_ref.get(...)` lose their type info because mypy sees `app_ref: Any`. Cosmetic but blocks a fully-clean `mypy tessera/`.
- Fix: type `app_ref` as `FastAPI`. May require a small `TYPE_CHECKING` import.

### D. Anthropic SDK type drift (`tessera/llm/anthropic.py`)
- Still 9 mypy `union-attr` errors because the SDK's content union includes blocks (`ToolUseBlock`, `WebSearchToolResultBlock`, etc.) without a `.text` attribute. iter 3 narrowed the runtime path with `isinstance(block, anthropic.types.TextBlock)`; mypy doesn't see through the generator expression. Either inline an explicit loop with a return-type annotation, or accept the noise.

### E. Subset-match fallback in `price_table.py`
- Algorithm is now correct (iter 2) but worst-case 2^N at `_SUBSET_MATCH_MAX_ARGS=10`. Above 7 args this does up to 1024 dict lookups per call. Acceptable for hot-path latency at typical sizes; profile if the operator wants more headroom or move to an "iterate the indexed entries for this op+realm" approach (O(N entries)).

## Suggested order

1. **A** — small change (one line in pyproject), removes the silent failure mode.
2. **B** — meaningful availability win for the JWT-MCP-mode operator.
3. **C** — mypy cleanup; reach 0-errors-in-tessera/.
4. **D** — same; would let `mypy tessera/` enforce in CI.
5. **E** — only worth doing if a customer reports a performance issue.

Lower-priority items not blocking anything:
- 56 ruff issues remain, mostly in `tests/` (import ordering, unused imports). One run of `ruff check --fix tests/` would clear most.
- The `licenseTier` audit-event extension promised by `arch/improvements/v0.3.0-stripe-integration.md` is not implemented. Once Stripe→license-server wiring lands in `cloudmorph-mono-repo`, this becomes actionable.
