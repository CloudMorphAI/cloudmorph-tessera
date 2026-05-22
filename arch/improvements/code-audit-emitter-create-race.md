# code-audit: _get_or_create_emitter race — audit hash chain head corruption (HIGH)

**Discovered**: 2026-05-22 code-audit overnight pass
**Severity**: HIGH

## Problem
`tessera/proxy.py:1087-1113` `_get_or_create_emitter`:
1. Checks `if scope in emitter_map`
2. If absent, creates and stores a new `AuditEmitter`
3. No lock around (1)–(2)

FastAPI runs on the asyncio event loop with concurrent request handling. Two coroutines handling different requests for the same new `scope` can both pass the "scope not in map" check, both call `hash_chain.restore_head(scope, head)` (the second may clobber the head the first installed), and both store their own `AuditEmitter`. The second write overwrites the first; the first emitter's in-flight events stamp against an abandoned head — producing audit chain forks.

Production impact: rare but real. Audit chain breaks for the first few events of any new scope under concurrent first-request load.

## Where
- File: `tessera/proxy.py:1087-1113`
- Function: `_get_or_create_emitter`

## Suggested fix
Protect `_get_or_create_emitter` with a module-level lock. The simplest correct option is `threading.RLock()` on a module-level `_emitter_map_lock`. Acquire it around the read + create + store. The lock is fast (no I/O held) so contention is negligible.

Asyncio purists may prefer `asyncio.Lock()` but the existing emit path uses `asyncio.to_thread` so the call site is already thread-comfortable. RLock is safer because it doesn't require the caller to be in async context.

Add a regression test that spawns 2 coroutines simultaneously calling `_get_or_create_emitter("new-scope")` and asserts both receive the *same* `AuditEmitter` instance.

## Effort
small (one lock + acquire/release wrapper + a concurrency test)

## Acceptance criteria
- Two coroutines requesting the same new scope receive the same `AuditEmitter` instance
- `hash_chain.restore_head` is called at most once per scope under concurrent first-request load
- Pre-existing scope lookups are unaffected (the lock only protects the create path or the whole check-and-set if simpler)

## On merge
Folds into the OSS audit emitter status doc. This file deleted after merge.
