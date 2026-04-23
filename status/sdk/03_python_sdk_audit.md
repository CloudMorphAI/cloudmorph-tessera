# 03 — Python SDK Audit (`sdk-python/`)

_254 LoC, stdlib-only, sync, urllib-based. Solid bones, missing the firewall integration story._

---

## 3.1 What's there today

### [sdk-python/cloudmorph/__init__.py](../../sdk-python/cloudmorph/__init__.py) (18 LoC)

```python
from cloudmorph.client import CloudMorph, CloudMorphError, RateLimitError
CloudMorphClient = CloudMorph
__all__ = ["CloudMorphClient", "CloudMorph", "CloudMorphError", "RateLimitError"]
__version__ = "0.1.0"
```

Two public class names for the same thing (`CloudMorph` and `CloudMorphClient`), kept "for backwards compat" — but since the SDK is `0.1.0` and pre-MVP, this backwards-compat is purely a footgun. Pick one and remove the alias before v1.

**Findings:**
- **P2:** Drop the alias before v1 ships. Right now it just doubles the API surface for no reason.

### [sdk-python/cloudmorph/client.py](../../sdk-python/cloudmorph/client.py) (236 LoC)

The whole client. Single class `CloudMorph`. Key surface:

- `__init__(token, base_url=None, timeout=60)` — defaults `base_url` to `https://mcp.cloudmorph.io`. Raises `ValueError` on empty token. Strips trailing slash.
- `request(action, *, targets=None, payload=None, account_id=None, wait=False, wait_seconds=None) → Dict`
- `request_and_wait(action, *, ..., poll_interval=2.0, max_wait=120.0) → Dict` — submits with `wait=True`, then polls if not terminal.
- `get_request_status(request_id) → Dict`
- `get_job_status(job_id) → Dict`
- `_call_tool(name, arguments)` — JSON-RPC 2.0 over POST to `${base_url}/mcp`.
- `_http_post(url, body)` — urllib + retry-after parsing on 429.
- `_is_terminal(status) → bool` — static, returns True for `completed|failed|cancelled|canceled|blocked`.

Exception hierarchy: `CloudMorphError(Exception) {message, status, code, data}` and `RateLimitError(CloudMorphError) {retry_after_seconds}`.

