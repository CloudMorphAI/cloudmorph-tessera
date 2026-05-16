"""Minimal mock GitHub MCP server for local end-to-end testing.

Listens on http://localhost:7000.  Responds to the three MCP methods that
client.py exercises:  initialize, tools/list, and tools/call.

Run with:
    pip install fastapi uvicorn
    python mock_github_mcp.py
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

TOOLS = [
    {
        "name": "github_create_issue",
        "description": "Create a new GitHub issue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["repo", "title"],
        },
    },
    {
        "name": "github_list_issues",
        "description": "List open issues in a repository.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    },
    {
        "name": "github_delete_issue",
        "description": "Delete a GitHub issue by number. Destructive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
            },
            "required": ["repo", "issue_number"],
        },
    },
]


@app.post("/")
async def handle(request: Request) -> JSONResponse:
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 1)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mock-github-mcp", "version": "0.0.1"},
            },
        })

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})

    if method == "tools/call":
        tool = body.get("params", {}).get("name", "")
        args = body.get("params", {}).get("arguments", {})
        if tool == "github_create_issue":
            text = f"Created issue '{args.get('title')}' in {args.get('repo')} (mock id #42)"
        elif tool == "github_list_issues":
            text = f"Open issues in {args.get('repo')}: #1 'bug', #2 'feature request'"
        elif tool == "github_delete_issue":
            # Should never reach here when Tessera enforcement is active.
            text = f"Deleted issue #{args.get('issue_number')} from {args.get('repo')}"
        else:
            text = f"Unknown tool: {tool}"
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": text}]},
        })

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
