"""Bounded loopback receiver; no token exchange, cloud calls, or persistence.

Use ``with EphemeralOAuthReceiver(timeout=300) as receiver``; navigate locally
only after the health probe is verified. ``wait_for_callback()`` returns private
code/verifier fields only after the query-free result page was served. The caller
must discard that returned object after use. Clearing references cannot guarantee
that immutable Python strings or operating-system buffers are overwritten.

``--probe-only`` serves public health data only and never constructs OAuth data.
Importing this module starts no listener. Request/error logs are always suppressed.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit

_AUTHORIZE = "https://auth.tessera.cloudmorph.ai/oauth/authorize"
_SCOPE = "tessera:policies:read tessera:audit:write"
_MAX_REQUESTS = 16
_RESULT = b"<!doctype html><title>Tessera</title><p>Return to the local operator.</p>"
_REJECT = b"Request not accepted."


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    """Secret-bearing fields deliberately do not appear in repr or CLI output."""

    outcome: str
    code: str | None = field(default=None, repr=False)
    verifier: str | None = field(default=None, repr=False)


class _Server(HTTPServer):
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, owner: _Loopback) -> None:
        self.owner: _Loopback | None = owner
        super().__init__(("127.0.0.1", 0), _Handler)
        self.timeout = 0.2

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, address = super().get_request()
        remaining = max(0.01, self.owner.deadline - time.monotonic()) if self.owner else 0.01
        connection.settimeout(min(2.0, remaining))
        return connection, address

    def finish_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        # Absolute per-connection cap also stops slow byte-by-byte header sends.
        remaining = max(0.01, self.owner.deadline - time.monotonic()) if self.owner else 0.01

        def expire() -> None:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(min(2.0, remaining), expire)
        timer.daemon = True
        timer.start()
        try:
            super().finish_request(request, client_address)
        finally:
            timer.cancel()
            timer.join()

    def verify_request(self, request: socket.socket, client_address: tuple[str, int]) -> bool:
        if self.owner is None:
            return False
        self.owner.requests += 1
        return (
            client_address[0] == "127.0.0.1"
            and self.owner.requests <= _MAX_REQUESTS
            and time.monotonic() < self.owner.deadline
        )

    def handle_error(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        pass  # Never print exception/request contents.


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def log_error(self, format: str, *args: object) -> None:
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._respond(400, _REJECT)

    def _respond(self, status: int, body: bytes, location: str | None = None) -> None:
        self.close_connection = True
        self.send_response_only(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        owner = self.server.owner
        if owner is None:
            return
        if (
            self.headers.get_all("Host") != [owner.host]
            or len(self.path) > 2048
            or sum(len(k) + len(v) for k, v in self.headers.items()) > 8192
            or self.headers.get_all("Transfer-Encoding")
            or self.headers.get_all("Content-Length") not in (None, ["0"])
        ):
            self._respond(400, _REJECT)
            return
        owner.receive(self)

    def _method_rejected(self) -> None:
        self._respond(405, _REJECT)

    do_HEAD = _method_rejected  # noqa: N815 - stdlib handler API
    do_POST = _method_rejected  # noqa: N815 - stdlib handler API
    do_PUT = _method_rejected  # noqa: N815 - stdlib handler API
    do_PATCH = _method_rejected  # noqa: N815 - stdlib handler API
    do_DELETE = _method_rejected  # noqa: N815 - stdlib handler API
    do_OPTIONS = _method_rejected  # noqa: N815 - stdlib handler API
    do_TRACE = _method_rejected  # noqa: N815 - stdlib handler API
    do_CONNECT = _method_rejected  # noqa: N815 - stdlib handler API


class _Loopback:
    def __init__(self, timeout: int) -> None:
        if type(timeout) is not int or not 300 <= timeout <= 360:
            raise ValueError("RECEIVER_DEADLINE_INVALID")
        self.deadline = time.monotonic() + timeout
        self.requests = 0
        self.server: _Server | None = _Server(self)
        self.port = self.server.server_port
        self.host = f"127.0.0.1:{self.port}"
        self.origin = f"http://{self.host}"
        self.failure: str | None = None
        self.complete = False

    def receive(self, handler: _Handler) -> None:
        raise NotImplementedError

    def pump(self) -> bool:
        if self.server is None or self.complete or self.failure:
            return False
        if self.requests >= _MAX_REQUESTS:
            self.failure = "REQUEST_LIMIT"
            return False
        if time.monotonic() >= self.deadline:
            self.failure = "TIMED_OUT"
            return False
        self.server.handle_request()
        return True

    def close(self) -> None:
        if self.server is not None:
            self.server.server_close()
            self.server.owner = None
            self.server = None


class EphemeralOAuthReceiver(_Loopback):
    """One context entry, one bind, one callback; all sensitive state stays in RAM."""

    def __init__(self, timeout: int = 300) -> None:
        # Bind only on context entry, never during construction/import.
        self._timeout = timeout
        self._entered = False
        self._waited = False
        self._code: str | None = None
        self._state: str | None = None
        self._verifier: str | None = None
        self._authorization_url: str | None = None
        self._redirect_uri: str | None = None
        self.server = None

    def __enter__(self) -> EphemeralOAuthReceiver:
        if self._entered:
            raise RuntimeError("RECEIVER_REENTRY_FORBIDDEN")
        self._entered = True
        try:
            super().__init__(self._timeout)
            self._verifier = secrets.token_urlsafe(32)
            self._state = secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode("ascii")).digest())
            self._redirect_uri = self.origin + "/callback"
            self._authorization_url = _AUTHORIZE + "?" + urlencode({
                "response_type": "code", "client_id": "tessera-cli", "scope": _SCOPE,
                "redirect_uri": self._redirect_uri, "state": self._state,
                "code_challenge": challenge.rstrip(b"=").decode("ascii"), "code_challenge_method": "S256",
            })
        except Exception:
            self.close()
            raise
        return self

    @property
    def authorization_url(self) -> str:
        if self._authorization_url is None:
            raise RuntimeError("RECEIVER_NOT_ACTIVE")
        return self._authorization_url

    @property
    def redirect_uri(self) -> str:
        if self._redirect_uri is None:
            raise RuntimeError("RECEIVER_NOT_ACTIVE")
        return self._redirect_uri

    def receive(self, handler: _Handler) -> None:
        if handler.path == "/result" and self._code is not None:
            handler._respond(200, _RESULT)
            self.complete = True
            return
        parsed = urlsplit(handler.path)
        if parsed.path != "/callback" or parsed.scheme or parsed.netloc or parsed.fragment:
            handler._respond(404, _REJECT)
            return
        if self._code is not None:
            handler._respond(400, _REJECT)
            return
        try:
            fields = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                               encoding="ascii", errors="strict", max_num_fields=2)
            values = dict(fields)
            if (
                len(fields) != 2 or set(values) != {"code", "state"}
                or not re.fullmatch(r"[0-9a-f]{64}", values["code"])
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", values["state"])
                or self._state is None or not secrets.compare_digest(values["state"], self._state)
            ):
                raise ValueError("INVALID_CALLBACK")
        except ValueError:
            self.failure = "INVALID_CALLBACK"
            handler._respond(400, _REJECT)
            return
        self._code = values["code"]
        handler._respond(303, b"", self.origin + "/result")

    def wait_for_callback(self) -> CallbackOutcome:
        if not self._entered or self.server is None or self._waited:
            raise RuntimeError("RECEIVER_WAIT_FORBIDDEN")
        self._waited = True
        try:
            while self.pump():
                pass
            if self.complete and self._code is not None and self._verifier is not None:
                return CallbackOutcome("VALIDATED_CALLBACK", self._code, self._verifier)
            status = "RESULT_NOT_SERVED" if self._code is not None else (self.failure or "TIMED_OUT")
            return CallbackOutcome(status)
        finally:
            self.close()

    def close(self) -> None:
        super().close()
        self._code = self._state = self._verifier = self._authorization_url = None

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


class _HealthProbe(_Loopback):
    def __init__(self) -> None:
        super().__init__(300)
        self.probe_id = secrets.token_urlsafe(16)
        self.path = "/healthz/" + self.probe_id
        self.self_nonce: str | None = secrets.token_urlsafe(32)
        self.self_complete = False
        self.body = json.dumps({"status": "ready", "probeId": self.probe_id}).encode("ascii")

    def receive(self, handler: _Handler) -> None:
        if handler.path != self.path:
            handler._respond(404, _REJECT)
            return
        supplied = handler.headers.get_all("X-Tessera-Health-Self")
        if not self.self_complete:
            if supplied != [self.self_nonce]:
                handler._respond(400, _REJECT)
                return
            handler._respond(200, self.body)
            self.self_complete = True
        elif supplied is None:
            handler._respond(200, self.body)
            self.complete = True
        else:
            handler._respond(400, _REJECT)


def _probe_only() -> int:
    probe = _HealthProbe()
    self_ok: list[bool] = []

    def self_read() -> None:
        connection = http.client.HTTPConnection("127.0.0.1", probe.port, timeout=2)
        try:
            connection.request("GET", probe.path, headers={"X-Tessera-Health-Self": probe.self_nonce})
            response = connection.getresponse()
            self_ok.append(response.status == 200 and response.read(1024) == probe.body)
        except (OSError, http.client.HTTPException):
            self_ok.append(False)
        finally:
            connection.close()

    thread = threading.Thread(target=self_read, daemon=True)
    try:
        thread.start()
        while not probe.self_complete and thread.is_alive() and probe.pump():
            pass
        thread.join(3)
        if self_ok != [True] or thread.is_alive():
            probe.failure = "SELF_HEALTH_FAILED"
        else:
            probe.self_nonce = None
            print(json.dumps({"status": "READY", "healthURL": probe.origin + probe.path,
                              "probeId": probe.probe_id, "port": probe.port}), flush=True)
            while probe.pump():
                pass
    finally:
        probe.self_nonce = None
        probe.close()
    try:
        with socket.create_connection(("127.0.0.1", probe.port), timeout=0.25):
            closed = False
    except ConnectionRefusedError:
        closed = True
    except OSError:
        closed = False
    passed = probe.complete and closed and self_ok == [True]
    print(json.dumps({"status": "PASS" if passed else "FAIL", "browserHealthServed": probe.complete,
                      "selfHealthServed": self_ok == [True], "portClosed": closed}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["--probe-only"]:
        print('{"status":"FAIL","code":"PROBE_ONLY_ARGUMENT_REQUIRED"}', flush=True)
        raise SystemExit(2)
    try:
        raise SystemExit(_probe_only())
    except Exception:
        print('{"status":"FAIL","code":"PROBE_LOCAL_FAILURE"}', flush=True)
        raise SystemExit(1) from None
