# Anthropic SDK + Tessera — end-to-end example

This example shows how to put Tessera in front of an MCP upstream when Claude
is driving tool calls through the Anthropic Python SDK. All tool requests pass
through Tessera, which evaluates them against your policies before forwarding
or blocking.

## What this example demonstrates

- Claude asks to create a GitHub issue — Tessera allows it, the mock server responds.
- Claude asks to delete a GitHub issue — Tessera blocks it based on
  `policies/block-destructive-issues.yaml`, the request never reaches the mock server.
- The full audit trail is written to `tessera-audit.db` and to stdout in real time.

```
anthropic Claude  →  MCP tool call  →  Tessera (policy check)  →  mock-github-mcp
                                             ↓ block
                                       JSON-RPC -32603 (reason in response)
```

## Prerequisites

```bash
pip install cloudmorph-tessera anthropic fastapi uvicorn
```

Requires Python 3.11+ and `anthropic>=0.40.0` (for `mcp_servers` support on
`messages.create`). The mock server uses `fastapi` and `uvicorn`.

You also need a valid `ANTHROPIC_API_KEY` in your environment.

## Steps

### 1. Start the mock GitHub MCP server (terminal 1)

```bash
cd examples/wrap_anthropic_sdk
python mock_github_mcp.py
# Listening on http://localhost:7000
```

The mock responds to `tools/list`, `tools/call`, and `initialize` without
any real GitHub credentials.

### 2. Start Tessera (terminal 2)

```bash
cd examples/wrap_anthropic_sdk
tessera serve --config tessera.example.yaml
# Tessera listening on http://localhost:8080
# Policies loaded from ./policies/ (enforcement mode)
```

### 3. Run the client (terminal 3)

```bash
cd examples/wrap_anthropic_sdk
export ANTHROPIC_API_KEY=sk-ant-...
export TESSERA_BEARER_TOKEN=dev-token   # matches tessera.example.yaml auth.type: bearer
python client.py
```

### 4. Watch the audit log (optional — any terminal)

```bash
tessera audit tail --follow --audit-path ./tessera-audit.db
```

## Expected output

**Allowed call (create issue)**

```
TextBlock(text="I've created the issue 'test from tessera' in cloudmorph/demo.")
```

In the Tessera log you'll see:
```
{"event":"decision","action":"allow","tool":"github_create_issue",...}
```

**Blocked call (delete issue)**

Claude receives a JSON-RPC error from Tessera:
```
{"jsonrpc":"2.0","error":{"code":-32603,"message":"Issue deletion is blocked by
policy. Use the GitHub UI for destructive operations."},"id":...}
```

In the Tessera log:
```
{"event":"decision","action":"block","tool":"github_delete_issue",
 "policy":"block-destructive-issues","reason":"Issue deletion is blocked by policy..."}
```

## How the policy works

`policies/block-destructive-issues.yaml` matches the `github_delete_issue` tool
on the `github` upstream and blocks unconditionally. Tessera never forwards the
request to the mock server.

To extend it — for example, to also block `github_close_issue` — add another
entry or use a glob:

```yaml
match:
  upstream: github
  tool: "github_*_issue"   # glob matches create, delete, close, reopen ...
when:
  - condition: tool_name_in
    values: ["github_delete_issue", "github_close_issue"]
action: block
```

See the full condition catalog in the
[Tessera policy reference](../../docs/POLICIES.md).

## Connecting to a real GitHub MCP server

Replace `http://localhost:7000` in `tessera.example.yaml` with your actual
GitHub MCP server URL and add a `credentials` block:

```yaml
upstreams:
  - name: github
    url: https://your-github-mcp-server.example.com
    credentials:
      header: Authorization
      value: "Bearer ${GITHUB_MCP_TOKEN}"
```

Set `GITHUB_MCP_TOKEN` in your environment before starting Tessera.

## Next steps

- Try the [OpenAI SDK example](../wrap_openai_sdk/) for the same flow with
  `openai.beta.tools`.
- Try the [LangChain example](../wrap_langchain/) to route a LangChain agent
  through Tessera.
