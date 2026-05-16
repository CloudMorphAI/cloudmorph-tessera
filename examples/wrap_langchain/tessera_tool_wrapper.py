"""MCPToolNode — LangChain Tool subclass that forwards calls through Tessera."""

from __future__ import annotations

import httpx
from langchain.tools import Tool


def build_tessera_tools(
    upstreams: list[str],
    tessera_base: str,
    bearer_token: str,
) -> list[Tool]:
    """Return a list of LangChain Tools, one per Tessera-proxied upstream.

    Each Tool POSTs to {tessera_base}/mcp/{upstream} with a JSON-RPC tools/call body.
    Tessera's policies decide allow/block; the Tool surfaces success or the
    structured error back to the LangChain agent.
    """
    tools = []
    for upstream in upstreams:

        def make_tool(u: str) -> Tool:
            def _call(tool_input: str) -> dict:
                # LangChain passes a single string argument to func.
                # Callers encode tool_name and arguments as "tool_name|json_arguments".
                # Example: "github_create_issue|{\"title\": \"hello\"}"
                if "|" in tool_input:
                    tool_name, _, raw_args = tool_input.partition("|")
                    import json

                    arguments = json.loads(raw_args) if raw_args.strip() else {}
                else:
                    tool_name = tool_input.strip()
                    arguments = {}

                resp = httpx.post(
                    f"{tessera_base}/mcp/{u}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                    headers={"Authorization": f"Bearer {bearer_token}"},
                    timeout=30,
                )
                return resp.json()

            return Tool(
                name=f"tessera_{u}",
                description=(
                    f"Call {u} tools via the Tessera firewall. "
                    f"Input format: '<tool_name>|<json_arguments>' "
                    f"e.g. 'github_create_issue|{{\"title\": \"my issue\"}}'"
                ),
                func=_call,
            )

        tools.append(make_tool(upstream))
    return tools
