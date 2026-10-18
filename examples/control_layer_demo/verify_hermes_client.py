"""Offline patched-source/fake-SDK checks, not an actual Hermes runtime proof.

Never connects, imports Hermes or starts a model/OAuth/server. Executes only the
exact patched class/three handler functions with explicit fake dependencies.
Original third-party checkout remains unchanged. Run in WSL with --hermes-source
pointing at the historical pinned source; no dependency installation is performed.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import contextvars
import functools
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import io
import json
import os
import signal
import sys
import tempfile
import threading
import traceback
import types
from pathlib import Path
from unittest.mock import patch


class OfflineHermesError(Exception):
    pass


def require(value):
    if not value:
        raise OfflineHermesError


def apply_patch(original, diff):
    """Strict in-memory unified-diff application; never writes a source tree."""
    lines, output, cursor = original.splitlines(keepends=True), [], 0
    chunks = diff.splitlines(keepends=True)
    index = 2
    import re

    while index < len(chunks):
        match = re.fullmatch(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@.*\n", chunks[index])
        require(match is not None)
        start = int(match.group(1)) - 1
        output.extend(lines[cursor:start])
        cursor, index = start, index + 1
        while index < len(chunks) and not chunks[index].startswith("@@"):
            line = chunks[index]
            require(line[0] in " +-" )
            if line[0] in " -":
                require(lines[cursor] == line[1:])
                cursor += 1
            if line[0] in " +":
                output.append(line[1:])
            index += 1
    return "".join(output + lines[cursor:])


def run(source):
    attempts, recovered, checks = [], [], []

    def forbidden(*_args, **_kwargs):
        attempts.append(1)
        raise OfflineHermesError

    with contextlib.ExitStack() as guards:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.socket.bind",
                       "socket.create_connection", "socket.getaddrinfo"):
            guards.enter_context(patch(target, forbidden))
        helper_path = Path(__file__).with_name("hermes_client.py")
        spec = importlib.util.spec_from_file_location("offline_hermes_connector", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        root = Path(__file__).resolve().parents[3]
        diff = root / "workspace-support/audit/2026-10-18-cm095-resume/hermes-cm095-single-dispatch.patch"
        require(hashlib.sha256(diff.read_bytes()).hexdigest() == helper.PATCH_SHA256)
        original = (source / "tools/mcp_tool_handlers.py").read_bytes()
        require(hashlib.sha256(original).hexdigest() == "d3f99c5832dd7a99b0a39447f97981281cccd13ba582587b97f914c726cdb5d6")
        for relative, expected in helper.SOURCE_PINS.items():
            if relative != "tools/mcp_tool_handlers.py":
                require(hashlib.sha256((source / relative).read_bytes()).hexdigest() == expected)
        patched = apply_patch(original.decode(), diff.read_text())
        require(hashlib.sha256(patched.encode()).hexdigest() == helper.SOURCE_PINS["tools/mcp_tool_handlers.py"])
        tree = ast.parse(patched)
        checks.append("SEVEN_ORIGINAL_PINS_AND_EXACT_PATCH_APPLICATION")
        catalog = [{"name": name, "inputSchema": {"type": "object"}}
                   for name in ("fixture.read", "fixture.blocked", "fixture.review")]
        aliases = ["mcp__tessera__" + tool["name"].replace(".", "_") for tool in catalog]
        mapping = helper.catalog_bindings(catalog, aliases, lambda _server, name: "mcp__tessera__" + name.replace(".", "_"))
        require(set(mapping) == {tool["name"] for tool in catalog})
        try:
            helper.catalog_bindings(catalog + [catalog[0]], aliases, lambda _server, name: "mcp__tessera__" + name.replace(".", "_"))
        except helper.ConnectorError:
            pass
        else:
            raise OfflineHermesError
        checks.append("FULL_GENERIC_CATALOG_AND_DUPLICATE_NAME_REJECTION")
        selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in {"CM095HTTPCall", "_dispatch", "_call_tool_racing_stdio_death", "_make_tool_handler"}]
        require(len(selected) == 4)

        @contextlib.asynccontextmanager
        async def track(*_args, **_kwargs):
            yield

        def recovery(*_args, **_kwargs):
            recovered.append(1)
            raise OfflineHermesError

        namespace = {"contextvars": contextvars, "asyncio": asyncio, "inspect": inspect,
            "Any": object, "Callable": object, "partial": functools.partial,
            "_CM095_HTTP_CALL": contextvars.ContextVar("offline_context", default=None),
            "_loop": types.SimpleNamespace(_run_on_mcp_loop=lambda call, **_kwargs: asyncio.run(call())),
            "_trust_gate_check": lambda *_args: None, "_check_circuit_breaker": lambda *_args: None,
            "_acquire_call_server": forbidden, "_tool_is_read_only": lambda *_args: False,
            "_track_inflight_rpc": track, "_render_call_tool_result": lambda *_args: "rendered-without-audit-metadata",
            "_handle_session_expired_and_retry": recovery, "_handle_stdio_child_exited_and_retry": recovery,
            "_handle_auth_error_and_retry": recovery, "_mcp_result_with_native_images": lambda text, *_args: text,
            "_result_is_error": lambda _text: False}
        # Test-only compilation of four exact source nodes with explicitly named
        # fakes. Production helper imports the reviewed prepared module normally;
        # no FunctionType, copied globals or runtime code rewriting exists there.
        test_tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
        exec(compile(ast.fix_missing_locations(test_tree), "<exact-patched-hermes-fake-sdk>", "exec"), namespace)  # noqa: S102 - pinned source-only offline test
        calls = []

        class SDK:
            mode = "response"

            async def call_tool(self, name, *, arguments, meta):
                calls.append((name, arguments, dict(meta)))
                if self.mode == "unknown":
                    raise TimeoutError
                if self.mode == "review":
                    error = RuntimeError()
                    error.error = {"code": -32604, "data": {"_meta": {"tessera_audit_event_id": "evt_review"}}}
                    raise error
                result = {"isError": self.mode == "deny", "content": [{"type": "text", "text": (
                    "POLICY_BLOCK\npolicy_id: offline" if self.mode == "deny" else "offline response")}],
                    "_meta": {"tessera_audit_event_id": "evt_offline"}}
                if self.mode == "missing_audit":
                    result.pop("_meta")
                return result

        sdk = SDK()
        server = types.SimpleNamespace(session=sdk, _is_http=lambda: True, _rpc_lock=asyncio.Lock())
        handlers = types.SimpleNamespace(CM095HTTPCall=namespace["CM095HTTPCall"], _CM095_HTTP_CALL=namespace["_CM095_HTTP_CALL"])
        probe = {"name": "fixture.read", "arguments": {"selection": "public"}}

        def client():
            value = helper.HermesClient(source=source, url="http://127.0.0.1:8765/mcp/control",
                bearer="public_offline_fixture_token_32chars", run_id="offline-hermes", probes=[probe])  # noqa: S106 - public fake
            value.connected, value.server, value.handlers = True, server, handlers
            value.names = {probe["name"]: "mcp__tessera__fixture_read"}
            handler = namespace["_make_tool_handler"]("tessera", probe["name"], 15)
            value.dispatcher = lambda _name, args, **_kwargs: handler(args)
            return value

        for mode, expected in (("response", "RESPONSE"), ("deny", "DENIED"), ("review", "REVIEW_STOP"),
                               ("unknown", "UNKNOWN"), ("missing_audit", "UNKNOWN")):
            sdk.mode = mode
            value = client()
            before = len(calls)
            result = value.call(probe["name"], probe["arguments"], "offline-use")
            require(result["status"] == expected and len(calls) == before + 1)
            require(calls[-1][2] == {key: result[key] for key in ("run_id", "conversation_id", "request_id", "tool_use_id")})
            require(result["audit_event_id"] == (None if mode in {"unknown", "missing_audit"} else "evt_review" if mode == "review" else "evt_offline"))
            try:
                value.call(probe["name"], probe["arguments"], "another-use")
            except helper.ConnectorError:
                pass
            else:
                raise OfflineHermesError
            require(len(calls) == before + 1)
            summary = json.dumps(value.public_summary())
            require("public_offline_fixture_token" not in summary and "selection" not in summary)
        require(not recovered)
        sdk.mode = "response"
        bound = handlers.CM095HTTPCall(server, probe["name"], probe["arguments"], {"request_id": "offline"})
        before = len(calls)
        asyncio.run(bound.invoke(server, "tessera", probe["name"], probe["arguments"]))
        try:
            asyncio.run(bound.invoke(server, "tessera", probe["name"], probe["arguments"]))
        except RuntimeError:
            pass
        else:
            raise OfflineHermesError
        require(len(calls) == before + 1)
        checks.extend(["EXACT_PATCHED_HANDLER_FAKE_SDK_METADATA", "RAW_AUDIT_CAPTURE_BEFORE_RENDERING",
                       "DENIAL_AND_SEPARATE_REVIEW_STOP", "TIMEOUT_UNKNOWN_NO_RECOVERY_OR_REPLAY",
                       "EACH_PROBE_ONCE_ZERO_SUBSEQUENT_DISPATCH", "MISSING_AUDIT_METADATA_FAILS_CLOSED",
                       "PATCH_NATIVE_BOUNDARY_ONCE_GUARD", "PUBLIC_SUMMARY_EXCLUDES_PRIVATE_PAYLOAD"])
        for url in ("https://outside.invalid/mcp/control", "http://127.0.0.1:8765/mcp/other", "http://user@127.0.0.1:8765/mcp/control"):
            try:
                helper.HermesClient(source=source, url=url, bearer="public_offline_fixture_token_32chars", run_id="offline", probes=[probe])  # noqa: S106
            except helper.ConnectorError:
                pass
            else:
                raise OfflineHermesError
        try:
            client().connect()
        except helper.ConnectorError as error:
            require(str(error) == "LIVE_ADMISSION_REQUIRED")
        else:
            raise OfflineHermesError
        require(hashlib.sha256((source / "tools/mcp_tool_handlers.py").read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest())
        require(not attempts and "model_tools" not in sys.modules)
        checks.extend(["ENDPOINT_AND_LIVE_ADMISSION_BOUNDARY", "THIRDPARTY_UNCHANGED_NO_HERMES_IMPORT_OR_NETWORK"])
        with tempfile.TemporaryDirectory(prefix="offline-hermes-parent-", dir="/dev/shm") as tmp:
            parent = Path(tmp)
            os.chmod(parent, 0o700)
            require(helper.private_profile_parent(parent) == parent)
            os.chmod(parent, 0o755)  # noqa: S103 - empty negative fixture, immediately restored
            try:
                helper.private_profile_parent(parent)
            except helper.ConnectorError:
                pass
            else:
                raise OfflineHermesError
            os.chmod(parent, 0o700)
            link = parent / "link"
            link.symlink_to(parent)
            try:
                helper.private_profile_parent(link)
            except helper.ConnectorError:
                pass
            else:
                raise OfflineHermesError
        require(not parent.exists())
        checks.append("PROFILE_PARENT_PRIVATE_CURRENT_UID_RAM_NO_SYMLINKS")
    return {"schema": "tessera.hermes_source_fake_sdk.v1", "status": "PASS", "checks": checks,
            "check_count": len(checks), "forbidden_attempts": len(attempts), "recoverer_calls": len(recovered),
            "actual_hermes_dispatcher_loaded": False, "runtime": helper.dependency_readiness()}


def run_actual(source):
    """Actual authenticated Hermes/SDK on fake HTTP, exercising production guard."""
    stage, requests, client, runtime_guard = "GUARDS", [], None, None
    network_attempts = []
    source = source.resolve(strict=True)
    allowed_env = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "SHELL", "VIRTUAL_ENV"}
    for key in list(os.environ):
        if key not in allowed_env:
            del os.environ[key]
    sys.dont_write_bytecode = True

    def no_network(event, _args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            network_attempts.append("OFFLINE_NETWORK_OR_PROCESS_ATTEMPT")
            raise OfflineHermesError

    sys.addaudithook(no_network)
    spec = importlib.util.spec_from_file_location("actual_offline_hermes_connector", Path(__file__).with_name("hermes_client.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    profile_parent = source.parent / "offline-profile-parent"
    profile_parent.mkdir(mode=0o700)
    try:
        with contextlib.ExitStack() as guards:
            guards.enter_context(patch("socket.has_ipv6", False))
            runtime_guard = guards.enter_context(helper.guarded_loopback_runtime(source=source, profile_parent=profile_parent))
            require(helper.dependency_readiness()["status"] == "DEPENDENCY_METADATA_MATCH_ONLY")
            # Repeated caught rejection must stay active; no raising profile callbacks.
            rejected = runtime_guard.rejected_function(lambda: None)
            for _ in range(2):
                try:
                    rejected()
                except helper.ConnectorError:
                    pass
                else:
                    raise OfflineHermesError
            require(len(runtime_guard.blocked) == 2)
            runtime_guard.blocked.clear()
            stage = "IMPORT_GUARDED_SDK"
            import httpx
            import httpx2
            native_thread = {"caller": threading.get_ident(), "sdk": None}
            def http(request):
                require(str(request.url) == "http://127.0.0.1:8787/mcp/control")
                require(request.headers.get("authorization") == "Bearer public_offline_fixture_token_32chars")
                if request.method != "POST":
                    return httpx2.Response(405, headers={"allow": "POST"})
                payload = json.loads(request.content)
                method, params = payload["method"], payload.get("params", {})
                requests.append(method)
                if "id" not in payload:
                    return httpx2.Response(202)
                if method == "initialize":
                    result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                              "serverInfo": {"name": "offline-fixture", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": name, "description": "Harmless offline fixture",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
                        for name in ("fixture.read", "fixture.blocked", "fixture.review")]}
                elif method == "tools/call":
                    require(params["name"] == "fixture.blocked" and params["arguments"] == {})
                    require(set(params["_meta"]) == {"run_id", "conversation_id", "request_id", "tool_use_id"})
                    require(params["_meta"]["run_id"] == params["_meta"]["conversation_id"] == "offline-actual-hermes")
                    native_thread["sdk"] = threading.get_ident()
                    result = {"isError": True, "content": [{"type": "text", "text": "POLICY_BLOCK\npolicy_id: offline"}],
                              "_meta": {"tessera_audit_event_id": "evt_actual_offline"}}
                else:
                    raise OfflineHermesError
                return httpx2.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

            for http_module in (httpx, httpx2):
                real_client = http_module.AsyncClient

                def offline_client(*args, _module=http_module, _real=real_client, **kwargs):
                    require(kwargs.get("auth") is None)
                    def adapted(request):
                        response = http(request)
                        return _module.Response(response.status_code, headers=dict(response.headers), content=response.content)
                    kwargs.update(transport=_module.MockTransport(adapted), mounts={}, trust_env=False, follow_redirects=False)
                    return _real(*args, **kwargs)

                guards.enter_context(patch.object(http_module, "AsyncClient", offline_client))

            stage = "ACTUAL_HERMES_IMPORT_AND_DISCOVERY"
            client = helper.HermesClient(source=source, url=helper.LOOPBACK_URL,
                bearer="public_offline_fixture_token_32chars", run_id="offline-actual-hermes",  # noqa: S106 - fixture
                probes=[{"name": "fixture.blocked", "arguments": {}}], profile_parent=profile_parent)
            catalog = client.connect(runtime_admitted=True)
            require(len(catalog) == 3)
            stage = "ACTUAL_DISPATCH_FAKE_HTTP"
            trace = client.call("fixture.blocked", {}, "offline-use")
            require(trace["status"] == "DENIED" and trace["audit_event_id"] == "evt_actual_offline")
            require(requests.count("tools/call") == 1 and native_thread["sdk"] != native_thread["caller"])
            try:
                client.call("fixture.blocked", {}, "after-denial")
            except helper.ConnectorError:
                pass
            else:
                raise OfflineHermesError
            require(requests.count("tools/call") == 1)
            origins = []
            for name in ("model_tools", "tools.mcp_tool_handlers", "tools.mcp_tool_discovery", "mcp", "httpx", "httpx2"):
                path = Path(sys.modules[name].__file__).resolve()
                require(path.is_relative_to(source) or path.is_relative_to(Path(sys.prefix).resolve()))
                origins.append({"module": name, "origin": "PREPARED_RAM_SOURCE" if path.is_relative_to(source) else "ISOLATED_VENV",
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            stage = "CLEANUP"
            home = Path(client._profile.name)
            client.close()
            client = None
            require(not home.exists() and not network_attempts)
            runtime_guard.assert_clean()
            return {"schema": "tessera.hermes_actual_offline.v2", "status": "PASS", "actual_hermes_dispatcher_loaded": True,
                    "connector_source_sha256": hashlib.sha256(Path(helper.__file__).read_bytes()).hexdigest(),
                    "verifier_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "transport": "REAL_SDK_FAKE_HTTP_ONLY", "tool_calls": 1, "after_denial_tool_calls": 0,
                    "context_crossed_real_mcp_thread": True, "origins": origins, "private_profile_removed": True,
                    "guard": runtime_guard.summary(), "network_attempts": 0, "persistent_guard_selftest_rejections": 2,
                    "source_authentication": "PARENT_FULL_ARCHIVE_ADMISSION_REQUIRED_SEPARATELY"}
    except BaseException as error:
        locations, observed = [], error
        for _ in range(3):
            locations.extend({"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                             for frame in traceback.extract_tb(observed.__traceback__)[-4:])
            observed = observed.__context__
            if observed is None:
                break
        return {"schema": "tessera.hermes_actual_offline.v2", "status": "FAIL", "phase": stage,
                "reason_code": "ACTUAL_OFFLINE_HERMES_CHECK_FAILED", "source_locations": locations,
                "guard": runtime_guard.summary() if runtime_guard else None, "network_attempts": len(network_attempts),
                "tool_calls": requests.count("tools/call")}
    finally:
        if client is not None:
            try:
                client.close()
            except BaseException:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-source", type=Path, required=True)
    parser.add_argument("--actual-offline", action="store_true", help="Dedicated admitted venv/RAM source only; fake HTTP, no live traffic")
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, lambda *_args: (_ for _ in ()).throw(OfflineHermesError()))
    signal.alarm(20)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run_actual(args.hermes_source) if args.actual_offline else run(args.hermes_source)
    except BaseException:
        result = {"schema": "tessera.hermes_source_fake_sdk.v1", "status": "FAIL", "reason_code": "OFFLINE_HERMES_SOURCE_CHECK_FAILED"}
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
