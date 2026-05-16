"""Minimal example: OpenAI tools API → Tessera firewall → MCP upstream."""

from __future__ import annotations

import json
import os

import httpx
from openai import OpenAI

TESSERA_BASE = os.environ.get("TESSERA_BASE", "http://localhost:8080")

client = OpenAI()

# OpenAI tools API — function-tool shape.
# Each tool maps to an MCP tool served through Tessera.
tools = [
    {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Create a GitHub issue via Tessera-proxied MCP",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    }
]

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {
            "role": "user",
            "content": "Create an issue 'test from tessera' in cloudmorph/demo",
        }
    ],
    tools=tools,
)

# When the model returns tool_calls, dispatch each one through Tessera manually.
# OpenAI's API doesn't speak MCP natively — the SDK gives you a tool_call struct
# that you must forward yourself (unlike Anthropic's SDK, which can use the `url`
# tool type to speak MCP directly and skip this dispatch step).
if resp.choices[0].message.tool_calls:
    bearer = os.environ["TESSERA_BEARER_TOKEN"]
    for call in resp.choices[0].message.tool_calls:
        tessera_resp = httpx.post(
            f"{TESSERA_BASE}/mcp/github",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": call.function.name,
                    "arguments": json.loads(call.function.arguments),
                },
            },
            headers={"Authorization": f"Bearer {bearer}"},
        )
        print(tessera_resp.json())
