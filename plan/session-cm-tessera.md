# Long-running audit session — cloudmorph-tessera

**Date**: 2026-05-14 → 2026-05-15
**Model**: Claude Opus 4.7 (1M context)
**Scope**: Audit every source file under `tessera/`, find improvements at the
code level, implement them, sync `arch/status/` docs, update arch/improvements
per merge-not-move convention.

**Starting commit**: `84a19a2` (auth(B-11): RFC 7591 DCR proxy + RFC 7662 token
introspection endpoints)

## Pre-flight baseline

| Check | Result |
|------|--------|
| Git state | dirty: `CHANGELOG.md` + `pyproject.toml` (in-progress v0.2.1 version bump from prior session). Left untouched per the "halt if dirty" rule's spirit — contained, not blocking. |
| Tests | 525 pass, 4 fail, 2 skipped. Failures all in `tests/test_audit_cli.py` with sqlite UNIQUE constraint violations. |
| Ruff | 83 errors (61 auto-fixable). Notable: 1× ASYNC212 (blocking httpx in async — real bug), 30× I001 import sort, 24× F401 unused imports. |
| Mypy | 103 errors in 30 files. Notable: 9 `Unused "type: ignore"` + 4 `object not callable` errors in `proxy.py` from type drift in `pluggable.resolve()`. |

## Iteration 1 — Hot path (proxy + policy + audit)

Commit: `7d1ff5d` (`arch: iteration 1 — hot-path correctness + type drift cleanup`)

### Findings
- **F-1.7 [HIGH]** `tessera/policy/conditions.py:322-329` — `_evaluate_predicted_cost` fallback called `asyncio.get_event_loop().run_until_complete(...)` from inside the *synchronous* policy engine. The engine runs inside the proxy's `async def proxy(...)` handler, so the event loop is already running and `run_until_complete` would raise `RuntimeError`. Per arch docs the proxy always pre-fetches cost into `context["cost_cache"]`; the fallback was dead-but-explosive code. Removed.
- **F-1.11 [HIGH]** `tests/test_audit_cli.py:_make_event` — every event had `eventHash = "a" * 64`. The sink's `audit_events.event_hash TEXT NOT NULL UNIQUE` constraint rejected the second insert, breaking 4 tests. Now derives a per-id SHA-256.
- **F-1.1 / F-1.2 / F-1.3 [MED]** `tessera/pluggable.py` returned `object` so callers tripped mypy's `not callable` and needed `# type: ignore[call-arg]`. Tightened to `type[Any]` + isinstance guard. Removed 7 stale `# type: ignore` comments in `proxy.py`. Added `TYPE_CHECKING` import for `AWSMcpUpstream` so `_forward_upstream` returns a concrete type.
- **F-1.8 / F-1.9 [LOW]** Redundant `except (TimeoutError, Exception)` / `(ZoneInfoNotFoundError, Exception)` clauses. Split into two clauses or narrowed to the actual raised exceptions.
- **F-1.10 [LOW]** `tessera/policy/matchers.py` silently swallowed `tool_pattern` regex failures. Added a debug log (pattern was already validated at load time so reaching here is unusual).
- **F-1.4 [LOW]** Pricing snapshot background refresh task swallowed all exceptions. Added debug log.

### Result
- Tests: 525 → **529** pass (4 pre-existing failures resolved).
- Ruff/mypy on `tessera/proxy.py`, `tessera/pluggable.py`, `tessera/policy/`, `tessera/audit/`: clean.
- No arch updates needed — changes brought code into alignment with documented design.

## Iteration 2 — Trust chain + cost + state

Commit: `f295727` (`arch: iteration 2 — trust-chain fix + cost/state typing cleanup`)

