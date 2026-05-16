"""Stub GitHub MCP upstream for local testing.

Run on port 7000:
    python mock_github_mcp.py

Tessera proxies tool calls to this server via tessera.example.yaml.
Responds to tools/call and tools/list; returns canned JSON-RPC results.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

_TOOLS = [
    {
        "name": "github_create_issue",
        "description": "Create a GitHub issue in a repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["title", "repo"],
        },
    },
    {
        "name": "github_delete_issue",
        "description": "Delete a GitHub issue. Destructive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "issue_number": {"type": "integer"},
                "repo": {"type": "string"},
            },
            "required": ["issue_number", "repo"],
        },
    },
]


@app.post("/")
async def handle(request: Request) -> JSONResponse:
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 1)

    if method == "tools/list":
        return JSONResponse(
            {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _TOOLS}}
        )

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "unknown")
        arguments = params.get("arguments", {})
        # Stub response — echo back tool name and arguments for easy inspection
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"[mock] {tool_name} called with {arguments}",
                        }
                    ]
                },
            }
        )

    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "mock-github-mcp", "version": "0.1.0"},
                },
            }
        )

    if method == "ping":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7000)
