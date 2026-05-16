"""Minimal example: Anthropic Claude tool-use -> Tessera firewall -> MCP upstream.

The Tessera proxy intercepts Claude's tool-call requests, evaluates them against
the policies in ./policies/, and either forwards or blocks. This file uses
anthropic-python's MCP server URL feature (requires anthropic>=0.40.0).

Request path:
    anthropic Claude  ->  MCP tool call  ->  Tessera (policy check)  ->  mock-github-mcp

Run:
    pip install cloudmorph-tessera anthropic fastapi uvicorn
    python mock_github_mcp.py          # terminal 1 (mock upstream on :7000)
    tessera serve --config tessera.example.yaml  # terminal 2 (Tessera on :8080)
    export TESSERA_BEARER_TOKEN=dev-token
    python client.py                   # terminal 3
"""

from __future__ import annotations

import os

from anthropic import Anthropic

# Tessera listens on :8080 by default.  All tool calls Claude makes to the
# "github" MCP tool are routed through Tessera's /mcp/github endpoint, where
# they are evaluated against ./policies/ before being forwarded to localhost:7000.
TESSERA_BASE = os.environ.get("TESSERA_BASE", "http://localhost:8080")
GITHUB_MCP_URL = f"{TESSERA_BASE}/mcp/github"

TESSERA_TOKEN = os.environ.get("TESSERA_BEARER_TOKEN", "dev-token")

client = Anthropic()

# anthropic-python >= 0.40 supports MCP server definitions directly on the
# messages.create call.  The SDK negotiates the MCP handshake, fetches the
# tool list from Tessera, and includes those tools in the Claude request.
#
# NOTE: if you are on an older anthropic-python, replace the `mcp_servers`
# block with a hand-crafted `tools` list using the tool definitions returned
# by `curl http://localhost:8080/mcp/github` after running tools/list.
response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    mcp_servers=[
        {
            "type": "url",
            "url": GITHUB_MCP_URL,
            "name": "github",
            "headers": {"Authorization": f"Bearer {TESSERA_TOKEN}"},
        }
    ],
    messages=[
        {
            "role": "user",
            "content": "Create an issue titled 'test from tessera' in cloudmorph/demo, "
                       "then try to delete issue #1.",
        }
    ],
)

for block in response.content:
    print(block)

# Expected output (success + block):
#
#   TextBlock(text="I created issue 'test from tessera' in cloudmorph/demo (mock id #42).")
#
#   The delete attempt is intercepted by Tessera before reaching the mock server:
#   ToolUseBlock(name='github_delete_issue', ...) -> Tessera returns JSON-RPC -32603
#   "Issue deletion is blocked by policy. Use the GitHub UI for destructive operations."
