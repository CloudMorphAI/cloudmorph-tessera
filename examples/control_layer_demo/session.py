"""Explicit, session-only public-client PKCE login; never reads saved credentials."""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx


def session_login(endpoint: str, *, timeout: int = 300) -> str:
    """Print a nonsecret authorization URL; keep callback/code/tokens in RAM."""
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "auth.tessera.cloudmorph.ai" or parsed.path not in ("", "/"):
        raise ValueError("session login requires the approved Tessera authorization origin")
    endpoint = endpoint.rstrip("/")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    captured: dict[str, str] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request = urlsplit(self.path)
            if request.path == "/complete" and not request.query and captured:
                captured["complete"] = "true"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Authorization received. Return to the local control-layer session.")
                return
            query = parse_qs(request.query)
            valid = request.path == "/callback" and query.get("state") == [state]
            if valid and len(query.get("code", [])) == 1:
                captured["code"] = query["code"][0]
            elif valid and query.get("error"):
                captured["error"] = "authorization_denied"
            self.send_response(303 if valid else 400)
            if valid:
                self.send_header("Location", "/complete")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    with http.server.HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        redirect = f"http://localhost:{server.server_port}/callback"
        url = endpoint + "/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": "tessera-cli", "redirect_uri": redirect,
            "scope": "tessera:policies:read tessera:audit:write", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        print("Open this authorization URL in the approved signed-in browser:\n" + url, flush=True)
        deadline = time.monotonic() + min(max(timeout, 1), 600)
        while not captured.get("complete") and time.monotonic() < deadline:
            server.handle_request()
            if captured and not captured.get("complete"):
                deadline = min(deadline, time.monotonic() + 3)
    if not captured.get("code"):
        raise RuntimeError("session authorization was denied or timed out")
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(endpoint + "/oauth/token", data={
            "grant_type": "authorization_code", "client_id": "tessera-cli",
            "code": captured.pop("code"), "redirect_uri": redirect, "code_verifier": verifier,
        })
        if response.status_code != 200:
            raise RuntimeError("session token exchange failed")
        payload = response.json()
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("session token exchange returned no access token")
    # No refresh token persistence or automated refresh/retry in this bounded run.
    payload.clear()
    return token
