"""Minimal mock MCP upstream — returns 200 OK instantly for bench use."""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn


async def catch_all(request):
    return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}})


app = Starlette(routes=[Route("/{path:path}", catch_all, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8401, log_level="critical", access_log=False)
