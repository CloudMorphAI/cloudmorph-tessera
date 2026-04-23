# 06 — Intent System Design

_Green-field. The differentiator from generic MCP gateways. ~24h MVP work._

---

## 1.1 Why intent is the differentiator

Other MCP firewalls (the few that exist) inspect *what* an agent is calling. Intent inspects *why*. The pitch:

> "Your agent says it's auditing S3 public access. The next call is `delete_bucket`. Block it — that's not auditing."

This is **the only thing in the firewall that catches mis-aligned agents** (not jailbroken — *mis-aligned*, doing something they shouldn't even though no one tried to attack the system). Customers will buy on this.

---

## 1.2 What counts as intent — the founder call

Three options for the intent representation:

| Option | Pros | Cons |
|---|---|---|
| Free-form text | Easy for agents (any LLM can produce one); humans can read | Hard to enforce — needs LLM judge for matching; subjective |
| Structured verbs (typed schema: verb + object + scope + constraints) | Enforceable; deterministic policy eval; machine-readable | Burdensome for agents to author; vocabulary becomes a moat |
| **Hybrid: structured verbs + free-form goal** | Strict where enforcement matters; expressive where humans need it; LLM judge fallback for ambiguity | Two-step adoption: agent must learn vocabulary |

**Decision: hybrid, locked.** Justification:
- Strict `structuredVerbs[]` enables deterministic Rego rules — no LLM in the policy hot path.
- Free-form `statedGoal` preserves human readability for audit logs and approval UIs.
- LLM judge runs *only* when lexical/semantic match is ambiguous — bounds cost.

Schema lives in [contracts/02_contracts_audit.md §2.3 IntentDeclaration](../contracts/02_contracts_audit.md).

---

## 1.3 Intent verb taxonomy — locked vocabulary

22 verbs grouped into 8 families. **Extensible only at major version bumps** — agents and policies pin schema version.

| Family | Verbs | Risk class |
|---|---|---|
| Read | `read.list`, `read.describe`, `read.get`, `read.search`, `read.aggregate` | low |
| Analyze | `analyze`, `summarize`, `compare` | low |
| Write | `write.create`, `write.update`, `write.delete` | medium-high |
| Execute | `execute.run`, `execute.deploy` | high |
| Notify | `notify.send`, `notify.publish` | medium |
| Escalate | `escalate.approve`, `escalate.deny` | (meta) |
| Audit | `audit.log`, `audit.export` | low |
| Simulate | `simulate`, `dry_run` | low |

**Why a verb taxonomy and not Rust-style ADT verbs:** simplicity. Agents need to be able to declare intent in 30 seconds via a tool call; a fancy ADT type system means custom client SDK code per version. Strings + enum is enough.

**Per-action mapping:** every cloud action maps to a verb family. The mapping lives in `cloudmorph-common-py/cloudmorph_common/action_verbs.py` and `-ts/src/action_verbs.ts`:

```python
ACTION_VERBS: dict[str, set[str]] = {
    "aws.s3.list_buckets": {"read.list"},
    "aws.s3.list_objects": {"read.list"},
    "aws.s3.delete_bucket": {"write.delete"},
    "aws.s3.delete_object": {"write.delete"},
    "aws.s3.put_object": {"write.create", "write.update"},
    "aws.iam.create_user": {"write.create"},
    "databricks.sql.execute_query": {"read.list", "read.aggregate", "write.create", "write.update", "write.delete"},  # depends on SQL — see special handling
    "snowflake.sql.execute_query": ...,
    ...
}
```

The dispatch action `databricks.sql.execute_query` has *all* possible verbs because the SQL inside determines the actual verb. The intent matcher (§1.5) parses the SQL with sqlglot and narrows the verb set.

---

## 1.4 Intent lifecycle

```
declare → bind to session → reference in tool calls → expire (TTL or session end) | revoke
```

Detail:

1. **Declare** — agent calls `cloudmorph_declare_intent(...)`. Server creates `IntentDeclaration` record, stores in session map, returns `intentId`. Audit emits `intent.declared`.
2. **Bind** — `intentId` is bound to the session. A session can have multiple active intents simultaneously (e.g., "audit access" + "send report" — two declared intents).
3. **Reference** — every subsequent `cloudmorph_request` (and `cloudmorph_proxy`) accepts `intentId`. If absent, decision evaluates with `input.intent` undefined — typically falls through to more restrictive default rules.
4. **Expire** — TTL default 5 min (configurable up to 60 min). Server removes from session map; subsequent references → "intent_expired" decision. Audit emits `intent.expired`.
5. **Revoke** — agent can call `cloudmorph_revoke_intent({intentId})`. Useful when an agent realizes mid-task it's about to violate intent. Audit emits `intent.revoked`.

Telemetry: track `(declared, referenced, expired_unreferenced, revoked)` per session. Declarations without follow-on calls signal dropped intents — useful for debugging agents.

---

## 1.5 Mismatch detection algorithm

Three-stage cascade:

```
Stage 1: Lexical (always run, < 1ms)
   ↓ if matched → done
Stage 2: Semantic via embedding (if Stage 1 ambiguous, ~50ms with cache)
   ↓ if matched → done
Stage 3: LLM judge (if Stage 2 ambiguous, ~500ms-1s, MVP stub returns "match")
   ↓ verdict
```

### Stage 1 — Lexical

Compare `intent.structuredVerbs[]` against the action's mapped verbs:

```python
def lexical_match(intent: IntentDeclaration, action: str) -> MatchResult:
    action_verbs = ACTION_VERBS.get(action, set())
    if not action_verbs:
        return MatchResult(verdict="ambiguous", reason="unknown_action_verb_mapping")
    
    intent_verbs = set(intent.structuredVerbs)
    
    if action_verbs <= intent_verbs:  # action's verbs are a subset of declared
        return MatchResult(verdict="match", confidence=1.0)
    
    if action_verbs & intent_verbs:  # partial overlap
        return MatchResult(verdict="ambiguous", confidence=len(action_verbs & intent_verbs) / len(action_verbs))
    
    return MatchResult(verdict="mismatch", confidence=0.0, reason=f"declared {intent_verbs}, attempted {action_verbs}")
```

Examples:
- Intent `{read.list, read.describe}` + action `aws.s3.list_buckets` (verbs `{read.list}`) → **match**
- Intent `{read.list}` + action `aws.s3.delete_bucket` (verbs `{write.delete}`) → **mismatch** (denial)
- Intent `{read.list, read.aggregate}` + action `databricks.sql.execute_query` (all verbs possible) → **ambiguous** → escalate to Stage 2

### Stage 2 — Semantic

Embedding similarity between `intent.statedGoal` and the action's description (from the action catalog):

```python
def semantic_match(intent: IntentDeclaration, action: str) -> MatchResult:
    intent_emb = embed(intent.statedGoal)             # cached per intentId
    action_desc = ACTION_DESCRIPTIONS[action]
    action_emb = embed(action_desc)                   # cached per action
    
    sim = cosine_similarity(intent_emb, action_emb)
    if sim > 0.7:
        return MatchResult(verdict="match", confidence=sim)
    if sim > 0.4:
        return MatchResult(verdict="ambiguous", confidence=sim)
    return MatchResult(verdict="mismatch", confidence=sim)
```

Embedding model: `voyage-3-lite` (fast, cheap, $0.02/1M tokens) or local `all-MiniLM-L6-v2` (free, ~50ms self-hosted). MVP uses local for zero external dependency; post-MVP adds remote option for higher quality.

### Stage 3 — LLM judge

Used only when Stages 1 and 2 are both ambiguous. Calls a small fast model (Haiku 4.5):

```python
async def llm_judge(intent: IntentDeclaration, action: str, args: dict) -> MatchResult:
    prompt = f"""
You are a security policy judge. Decide if the action is consistent with the agent's stated intent.

Stated intent: "{intent.statedGoal}"
Stated structured verbs: {intent.structuredVerbs}
Attempted action: {action}
Attempted arguments: {json.dumps(args)[:500]}

Respond with exactly one of: MATCH, MISMATCH, AMBIGUOUS.
Then on a new line, a one-sentence reason.
"""
    resp = await anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    verdict_line, reason_line = resp.content[0].text.strip().split("\n", 1)
    return MatchResult(verdict=verdict_line.strip().lower(), reason=reason_line)
```

LLM-judge results are **cached per `(intentId, action, sha256(args))`** for 5 min. So the same agent loop hitting the same action with the same args pays the LLM cost once.

**MVP scope:** Stage 1 production-ready, Stage 2 stubbed (returns "ambiguous" for unknown actions; embedding lookup added in week 2 if time), Stage 3 stubbed (returns "match" + comment "llm_judge_not_implemented_in_mvp"). Block E.

### Configurable strictness

Per tenant, set the strictness mode:

| Mode | Behavior |
|---|---|
| `strict` | Lexical mismatch → deny. Lexical ambiguous → semantic; semantic mismatch → deny; semantic ambiguous → LLM judge → deny if mismatch. |
| `balanced` | Lexical mismatch → deny. Ambiguous → semantic; semantic ambiguous → LLM judge; LLM mismatch → audit_only (log loudly, allow). |
| `permissive` | Mismatches downgraded to `audit_only`. Useful for safe rule rollout. |

Default: `balanced`. Configurable in tenant settings (data document in policy bundle).

---

## 1.6 Telemetry

Metrics:
- `cm_intent_declarations_total{tenant, verb, agent_vendor}` — declarations by verb
- `cm_intent_calls_total{tenant, intent_id}` — actions referencing intent
- `cm_intent_expired_unreferenced_total{tenant}` — declarations with zero calls
- `cm_intent_revoked_total{tenant}` — revocations
- `cm_intent_mismatch_total{tenant, severity, stage}` — by detection stage
- `cm_intent_match_stage_seconds{stage}` — eval time by stage
- `cm_intent_llm_judge_calls_total` — count of stage-3 escalations
- `cm_intent_llm_judge_cache_hits_total`

Audit events:
- `intent.declared`
- `intent.revoked`
- `intent.expired`
- `intent.referenced` (debug only)
- `intent.mismatch_detected`

---

## 1.7 Edge cases

### Multiple intents in one session

A session can have several active intents (e.g., one for "audit S3" and another for "send report"). When a tool call comes in without a specific `intentId`, the matcher tries each active intent and uses the **best match** (or denial if no match across all).

### No intent declared

Two policies:
- **Strict:** every tool call requires an intentId; missing intent → deny with `intent_required` reason.
- **Permissive:** missing intent → fall through to default rules; typically restrictive (only `read.list`/`read.describe` actions allowed).

Per-tenant config. MVP default: **permissive** (lower friction for first integration).

### Intent referenced after expiry

Server returns decision with outcome `deny`, reason `intent_expired`. Audit emits `intent.referenced` with `state: expired`.

### Intent declared but never used

Audit emits `intent.expired` at TTL with `referencedCount: 0`. Surfaced in dashboards — agents that drop intents on the floor are buggy and customers should know.

### Conflicting intents

Two active intents `{read.list}` and `{write.delete}` — and a `delete_object` call. Matches the second intent → allowed. **Customers wanting "intents are conjunctive" (must match ALL) need a tenant-setting flag** — `intentMatchMode: "any" | "all"`. MVP defaults to "any". Post-MVP adds "all".

### Stale verb mapping

A new action ships in an executor but `ACTION_VERBS` isn't updated → verb mapping is empty → matcher returns ambiguous → escalates. **Mitigation:** CI gate that asserts every action handler has a verb mapping. Add `tests/test_action_verbs_complete.py` that imports the registry and the verb map and asserts no action is unmapped.

---

## 1.8 Cross-system integration

### MCP server

- New tool `cloudmorph_declare_intent` (see [mcp/01_server_audit.md §1.2](../mcp/01_server_audit.md))
- New tool `cloudmorph_revoke_intent`
- Per-request: pre-policy step computes `intentMatchScore`, attaches to `PolicyInput`

### Policy engine

- Reads `input.intent` and `input.intentMatchScore` in Rego rules
- See [policy/05_policy_engine_design.md §1.7](../policy/05_policy_engine_design.md) for the mismatch rules

### SDK

- Python SDK exposes `cm.declare_intent(IntentDeclaration)` and `cm.revoke_intent(intentId)`
- The Anthropic adapter auto-extracts intent from the system prompt + first user message via heuristic (MVP) or LLM (post-MVP)

### Audit log

- Every decision includes `evidence.intentMatchScore`
- Intent lifecycle events (declared/revoked/expired) emitted as separate audit events

---

## 1.9 Data structures

```typescript
// In-memory session store
interface SessionStore {
  get(sessionId: string): Promise<Session | null>;
  setIntent(sessionId: string, intent: IntentDeclaration): Promise<void>;
  removeIntent(sessionId: string, intentId: string): Promise<void>;
  cleanup(now: Date): Promise<number>;   // returns count expired
}

// MVP: in-process Map; post-MVP: Redis
class InMemorySessionStore implements SessionStore { ... }
class RedisSessionStore implements SessionStore { ... }   // post-MVP

// Intent matcher
interface IntentMatcher {
  match(intent: IntentDeclaration, action: string, args: object): Promise<MatchResult>;
}

interface MatchResult {
  verdict: "match" | "ambiguous" | "mismatch";
  confidence: number;
  stage: "lexical" | "semantic" | "llm_judge";
  reason?: string;
  cacheHit?: boolean;
}
```

---

## 1.10 Test plan

| Test | Effort |
|---|---:|
| `tests/test_intent_lexical.py` — every verb combo, every action verb mapping | 4h |
| `tests/test_intent_semantic.py` — mocked embedding, similarity thresholds | 3h |
| `tests/test_intent_llm_judge.py` — mocked anthropic call, cache hit | 3h |
| `tests/test_intent_lifecycle.py` — declare/revoke/expire/reference | 4h |
| `tests/test_intent_session_store.py` — store impl, cleanup, multi-intent | 3h |
| `tests/test_intent_action_verbs_complete.py` — every executor handler has a verb mapping | 1h |
| `tests/test_intent_strictness_modes.py` — strict/balanced/permissive across same fixtures | 2h |

**Total: ~20h.**

---

## 1.11 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| `IntentDeclaration` contract | P0 | (contracts/02) | B |
| Verb taxonomy + action mapping | P0 | 6h | E |
| `cloudmorph_declare_intent` tool | P0 | 6h | D |
| `cloudmorph_revoke_intent` tool | P1 | 1h | D |
| Session store (in-memory) | P0 | 4h | D |
| Session store (Redis) | P2 | 6h | post-MVP |
| Lexical matcher | P0 | 6h | E |
| Semantic matcher (embedding lookup) | P1 | 8h | E (stretch) |
| LLM judge (stub for MVP) | P1 | 2h | E |
| LLM judge (real) | P2 | 6h | post-MVP |
| Strictness modes (3 tiers) | P1 | 4h | E |
| Tests (~20h) | P0 | 20h | E |
| Telemetry + metrics | P1 | 4h | H |
| `cm_intent_*` Prometheus metrics | P1 | (in H) | H |
| Stale verb mapping CI gate | P1 | 1h | A |

**MVP critical-path total: ~52h.** Block E (much of it shared with policy engine work).

---

## 1.12 Out of scope

- Intent declaration via natural language only (no structured verbs at all). Re-evaluate after first 3 design partners — if the verb vocabulary is the friction, soften.
- LLM-judge in the hot path (>10% of evaluations). MVP keeps it on cold/escalation only.
- Cross-session intent inheritance ("inherit my prior session's intent"). Adds complexity for unclear value.
- Intent autoshare across organizations (intent marketplace). Premature.

---

## 1.13 Source-link references

- [contracts/02_contracts_audit.md §2.3 IntentDeclaration](../contracts/02_contracts_audit.md)
- [policy/05_policy_engine_design.md §1.7](../policy/05_policy_engine_design.md) — intent-conditional Rego rules
- [mcp/01_server_audit.md §1.6](../mcp/01_server_audit.md) — MCP integration points
- [sdk/03_python_sdk_audit.md §3.4](../sdk/03_python_sdk_audit.md) — Anthropic adapter intent extraction
- [ARCHITECTURE.md §7](../ARCHITECTURE.md)
