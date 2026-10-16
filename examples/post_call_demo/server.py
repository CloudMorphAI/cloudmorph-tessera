"""Loopback-only local synthetic demo UI. No hosted deployment or vendor adapters."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from policy_integration import (  # noqa: E402
    ContractError,
    PolicyGateway,
    policy_description,
    require_scenario,
    run_scripted,
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("Non-finite JSON")


class DemoService:
    def __init__(self, *, enable_nova=False, output_dir: Path | None = None):
        self.enable_nova = enable_nova
        self.output_dir = output_dir
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[str, int, dict[str, Any]]] = {}
        self.live_attempted = False
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

    def run(self, mode: str, key: str):
        if not self.lock.acquire(blocking=False):
            return 409, {"error": "A local run is already in progress"}
        try:
            if key in self.cache:
                prior_mode, status, receipt = self.cache[key]
                if prior_mode != mode:
                    return 409, {"error": "Idempotency key already used for a different mode"}
                return status, {**receipt, "replayed": True}
            if len(self.cache) >= 10:
                return 429, {"error": "Local ten-run session limit reached; restart explicitly to reset"}
            if mode == "nova" and (not self.enable_nova or self.live_attempted):
                return 409, {"error": "Nova disabled or its single allowed attempt is already consumed"}
            if mode == "nova":
                self.live_attempted = True  # A failed attempt cannot silently retry a billable call.
            run_id = "post-call-" + uuid.uuid4().hex
            try:
                with PolicyGateway(run_id) as gateway:
                    if mode == "scripted":
                        run_scripted(gateway)
                        metadata = {"model": None, "provider": None,
                                    "usage": {"input_tokens": 0, "output_tokens": 0, "model_calls": 0}}
                    else:
                        from model_runner import ModelRunError, run_model
                        try:
                            raw_metadata = run_model(gateway)
                        except Exception as exc:
                            evidence = gateway.finish()
                            reason = str(exc) if isinstance(exc, ModelRunError) else "Invalid model/gateway result"
                            raise FailedRunError(evidence, getattr(exc, "partial", {}), reason) from exc
                        raw_usage = raw_metadata.get("usage", {})
                        metadata = {**raw_metadata, "model": raw_metadata.get("model_id"),
                                    "usage": {**raw_usage, "model_calls": raw_usage.get("attempted_calls")}}
                    receipt = gateway.finish()
                require_scenario(receipt)
                receipt.update(metadata, mode=mode, replayed=False, synthetic=True,
                               policy_version=policy_version(), ui_version=ui_version())
                status = 200
            except FailedRunError as exc:
                status, receipt = 502, {"run_id": run_id, "mode": mode, "replayed": False,
                    "error": "The model run failed. No completed run is asserted; no automatic retry occurred.",
                    "failure_reason": exc.reason,
                    "partial_evidence": exc.evidence, "model_trace": exc.model_trace}
            except Exception:
                # Never return provider exception text or a fabricated successful transcript.
                status, receipt = 502, {"run_id": run_id, "mode": mode, "replayed": False,
                    "error": "The local run failed. No completed run is asserted; no automatic retry occurred."}
            self.cache[key] = (mode, status, receipt)
            if self.output_dir:
                (self.output_dir / f"{run_id}.json").write_text(
                    json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            return status, receipt
        finally:
            self.lock.release()


class FailedRunError(RuntimeError):
    def __init__(self, evidence, model_trace, reason):
        super().__init__("Incomplete model run")
        self.evidence = evidence
        self.model_trace = model_trace
        self.reason = reason


def policy_version():
    digest = hashlib.sha256()
    for path in sorted((HERE / "policies").glob("*.yaml")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ui_version():
    digest = hashlib.sha256()
    for filename in ("index.html", "app.js", "styles.css"):
        digest.update((HERE / "web" / filename).read_bytes())
    return digest.hexdigest()


def make_handler(service: DemoService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, status, payload, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_valid(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self._host_valid():
                self._send(403, {"error": "Loopback Host required"})
                return
            if self.path == "/api/health":
                self._send(200, {"status": "ready", "synthetic": True,
                    "modes": ["scripted"] + (["nova"] if service.enable_nova and not service.live_attempted else []),
                    "policy_admin_auth_verified": False})
            elif self.path == "/api/policy":
                self._send(200, {**policy_description(), "policy_version": policy_version()})
            elif self.path in ("/", "/index.html", "/app.js", "/styles.css"):
                filename = "index.html" if self.path == "/" else self.path[1:]
                content_type = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                                "styles.css": "text/css; charset=utf-8"}[filename]
                self._send(200, (HERE / "web" / filename).read_bytes(), content_type)
            else:
                self._send(404, {"error": "Unknown local endpoint"})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self._host_valid() or self.headers.get("Origin") != origin:
                self._send(403, {"error": "Exact loopback Origin and Host required"})
                return
            if self.path != "/api/run":
                self._send(404, {"error": "Unknown local endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1024 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Invalid request size/type")
                body = json.loads(self.rfile.read(length), object_pairs_hook=_object, parse_constant=_reject_constant)
                if not isinstance(body, dict) or set(body) != {"mode", "idempotency_key"}:
                    raise ValueError("Unexpected request shape")
                mode, key = body["mode"], body["idempotency_key"]
                if (mode not in ("scripted", "nova") or not isinstance(key, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key)
                        or key != self.headers.get("Idempotency-Key")):
                    raise ValueError("Invalid run parameters")
            except (ValueError, TypeError, ContractError):
                self._send(400, {"error": "Invalid fixed-scenario request"})
                return
            status, receipt = service.run(mode, key)
            self._send(status, receipt)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--enable-nova", action="store_true", help="Opt in to ONE bounded real model attempt")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose an unprivileged local port")
    service = DemoService(enable_nova=args.enable_nova, output_dir=args.output_dir)
    if args.enable_nova:
        from model_runner import enable_live_transport, live_startup_preflight
        preflight = live_startup_preflight()
        enable_live_transport(preflight)
        if args.output_dir:
            (args.output_dir / "nova-preflight.json").write_text(
                json.dumps(preflight, indent=2) + "\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(service))
    print(f"Synthetic local UI: http://127.0.0.1:{args.port}; Nova {'opted in (one attempt)' if args.enable_nova else 'disabled'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if args.enable_nova:
            from model_runner import disable_live_transport
            disable_live_transport()


if __name__ == "__main__":
    main()
