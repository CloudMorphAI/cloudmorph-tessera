# LangChain Agent via Tessera

This example shows a LangChain agent routing every MCP tool call through Tessera's
policy firewall. The agent uses a custom `MCPToolNode` wrapper
(`tessera_tool_wrapper.py`) that encapsulates the JSON-RPC `tools/call` shape, so
every LangChain Tool invocation is automatically auditable and policy-gated.

## Why a custom Tool wrapper?

LangChain's built-in `RequestsTool` could forward HTTP calls, but it exposes a raw
`requests.get/post` interface — the caller would have to construct the full JSON-RPC
envelope every time. The `build_tessera_tools` factory in `tessera_tool_wrapper.py`
encodes that envelope once and returns clean `Tool` objects the agent can call by name.

LangChain's value here: chaining multiple tools, built-in retry logic, and scratchpad
reasoning. Tessera plugs in transparently — the agent never knows whether a call was
allowed or blocked until it reads the response.

## Prerequisites

- Python 3.12+
- Tessera installed: `pip install cloudmorph-tessera`
- LangChain + Anthropic provider:

  ```bash
  pip install langchain langchain-anthropic httpx
  ```

  If you prefer OpenAI, install `langchain-openai` instead and swap
  `ChatAnthropic` for `ChatOpenAI(model="gpt-4o")` in `agent.py`.

- `ANTHROPIC_API_KEY` set in your environment (or `OPENAI_API_KEY` if you swapped).

## Quickstart (3 terminals)

**Terminal 1 — mock GitHub MCP server (port 7000):**

```bash
cd examples/wrap_langchain
pip install fastapi uvicorn
python mock_github_mcp.py
```

**Terminal 2 — Tessera (port 8080):**

```bash
cd examples/wrap_langchain
cp tessera.example.yaml tessera.yaml
export TESSERA_BEARER_TOKEN=dev-token-local
tessera serve --config tessera.yaml
```

**Terminal 3 — run the agent:**

```bash
cd examples/wrap_langchain
export TESSERA_BASE=http://localhost:8080
export TESSERA_BEARER_TOKEN=dev-token-local
export ANTHROPIC_API_KEY=<your-key>
python agent.py
```

## Expected behaviour

The agent calls `github_create_issue` through Tessera. Tessera evaluates the
`policies/block-destructive-issues.yaml` policy (which only blocks
`github_delete_issue`) and allows the create. You should see the mock response:

```
[mock] github_create_issue called with {'title': 'test from tessera', ...}
```

To see a block in action, change the agent input to ask for a deletion:

```python
result = executor.invoke({"input": "Delete issue #42 in cloudmorph/demo"})
```

Tessera returns a JSON-RPC `-32603` error with the policy reason. The agent
surfaces it in its scratchpad and reports failure to the caller.

## Swapping the LLM provider

To use OpenAI instead of Anthropic, edit `agent.py`:

```python
# Replace:
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-opus-4-7")

# With:
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4o")
```

Install `langchain-openai` and set `OPENAI_API_KEY`. Everything else stays the same —
Tessera doesn't care which LLM is driving the agent.

## Inspect the audit chain

```bash
tessera audit verify --audit-path ./tessera-audit.db
```

Every allow and block decision is recorded in the SQLite audit log with a verified
hash chain, regardless of which upstream tool was called.
