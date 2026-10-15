"""Local-only HTTP adapter and recorded export for the fixed Tessera panel demo."""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from engine import DemoRunError, run_isolated


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, port: int, web_root: Path, run_root: Path, timeout: int) -> None:
        self.web_root = web_root.resolve()
        self.run_root = run_root
        self.run_timeout = timeout
        self.run_lock = threading.Lock()
        self.latest_zip: bytes | None = None
        super().__init__(("127.0.0.1", port), DemoHandler)


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: Any) -> None:
        return  # No request bodies, credentials, paths or stacks in browser-facing logs.

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Connection", "close")
        if content_type == "application/zip":
            self.send_header("Content-Disposition", 'attachment; filename="tessera-demo-evidence.zip"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, body: dict[str, Any]) -> None:
        self._send(code, json.dumps(body).encode(), "application/json; charset=utf-8")

    def _local_request(self) -> bool:
        # Host validation also protects loopback from browser DNS-rebinding requests.
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            self._json(403, {"error": "Use this demo through its local address."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{name}" for name in allowed}:
            self._json(403, {"error": "Cross-origin demo requests are disabled."})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json(403, {"error": "Cross-site demo requests are disabled."})
            return False
        return True

    def do_GET(self) -> None:
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(200, {"service": "tessera-panel-demo", "schema_version": 1, "live": True, "offline": True})
        elif path == "/api/evidence":
            evidence = self.server.latest_zip
            if evidence is None:
                self._json(404, {"error": "Run the offline demo before downloading live evidence."})
            else:
                self._send(200, evidence, "application/zip")
        elif path.startswith("/api/"):
            self._json(404, {"error": "Unknown demo endpoint."})
        else:
            target = (self.server.web_root / unquote(path).lstrip("/")).resolve()
            if target.is_dir():
                target = (target / "index.html").resolve()
            if not target.is_relative_to(self.server.web_root) or not target.is_file():
                self._json(404, {"error": "Demo page not found."})
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), content_type)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if not self._local_request():
            return
        if urlsplit(self.path).path != "/api/run":
            self._json(404, {"error": "Unknown demo endpoint."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1024 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid body size")
            raw = self.rfile.read(length)
            if raw and json.loads(raw) != {}:
                raise ValueError("Only fixed scenarios are supported")
        except (ValueError, OSError):
            self._json(400, {"error": "This demo accepts only an empty request or {} for its fixed scenarios."})
            return
        if not self.server.run_lock.acquire(blocking=False):
            self._json(409, {"error": "An offline demo is already running. Try again after it finishes."})
            return
        try:
            with tempfile.TemporaryDirectory(prefix="run-", dir=self.server.run_root) as output:
                projection = run_isolated(Path(output), mode="live", timeout=self.server.run_timeout)
                self.server.latest_zip = (Path(output) / "evidence.zip").read_bytes()
                self._json(200, projection)
        except DemoRunError as exc:
            self._json(503, {"error": str(exc)})
        except Exception:
            self._json(503, {"error": "The offline demo could not complete. No new evidence was published."})
        finally:
            self.server.run_lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Record one actual engine run with its signed ZIP")
    export.add_argument("--output-dir", required=True, type=Path)
    export.add_argument("--timeout", type=int, default=45)
    serve = commands.add_parser("serve", help="Serve supplied demo assets and the fixed live-run API locally")
    serve.add_argument("--web-root", required=True, type=Path)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    if args.command == "export":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        # Only replace the two output files after a verified child completes.
        with tempfile.TemporaryDirectory(prefix="tessera-panel-export-") as temporary:
            try:
                result = run_isolated(Path(temporary), timeout=args.timeout)
            except DemoRunError as exc:
                parser.exit(1, f"{exc}\n")
            for name in ("evidence.zip", "demo-data.json"):
                staged = args.output_dir / f".{name}.tmp"
                shutil.copyfile(Path(temporary) / name, staged)
                staged.replace(args.output_dir / name)
        print(f"Recorded engine run {result['run_id']}: {result['summary']['blocked']} blocked, "
              f"{result['summary']['allowed']} allowed; signed evidence verified.")
    else:
        if not args.web_root.is_dir():
            parser.error("--web-root must be an existing directory")
        with tempfile.TemporaryDirectory(prefix="tessera-panel-server-") as temporary:
            with DemoServer(args.port, args.web_root, Path(temporary), args.timeout) as server:
                print(f"Tessera offline panel demo: http://127.0.0.1:{server.server_port}/", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass


if __name__ == "__main__":
    main()