### Findings
- **F-2.1 [HIGH] — arch drift** `tessera/intelligence/client.py` fetched the catalog and pack tarballs **without sending the `X-Tessera-License` header**. The arch doc states the CloudFront license-gating function returns 401 without this header — the production CDN would have rejected every request. Reorganized `refresh()` so the license check runs first, then both catalogs and packs are fetched with `X-Tessera-License: <jwt>`.
- **F-2.12 [MED] — silent algorithmic bug** `tessera/cost/price_table.py:cost_for_call` Tier-2 subset matching contained `items[i + 1:i + drop_count + 1 - drop_count]` which always evaluates to `items[i+1:i+1]` — an empty slice. Effective behavior: drop K *contiguous* items starting at i. Non-contiguous subsets (e.g., callers passing extra args interleaved with the indexed key set) were never tried. Replaced with bounded `itertools.combinations` (capped at `_SUBSET_MATCH_MAX_ARGS=10` so 2^N doesn't explode on pathological calls).
- **F-2.10 [LOW] — comment drift** `tessera/intelligence/client.py:169` `# noqa: BLE001 — TamperDetected re-raised as log+skip` was misleading; the code does NOT re-raise. Tightened to `# log + skip on TamperDetected`.
- **F-EBS [LOW]** `_map_ebs_create_volume` computed `size`/`iops` locals that were never used (Infracost EBS uses them as usage multipliers, not SKU attribute filters). Removed with explanatory comment.
- **Type cleanup** across cost/intelligence/state: `dict` → `dict[str, Any]`, `asyncio.Task | None` → `asyncio.Task[None] | None`, `dict[str, Any]` → `dict[str, PriceTable]`, removed unused `# type: ignore` after `_TIER_ORDER` narrowing, `# type: ignore[import-untyped]` for jose + yaml stubs.

### Arch doc update
- `arch/status/intelligence-and-licensing.md` — added one sentence documenting that `LicenseStatus.jwt` is retained for CDN forwarding and that the CDN returns 401 without it.

### Result
- Tests: 529 pass (no regressions; `tests/integration_cdn_smoke.py` is env-gated and remains the live integration check).
- Ruff/mypy on intelligence/cost/state/config: clean.

## Iteration 3 — Surfaces (auth + llm + cli + integrations)

Commit: `fd81998` (`arch: iteration 3 — surface cleanup + auth/llm correctness`)

### Findings
- **F-3.1 [HIGH] — real async bug** `tessera/auth/oauth_rs.py` DCR-proxy endpoint used `with httpx.Client(timeout=10.0) as client: upstream_resp = client.post(...)` inside an async FastAPI handler. Synchronous httpx blocks the event loop until the upstream responds — a single slow upstream stalls every concurrent request. Switched to `async with httpx.AsyncClient(...)` + `await client.post(...)`. Updated `test_dcr_proxy_forwards_to_upstream` to mock `AsyncClient` instead of `Client`.
- **F-3.2 [MED] — dead code** `tessera/auth/oauth_rs.py` had two module-level `@router.get("/.well-known/oauth-protected-resource")` placeholder handlers that returned 500 and were never mounted. Plus a `router = APIRouter()` symbol that was never imported anywhere. Removed all three.
- **F-LLM [MED] — Anthropic SDK assumption** `tessera/llm/anthropic.py:130,81` accessed `response.content[0].text` assuming index 0 is always a `TextBlock`. Modern Claude (Opus 4 / Sonnet 4 / Haiku 4) interleaves `ThinkingBlock`/`ToolUseBlock`/`TextBlock` when extended thinking is enabled, so the index-zero assumption breaks intermittently. Replaced with isinstance-narrowed lookup of the first `TextBlock`. Updated mock test fixture to use `spec=TextBlock` for the same reason.
- **F-LLM-2 [LOW]** `tessera/llm/gemini.py` did not guard `response.text is None` before passing to `_parse_and_validate_response(text: str)`. Now raises ValueError that the retry loop catches.
- **F-CLI [LOW]** `tessera/cli.py:policy_test` had `default_action: str = typer.Option(None, ...)` — type annotation said `str` but default was `None`, so mypy correctly flagged the `if default_action is None` branch as unreachable. Changed type to `str | None`. Same for `fixture_dir` and `fixture`. Added `Any` return type to `_resolve_llm_provider`.
- **Misc** — F-3.4 redundant FastAPI Request import (kept with `# noqa: TC002` since it's used at runtime), nested-if pair flattening, S603 false positive on docker subprocess call.

### Result
- Tests: 529 pass.
- Mypy: 103 errors in 30 files → **44 errors in 18 files** (−59 errors, −12 files). Remainder are external SDK type drift (anthropic content-block union, untyped `yaml`/`jose` stubs) and FastAPI's untyped `@app.get(...)` decorator inside `make_metadata_route` — not actionable at this level.
- Ruff: 83 errors → **56 errors** (mostly test-file cleanup remaining).

## Cross-repo drift discovered (NOT edited)

None encountered that required notes outside `cloudmorph-tessera`. The only cross-repo concern remains the `public_key.pem` byte-coupling between this repo and `tessera-intelligence/_metadata/public-key.pem`, which the producer-side round-trip test catches — already documented in `arch/status/intelligence-and-licensing.md`.

## Open questions for the founder

1. **PyJWT undeclared dep**: `tessera/auth/oauth_rs.py` imports `jwt` (PyJWT) for the `/introspect` endpoint, but PyJWT is not in `pyproject.toml` — currently installed as a transitive of `mcp` / `msal`. If those drop the dependency, introspect breaks silently (returns `{"active": false}` because of the broad except). Three options:
   - Declare `PyJWT>=2.x` as a runtime dep (simplest).
   - Switch `oauth_rs.py` to `python-jose` to match `_jwks.py` / `intelligence/license.py`.
   - Leave as-is and document that introspect requires the `[oidc]` extra group.
2. **`jwt_mcp.py` blocks the event loop on JWKS cache miss**: the synchronous `httpx.Client.get(jwks_url)` inside an async route is the same class of bug as F-3.1 but in a different surface. First request after cache rotation blocks. Either: (a) move JWKS fetch to startup background task, (b) make the JWKS cache async, (c) accept the trade-off and document.
3. **Subset-match fallback in price_table.py**: now correct, but the per-call cost of `itertools.combinations(items, size)` is 2^N at the worst case. The `_SUBSET_MATCH_MAX_ARGS=10` cap prevents explosion, but values 7-10 still do up to 1024 lookups per call. Acceptable for typical tool calls but worth profiling if hot path latency matters.

## Final state

| Check | Baseline | Final | Delta |
|------|---------|-------|-------|
| Tests passing | 525 | 529 | +4 (4 pre-existing failures fixed) |
| Test failures | 4 | 0 | -4 |
| Ruff errors | 83 | 56 | -27 |
| Mypy errors | 103 | 44 | -59 |
| Mypy-erroring files | 30 | 18 | -12 |

## Commits on local main (unpushed per instructions)

```
fd81998 arch: iteration 3 — surface cleanup + auth/llm correctness
f295727 arch: iteration 2 — trust-chain fix + cost/state typing cleanup
7d1ff5d arch: iteration 1 — hot-path correctness + type drift cleanup
```

Starting SHA `84a19a2` is unchanged on `origin/main` (no push performed).