**Strengths:**
- Stdlib-only — zero install friction.
- Sync — easy mental model for newcomers.
- JSON-RPC 2.0 spoken correctly.
- `Retry-After` header parsed correctly (despite a prior plan claim that it wasn't — verified at [client.py:207](../../sdk-python/cloudmorph/client.py)).

**Findings:**
- **P1 bug:** `CloudMorphError.code = err.get("message", "unknown")` at [client.py:165-167](../../sdk-python/cloudmorph/client.py) — uses the human-readable error message text as the structured `code` field. That makes downstream `if e.code == "tool_not_found"` checks brittle. **Fix:** map the JSON-RPC numeric `code` to a stable string (`-32601 → "method_not_found"`, etc.) or use `error.data.error_code` if upstream provides it. This is the bug the prior plan was reaching for.
- **P1:** `wait_seconds` is passed to the server unchecked. Server caps at 55. SDK should validate `0 ≤ wait_seconds ≤ 55` and raise `ValueError` for out-of-range *before* the network call, with a clear message about the cap.
- **P1:** `poll_interval=2.0` and `max_wait=120.0` are reasonable defaults but not configurable per-call attribute on the class. Move to class-level defaults so customers can change globally.
- **P1:** `request_and_wait` ignores cost — long-poll keeps the connection open for `wait_seconds`, then *also* polls. Doubles the work. Either keep one approach or document the chained behavior.
- **P1:** Synchronous `time.sleep(poll_interval)` blocks the calling thread/event loop. If used inside an async context, this is a footgun.
- **P2:** No structured logging (silently does network IO). Add `logger = logging.getLogger("cloudmorph")` and emit DEBUG-level events.
- **P2:** No request-id propagation in client logs.

### [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml) (32 LoC)

```toml
name = "cloudmorph"
version = "0.1.0"
license = "Apache-2.0"
requires-python = ">=3.9"
classifiers = [
    "Development Status :: 5 - Production/Stable",  # ← overstated
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
```

No `dependencies = [...]`, no `[project.optional-dependencies]`, no `py.typed`, no `tool.setuptools` package include for `tests`, no `tool.pytest.ini_options`, no `tool.ruff` config. Just bones.

**Findings:**
- **P1:** `Development Status :: 5 - Production/Stable` for `0.1.0` with no firewall integration is wildly overstated. Set to `4 - Beta`.
- **P1:** Add Python 3.13 to classifiers + CI matrix (3.13 GA was 2025-10).
- **P1:** Missing `py.typed` marker file at `sdk-python/cloudmorph/py.typed`.
- **P1:** Missing `extras_require`:
  ```toml
  [project.optional-dependencies]
  anthropic = ["anthropic>=0.40.0"]
  openai = ["openai>=1.50.0"]
  bedrock = ["boto3>=1.34.0"]
  langchain = ["langchain-core>=0.3.0"]
  llamaindex = ["llama-index-core>=0.12.0"]
  pydantic_ai = ["pydantic-ai>=0.0.20"]
  async = ["httpx>=0.27.0"]
  all = ["cloudmorph[anthropic,openai,bedrock,langchain,llamaindex,pydantic_ai,async]"]
  ```
- **P1:** Repository URL says `cloudmorph-control-center` but the actual repo's git remote (per `cloudmorph-mcp/package.json`) is `cloudmorph-mcp`. Pick one. If the SDK is going to live in a sibling repo (`cloudmorph-sdk-python`), say so.

---

## 3.2 The 3-line firewall promise

Today (raw client):
```python
from cloudmorph import CloudMorphClient
client = CloudMorphClient(token="cm_...")
result = client.request_and_wait("aws.s3.list_buckets")
```

Target (firewall integration):
```python
from cloudmorph import firewall
firewall.wrap(my_agent)        # auto-intercepts tool calls
# OR
@firewall.govern(intent="audit s3 public access")
def list_buckets(): ...
# OR (framework-native)
from cloudmorph.adapters.anthropic import GovernedAnthropic
client = GovernedAnthropic(api_key="...", cm_token="cm_...")
```

The gap is that there is no `firewall` module and no `adapters/`. SDK is purely a transport client.

---

## 3.3 Patterns to add

Three integration patterns, each fitting a different agent shape:

### 3.3.1 MCP-proxy pattern (primary)

For agents that already speak MCP (Cursor, Claude Desktop, Codex, custom MCP-client agents):

```python
from cloudmorph import firewall
firewall.start_proxy(
    cm_token="cm_...",
    upstream_mcp_url="http://localhost:3001",   # downstream MCP we're wrapping
    listen_port=3000,                            # local stdio/HTTP we expose to agent
)
```

Internally: spawns a local stdio MCP server (uses `mcp` Python package or shells to a Node process running `cloudmorph-mcp` in stdio mode pointed at `cm_token`+`upstream_mcp_url`). Zero changes in the agent's code — just point its MCP config at `localhost:3000` instead of the real downstream.

**Effort:** 12h. Block F. Depends on `cloudmorph_proxy` MCP tool (Block D).

### 3.3.2 Decorator pattern

For agents driving raw `Anthropic().messages.create(..., tools=[...])` loops or any tool-call dispatcher under their control:

```python
from cloudmorph import firewall

@firewall.govern(token="cm_...")
def execute_tool(tool_name: str, tool_args: dict) -> dict:
    """The agent's tool-dispatch function. Wrapped, every call is policy-evaluated."""
    if tool_name == "list_s3_buckets":
        return aws.s3.list_buckets(...)
    ...
```

Decorator wraps the function: pre-call → declares intent (or uses prior declaration), pre-call → calls Control Centre with `(tool_name, tool_args, intent_id, runtime_context)`, on `allow` → calls original; on `deny`/`mutate`/`approve`/`redact` → handles per decision; post-call → emits audit event.

**Effort:** 6h. Block F.

### 3.3.3 Middleware pattern

For frameworks with first-class middleware:

- LangChain: subclass `BaseCallbackHandler`, hook `on_tool_start` / `on_tool_end`.
- LlamaIndex: query-engine wrapper that intercepts tool router decisions.
- Pydantic AI: `Tool` wrapper or `Agent`-level callback.

```python
from langchain_core.callbacks import BaseCallbackHandler
from cloudmorph.adapters.langchain import CloudMorphCallback

agent = create_react_agent(model, tools=[...], callbacks=[CloudMorphCallback(token="cm_...")])
```

**Effort:** 4h per framework. Block F. LangChain + LlamaIndex in MVP, others post-MVP.

---

## 3.4 Framework adapters

| Adapter | Module path | Pattern | Effort | MVP? |
|---|---|---|---:|:-:|
| Anthropic | `cloudmorph.adapters.anthropic` | Decorator on `client.messages.create`; intercepts tool-use blocks; auto-declares intent from system prompt | 8h | ✓ |
| OpenAI | `cloudmorph.adapters.openai` | Decorator on `chat.completions.create` with `tools=`; same intent flow | 6h | ✓ |
| Bedrock | `cloudmorph.adapters.bedrock` | Wrap `boto3.client("bedrock-runtime").invoke_model` with tool-use parsing | 8h | post-MVP |
| LangChain | `cloudmorph.adapters.langchain` | `CloudMorphCallback(BaseCallbackHandler)` | 6h | ✓ stretch |
| LlamaIndex | `cloudmorph.adapters.llamaindex` | Query-engine wrapper | 6h | ✓ stretch |
| Pydantic AI | `cloudmorph.adapters.pydantic_ai` | `Tool` wrapper | 4h | post-MVP |
| CrewAI | `cloudmorph.adapters.crewai` | Custom Agent wrapper | 6h | post-MVP |
| AutoGen | `cloudmorph.adapters.autogen` | Custom Agent wrapper | 6h | post-MVP |
| Cohere | `cloudmorph.adapters.cohere` | `chat` wrapper with tool parsing | 4h | post-MVP |

**Anthropic adapter is the highest priority.** Most design partners likely to be anthropic-native.

### Anthropic adapter sketch

```python
# cloudmorph/adapters/anthropic.py
from anthropic import Anthropic, AnthropicError
from anthropic.types import ToolUseBlock, ToolResultBlock
from cloudmorph import CloudMorph, firewall
from cloudmorph.contracts import IntentDeclaration

class GovernedAnthropic:
    """Drop-in replacement for anthropic.Anthropic that policy-evaluates every tool use."""
    def __init__(self, api_key: str, cm_token: str, *, intent_extractor=None, **kwargs):
        self._client = Anthropic(api_key=api_key, **kwargs)
        self._cm = CloudMorph(token=cm_token)
        self._intent_extractor = intent_extractor or default_intent_extractor

    def messages_create(self, *, model, messages, tools=None, system=None, **kwargs):
        # 1. Extract intent from system prompt + first user message (LLM-judge or heuristic).
        intent = self._intent_extractor(system=system, messages=messages, tools=tools)
        intent_id = self._cm.declare_intent(intent)

        # 2. Pass through to Anthropic.
        response = self._client.messages.create(
            model=model, messages=messages, tools=tools, system=system, **kwargs
        )

        # 3. For each tool_use block, evaluate via Control Centre.
        for block in response.content:
            if isinstance(block, ToolUseBlock):
                decision = self._cm.evaluate_tool_call(
                    tool_name=block.name,
                    tool_args=block.input,
                    intent_id=intent_id,
                )
                # ... handle allow/deny/approve/mutate/redact ...
        return response
```

The trick is `_intent_extractor`. MVP default: heuristic (parse the system prompt for verbs, map to structured verbs). Post-MVP: LLM-judge mode that calls a small model (Haiku 4.5) to extract structured verbs from arbitrary system prompts.

---

## 3.5 Async & streaming

Today: sync only. Adoption blocker for async-first agents (most modern ones).

```python
# Add: cloudmorph/async_client.py
import httpx
from cloudmorph.client import CloudMorph

class AsyncCloudMorph:
    """Async variant. Uses httpx instead of urllib."""
    def __init__(self, token, base_url=None, timeout=60):
        self.token = token
        self.base_url = (base_url or CloudMorph.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._rpc_id = 0

    async def request(self, action, *, targets=None, payload=None, ...): ...
    async def request_and_wait(self, action, ...): ...
    async def declare_intent(self, intent: IntentDeclaration) -> str: ...
    async def evaluate_tool_call(self, tool_name, tool_args, intent_id): ...
    async def aclose(self): await self._client.aclose()

    async def __aenter__(self): return self
    async def __aexit__(self, *exc): await self.aclose()
```

`httpx` is the right choice over `aiohttp`: same API for sync and async, HTTP/2 support, smaller surface.

**Streaming decisions:** `cloudmorph_request{wait=true}` can SSE-stream status updates instead of long-poll-then-fallback-poll. Post-MVP: add `stream_request(action, ...) → AsyncIterator[StatusUpdate]`.

**Effort:** 8h for `AsyncCloudMorph` (basic). Block F. Streaming is post-MVP.

---

## 3.6 Packaging

| Item | Severity | Effort |
|---|---|---:|
| `extras_require` per framework | P1 | 1h |
| `py.typed` marker | P1 | 30min |
| Type stubs for everything (already typed via inline) | P2 | 4h |
| GitHub Actions release on tag → PyPI | P1 | 3h |
| Sigstore signing of releases | P2 | 2h |
| Python 3.9-3.13 matrix in CI | P1 | 1h |
| `Development Status :: 4 - Beta` | P1 | 5min |
| `cloudmorph-firewall` extras meta-package (post-v1) | P2 | 2h |
| Pin contract `schemaVersion` SDK targets | P1 | 2h |

PyPI release flow:
```yaml
# .github/workflows/release.yml
on:
  push:
    tags: ["sdk-v*"]
jobs:
  release:
    permissions: { id-token: write }   # for sigstore + trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1   # trusted publisher, no token
```

Pre-tag: `python -m pytest tests/test_python_sdk.py && python -m build && twine check dist/*`. CI gates on these.

---

## 3.7 Documentation gaps

- **README in [sdk-python/README.md](../../sdk-python/README.md)** — exists but modified (worktree). Audit not done this pass; cover in Block I along with the rest of `docs/`.
- **No `examples/`** — should ship one example per pattern (proxy, decorator, anthropic adapter).
- **No type stub examples** — show `mypy --strict` clean usage.
- **No async example** — when `AsyncCloudMorph` lands.

---

## 3.8 Test coverage

[tests/test_python_sdk.py](../../tests/test_python_sdk.py) (122 LoC) — covers:
- `__init__` validation (token required, base_url defaults, base_url stripping)
- `_is_terminal` static helper
- Mocked `urlopen` happy path for `request`
- Mocked `urlopen` happy path for `request_and_wait` no-wait variant
- RPC-error → `CloudMorphError`

**Missing:**
- 429 → `RateLimitError` with `Retry-After` parsing
- HTTPError code paths (4xx, 5xx) → `CloudMorphError` with right `status`
- URLError → `CloudMorphError` with `code="connection_error"`
- `request_and_wait` polling loop
- Both `targets=` and `account_id=` paths in `request`
- The `code = err.get("message")` bug (lock the broken behavior in a test, fix the test when fixing the bug — pin the change)
- Type stubs (`mypy --strict tests/`)
- Doctest examples in module docstrings

**Coverage target:** 90% line / 85% branch on `cloudmorph/client.py`. Block F.

When `AsyncCloudMorph` and adapters land, a separate test module per:
- `tests/test_python_sdk_async.py`
- `tests/test_adapter_anthropic.py` (use `responses` or `httpx_mock`)
- `tests/test_adapter_openai.py`
- `tests/test_firewall_decorator.py`
- `tests/test_firewall_proxy.py`

---

## 3.9 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Fix `CloudMorphError.code` mapping bug | P1 | 2h | F |
| `firewall.wrap()` MCP-proxy primary integration | P0 | 12h | F |
| `firewall.govern` decorator | P0 | 6h | F |
| Anthropic adapter | P0 | 8h | F |
| OpenAI adapter | P1 | 6h | F |
| `AsyncCloudMorph` (httpx) | P1 | 8h | F |
| LangChain adapter | P1 | 6h | F (stretch) |
| LlamaIndex adapter | P1 | 6h | F (stretch) |
| `extras_require` packaging | P1 | 1h | F |
| `py.typed` marker | P1 | 30min | F |
| Validate `wait_seconds ≤ 55` client-side | P1 | 1h | F |
| Configurable poll/timeout class defaults | P2 | 1h | F |
| Bedrock adapter | P2 | 8h | post-MVP |
| Pydantic AI adapter | P2 | 4h | post-MVP |
| CrewAI / AutoGen / Cohere adapters | P2 | 16h | post-MVP |
| Streaming decisions via SSE | P2 | 6h | post-MVP |
| Test coverage to 90% | P0 | 8h | F |
| GitHub Actions PyPI release flow | P1 | 3h | F |
| Sigstore signing | P2 | 2h | post-MVP |
| Documentation site / examples/ | P1 | 6h | I |

**MVP critical-path total: ~50h (one engineer ~6 days).** Tight for Block F but achievable if the proxy pattern (12h) and Anthropic adapter (8h) get the lion's share.

---

## 3.10 Out of scope

- Sync→async magic (calling sync from async or vice versa). Two clear classes is better than one cute one.
- Auto-discovery of `cm_token` from env var. *Maybe* `CLOUDMORPH_TOKEN` after v1 — but MVP forces explicit pass for clarity.
- Browser-targeted SDK (TypeScript/JS for the web). The TypeScript SDK referenced in [docs/getting-started.md](../../docs/getting-started.md:34) does not exist; either build it post-MVP or remove the reference.

---

## 3.11 Source links

- [sdk-python/cloudmorph/__init__.py](../../sdk-python/cloudmorph/__init__.py)
- [sdk-python/cloudmorph/client.py](../../sdk-python/cloudmorph/client.py)
- [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml)
- [tests/test_python_sdk.py](../../tests/test_python_sdk.py)

Implementation: Block F. Depends on Block B (contracts), Block D (MCP server tools to wrap).
