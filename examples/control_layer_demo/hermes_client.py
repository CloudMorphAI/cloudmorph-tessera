"""Source-prepared, scripted Hermes MCP connector; never a reasoning agent.

Requires a separately reviewed isolated dependency environment and a RAM copy of
Hermes 51abb25 with the exact CM095 patch. No installation, login, policy writer,
model, CLI entry point or live admission is provided here. Root's operator owns
OAuth, strict activation and canonical audit upload once. Returned SDK payloads
are private in-process evidence; public_summary excludes arguments and results.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import inspect
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

HERMES_COMMIT = "51abb25ce07f384bd60f1eba151623c769db6b3e"
PATCH_SHA256 = "dac520caa486f8c567cfa3b6bd25fef746ca8c5b18b85322b095caaeb42817a7"
SOURCE_PINS = {
    "model_tools.py": "fbb40d0058e3edd6c4e1608b8db9e922aaf38d2c9d0438c3811c6d6b01b45340",
    "pyproject.toml": "ff41b097147124ff2eb51a437d5f44eddea35104900c1c79c7edfb8b7352d901",
    "uv.lock": "16300cf807347b7fcfa3c19963f5fe950913fdb3e41e3c649ea724bc7dc997da",
    "tools/mcp_tool_discovery.py": "9be361ff6791ef9a15cbfe9745f6b389eac3b9d7d9be99d42d4ddf50061495b0",
    "tools/mcp_tool_handlers.py": "203d9416b18ab298f101877e7b4cddf3e9e2120dd26bf54c58cf760cd115e041",
    "tools/mcp_tool_registration.py": "523543037975b14f9c84f9181dfbe3f7a9024e4af5e10238eab9ba22fa86c175",
    "tools/mcp_tool_schema.py": "21ab2ef8b122add133af4ef387b32f6e8c6711f6e1b2f6f4744bcb279fa2135b",
}
REQUIRED_VERSIONS = {"mcp": "2.0.0", "httpx2": "2.12.0", "ruamel.yaml": "0.18.16"}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}\Z")
OWNED_PREFIXES = ("tools", "model_tools", "hermes_cli", "hermes_platform", "agent", "utils")
LOOPBACK_URL = "http://127.0.0.1:8787/mcp/control"
_ACTIVE_GUARD = None


class ConnectorError(Exception):
    """Fixed code only; no raw private exception text."""


def _require(value, code="CONNECTOR_BOUNDARY_REJECTED"):
    if not value:
        raise ConnectorError(code)


def dependency_readiness():
    missing = []
    for distribution, expected in REQUIRED_VERSIONS.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            missing.append(distribution)
    return {"status": "BLOCKED" if missing else "DEPENDENCY_METADATA_MATCH_ONLY",
            "reason_code": "ISOLATED_DEPENDENCIES_UNPROVEN" if missing else "IMPORTS_NOT_EXECUTED",
            "mismatched_distributions": missing, "hermes_commit": HERMES_COMMIT}


def verify_prepared_source(source: Path):
    source = source.resolve(strict=True)
    _require(source.is_relative_to(Path("/dev/shm")), "RAM_SOURCE_REQUIRED")  # noqa: S108 - required RAM source
    for relative, expected in SOURCE_PINS.items():
        path = source / relative
        _require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(source))
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "HERMES_SOURCE_PIN_MISMATCH")
    return source


def private_profile_parent(parent: Path | None):
    if parent is None:
        return None
    parent = Path(parent)
    resolved = parent.resolve(strict=True)
    _require(resolved == parent.absolute() and resolved.is_relative_to(Path("/dev/shm")),  # noqa: S108 - required owned RAM
             "PRIVATE_RAM_PROFILE_PARENT_REQUIRED")
    info = resolved.stat()
    _require(resolved.is_dir() and not parent.is_symlink() and info.st_uid == os.getuid()
             and info.st_mode & 0o7777 == 0o700, "PRIVATE_RAM_PROFILE_PARENT_REQUIRED")
    mounts = Path("/proc/mounts").read_text().splitlines()
    _require(any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in mounts))  # noqa: S108 - verified mount
    return resolved


class guarded_loopback_runtime:  # noqa: N801 - named opt-in public context API
    """Opt-in containment for one fresh, dedicated scripted connector child.

    Normal SDK transport is retained, restricted to the fixed loopback endpoint.
    Guards intentionally remain active through process exit, including atexit.
    This is not a general Python security sandbox or a reusable parent context.
    Caller must authenticate the complete source tree and dependency environment.
    """

    def __init__(self, *, source: Path, profile_parent: Path):
        self.source = verify_prepared_source(source)
        self.parent = private_profile_parent(profile_parent)
        _require(self.parent is not None)
        self.active = False
        self.blocked, self.optional_imports, self.unwrapped = [], set(), 0
        self.replacements, self.installed = [], set()
        self.empty_secret_reads = 0

    def reject(self, code="RUNTIME_BOUNDARY_FORBIDDEN"):
        self.blocked.append(code)
        raise ConnectorError(code)

    @staticmethod
    def names(module):
        if module in {"hermes_cli.env_loader", "agent.secret_scope"}:
            return {"hydrate_profile_secret_sources", "build_profile_secret_scope", "get_secret"}
        if "oauth" in module or module in {"hermes_cli.auth", "mcp.client.auth"}:
            return {"refresh_token", "load_tokens", "get_token", "authorize", "get_provider", "async_auth_flow"}
        return set()

    def replace(self, owner, name, value):
        self.replacements.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def empty_secret(self, *_args, **_kwargs):
        # Deliberately no environment, dotenv, keyring or file fallback.
        self.empty_secret_reads += 1
        return None  # noqa: RET501 - explicit empty secret resolver contract

    def rejected_function(self, original):
        if inspect.iscoroutinefunction(original):
            async def rejected(*_args, **_kwargs):
                self.reject("PROVIDER_AUTH_SECRET_CALL_FORBIDDEN")
        else:
            def rejected(*_args, **_kwargs):
                self.reject("PROVIDER_AUTH_SECRET_CALL_FORBIDDEN")
        return rejected

    def install(self, module):
        for name in self.names(module.__name__):
            original = vars(module).get(name)
            if inspect.isfunction(original):
                replacement = self.empty_secret if name == "get_secret" else self.rejected_function(original)
                self.replace(module, name, replacement)
                self.installed.add(module.__name__ + "." + name)
        for value in list(vars(module).values()):
            if not inspect.isclass(value) or value.__module__ != module.__name__:
                continue
            for name in self.names(module.__name__):
                original = vars(value).get(name)
                if isinstance(original, (staticmethod, classmethod)):
                    self.replace(value, name, type(original)(self.rejected_function(original.__func__)))
                elif inspect.isfunction(original):
                    self.replace(value, name, self.rejected_function(original))
        if module.__name__ == "tools.mcp_tool_discovery":
            self.replace(module, "_owner_secret_mapping", lambda _home: {})
        if module.__name__ in {"httpx", "httpx2"}:
            guard, original = self, module.AsyncClient
            default_auth = inspect.signature(original.send).parameters["auth"].default

            class RestrictedClient(original):
                def __init__(self, *args, **kwargs):
                    _require(kwargs.get("auth") is None, "HTTP_AUTH_RECOVERER_FORBIDDEN")
                    kwargs["follow_redirects"] = False
                    super().__init__(*args, **kwargs)

                async def send(self, request, *args, **kwargs):
                    if str(request.url) != LOOPBACK_URL or request.method not in {"HEAD", "GET", "POST", "DELETE"}:
                        guard.reject("HTTP_ENDPOINT_FORBIDDEN")
                    auth = kwargs.get("auth")
                    if kwargs.get("follow_redirects") is True or (auth is not None and auth is not default_auth):
                        guard.reject("HTTP_AUTH_RECOVERER_FORBIDDEN")
                    kwargs["follow_redirects"] = False
                    kwargs["auth"] = None
                    return await super().send(request, *args, **kwargs)

            self.replace(module, "AsyncClient", RestrictedClient)
            self.replace(module, "Client", self.rejected_function(module.Client))

    def audit(self, event, args):
        if not self.active:
            return
        if event == "import" and args[0].split(".", 1)[0] in {
                "boto3", "botocore", "aiobotocore", "anthropic", "openai", "keyring"}:
            self.optional_imports.add(args[0].split(".", 1)[0])
            raise ImportError("PROVIDER_IMPORT_FORBIDDEN")
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "socket.bind", "socket.sendto"}:
            self.reject("PROCESS_OR_LISTENER_FORBIDDEN")
        if event == "socket.connect":
            sock, address = args[:2]
            if address != ("127.0.0.1", 8787) or sock.family != socket.AF_INET or sock.type & 0xF != socket.SOCK_STREAM:
                self.reject("EXTERNAL_NETWORK_FORBIDDEN")
        if event == "socket.getaddrinfo" and (args[0], args[1]) != ("127.0.0.1", 8787):
            self.reject("DNS_FORBIDDEN")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            in_profile = path.is_relative_to(self.parent)
            if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) and not in_profile:
                self.reject("NON_PROFILE_WRITE_FORBIDDEN")
            protected = {".aws", ".azure", ".netrc", "oauth.json", "credentials", ".env", ".op.env"}
            if any(part in protected for part in path.parts) and not in_profile:
                self.reject("PROTECTED_FILE_FORBIDDEN")
            allowed = (self.source, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), Path(__file__).parent.resolve())
            if path.is_relative_to(Path.home()) and not in_profile and not any(path.is_relative_to(p) for p in allowed):
                self.reject("ORDINARY_HOME_FILE_FORBIDDEN")

    def observe(self, frame, event, _arg):
        if event == "call" and frame.f_code.co_name in self.names(frame.f_globals.get("__name__", "")):
            self.unwrapped += 1  # Coverage only; never raise from a profile callback.

    def __enter__(self):
        global _ACTIVE_GUARD
        _require(_ACTIVE_GUARD is None and not any(n.split(".", 1)[0] in OWNED_PREFIXES for n in sys.modules),
                 "FRESH_PROCESS_REQUIRED")
        guard = self

        class Loader:
            def __init__(self, original):
                self.original = original

            def create_module(self, spec):
                return self.original.create_module(spec)

            def exec_module(self, module):
                self.original.exec_module(module)
                guard.install(module)

            def __getattr__(self, name):
                return getattr(self.original, name)

        class Finder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if not (guard.names(fullname) or fullname in {"httpx", "httpx2", "tools.mcp_tool_discovery"}):
                    return None
                spec = importlib.machinery.PathFinder.find_spec(fullname, path)
                if spec is not None and spec.loader is not None:
                    spec.loader = Loader(spec.loader)
                return spec

        self.finder = Finder()
        self.previous_profile, self.previous_thread_profile = sys.getprofile(), threading.getprofile()
        self.previous_logging = logging.root.manager.disable
        self.previous_bytecode = sys.dont_write_bytecode
        logging.disable(logging.CRITICAL)
        sys.dont_write_bytecode = True
        self.active = True
        _ACTIVE_GUARD = self
        sys.addaudithook(self.audit)
        sys.meta_path.insert(0, self.finder)
        sys.setprofile(self.observe)
        threading.setprofile(self.observe)
        for name in ("httpx", "httpx2"):
            if name in sys.modules:
                self.install(sys.modules[name])
        return self

    def summary(self):
        return {"api": "guarded_loopback_runtime.v1", "forbidden_calls": len(self.blocked),
                "blocked_codes": sorted(set(self.blocked)), "unwrapped_forbidden_entries": self.unwrapped,
                "empty_secret_reads": self.empty_secret_reads,
                "blocked_optional_provider_imports": sorted(self.optional_imports),
                "persistent_function_guards": sorted(self.installed)}

    def assert_clean(self):
        _require(not self.blocked and not self.unwrapped, "RUNTIME_GUARD_CHECK_FAILED")

    def __exit__(self, *_exc):
        pass  # Dedicated child only: protect shutdown/atexit as well as calls.


def _plain(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(value, "model_dump") else value


def catalog_bindings(catalog, registered, alias_function):
    _require(isinstance(catalog, list) and 1 <= len(catalog) <= 128)
    names = {}
    for tool in catalog:
        _require(isinstance(tool, dict))
        name = tool.get("name")
        _require(isinstance(name, str) and ID_RE.fullmatch(name) and name not in names)
        _require(isinstance(tool.get("inputSchema"), dict))
        alias = alias_function("tessera", name)
        _require(alias in registered and alias not in names.values())
        names[name] = alias
    return names


def _observed(call):
    result, error = _plain(call.raw_result), _plain(call.raw_error)
    if isinstance(result, dict):
        meta = result.get("_meta", {})
        audit_id = meta.get("tessera_audit_event_id") if isinstance(meta, dict) else None
        texts = [part.get("text", "") for part in result.get("content", []) if isinstance(part, dict)]
        status = "DENIED" if result.get("isError") is True and any(
            isinstance(text, str) and text.startswith("POLICY_BLOCK\n") for text in texts) else (
                "TOOL_ERROR" if result.get("isError") is True else "RESPONSE")
    elif isinstance(error, dict):
        data = error.get("data", {})
        meta = data.get("_meta", {}) if isinstance(data, dict) else {}
        audit_id = meta.get("tessera_audit_event_id") if isinstance(meta, dict) else None
        status = "REVIEW_STOP" if error.get("code") == -32604 else "UNKNOWN"
    else:
        status, audit_id = "UNKNOWN", None
    if not isinstance(audit_id, str) or not ID_RE.fullmatch(audit_id):
        status, audit_id = "UNKNOWN", None
    return status, audit_id, result, error


class HermesClient:
    """Dedicated-process connector for at most three exact operator probes.

    A denial/review/error/unknown permanently stops this instance. A separate
    review task needs a separate explicit admission; it never resumes a denial.
    This class cannot attest downstream zero execution; root must verify the
    independent executor ledger against returned audit/correlation IDs.
    """

    def __init__(self, *, source: Path, url: str, bearer: str, run_id: str, probes: list[dict],
                 profile_parent: Path | None = None):
        parsed = urlsplit(url)
        _require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                 and parsed.path == "/mcp/control" and not parsed.query and not parsed.fragment
                 and not parsed.username and not parsed.password)
        _require(isinstance(bearer, str) and 32 <= len(bearer) <= 512 and bearer.isascii()
                 and not any(ch.isspace() for ch in bearer))
        _require(isinstance(run_id, str) and ID_RE.fullmatch(run_id))
        _require(isinstance(probes, list) and 1 <= len(probes) <= 3)
        for probe in probes:
            _require(isinstance(probe, dict) and set(probe) == {"name", "arguments"}
                     and isinstance(probe["name"], str) and ID_RE.fullmatch(probe["name"])
                     and isinstance(probe["arguments"], dict))
        raw = json.dumps(probes, sort_keys=True, allow_nan=False)
        _require(len(raw.encode()) <= 8192)
        self.source, self.url, self.bearer, self.run_id = source, url, bearer, run_id
        self.probes = json.loads(raw)
        self.profile_parent = private_profile_parent(profile_parent)
        self.catalog, self.names, self.receipts = [], {}, []
        self.stopped, self.connected = False, False
        self._profile, self._environment, self._used, self._used_probes = None, {}, set(), set()

    def _origins(self):
        for name, module in list(sys.modules.items()):
            if name.split(".", 1)[0] in OWNED_PREFIXES and getattr(module, "__file__", None):
                _require(Path(module.__file__).resolve().is_relative_to(self.source), "HERMES_IMPORT_ORIGIN_MISMATCH")

    def connect(self, *, runtime_admitted: bool = False):
        _require(runtime_admitted is True, "LIVE_ADMISSION_REQUIRED")
        _require(not self.connected and self._profile is None)
        _require(dependency_readiness()["status"] == "DEPENDENCY_METADATA_MATCH_ONLY", "ISOLATED_DEPENDENCIES_UNPROVEN")
        self.source = verify_prepared_source(self.source)
        if _ACTIVE_GUARD is not None:
            _require(self.source == _ACTIVE_GUARD.source and self.profile_parent == _ACTIVE_GUARD.parent
                     and self.url == LOOPBACK_URL, "GUARDED_CONNECTOR_IDENTITY_MISMATCH")
        _require(not any(name.split(".", 1)[0] in OWNED_PREFIXES for name in sys.modules), "FRESH_PROCESS_REQUIRED")
        mounts = Path("/proc/mounts").read_text().splitlines()
        _require(any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in mounts))  # noqa: S108 - verified RAM
        parent = private_profile_parent(self.profile_parent)
        self._profile = tempfile.TemporaryDirectory(prefix="cm095-hermes-", dir=parent or "/dev/shm")  # noqa: S108 - verified RAM
        home = Path(self._profile.name)
        os.chmod(home, 0o700)
        values = {"HERMES_HOME": str(home), "XDG_CACHE_HOME": str(home / "cache"),
                  "XDG_CONFIG_HOME": str(home / "config"), "XDG_STATE_HOME": str(home / "state")}
        self._environment = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        config = {"mcp_servers": {"tessera": {"url": self.url, "headers": {"Authorization": "Bearer " + self.bearer},
                  "enabled": True, "lazy": False, "connect_timeout": 10, "timeout": 15,
                  "tools": {"resources": False, "prompts": False}}}}
        fd = os.open(home / "config.yaml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream)
        self.bearer = ""
        sys.path.insert(0, str(self.source))
        try:
            self.handlers = importlib.import_module("tools.mcp_tool_handlers")
            discovery = importlib.import_module("tools.mcp_tool_discovery")
            schema = importlib.import_module("tools.mcp_tool_schema")
            self.dispatcher = importlib.import_module("model_tools").handle_function_call
            self._origins()
            registered = discovery.discover_mcp_tools(allowed_mcp_names=["tessera"])
            scope = importlib.import_module("tools.mcp_tool_scope")
            self.server = self.handlers._core._servers[scope._resolve_server_key("tessera")]
            _require(self.server._is_http() is True)
            self.catalog = [_plain(tool) for tool in self.server._tools]
            self.names = catalog_bindings(self.catalog, registered, schema.mcp_prefixed_tool_name)
            _require(all(probe["name"] in self.names for probe in self.probes))
            self._origins()
            self.connected = True
            return self.catalog
        except BaseException:
            self.close()
            raise ConnectorError("HERMES_DISCOVERY_FAILED") from None

    def call(self, name: str, arguments: dict, tool_use_id: str):
        _require(self.connected and not self.stopped, "CONNECTOR_STOPPED")
        _require({"name": name, "arguments": arguments} in self.probes and name in self.names)
        _require(isinstance(tool_use_id, str) and ID_RE.fullmatch(tool_use_id) and tool_use_id not in self._used)
        probe_key = hashlib.sha256(json.dumps({"name": name, "arguments": arguments}, sort_keys=True,
                                             allow_nan=False).encode()).hexdigest()
        _require(probe_key not in self._used_probes and len(self.receipts) < len(self.probes), "PROBE_ALREADY_CONSUMED")
        self._used_probes.add(probe_key)
        self._used.add(tool_use_id)
        request_id = uuid.uuid4().hex
        args = json.loads(json.dumps(arguments, allow_nan=False))
        meta = {"run_id": self.run_id, "conversation_id": self.run_id,
                "request_id": request_id, "tool_use_id": tool_use_id}
        bound = self.handlers.CM095HTTPCall(self.server, name, args, meta)
        token = self.handlers._CM095_HTTP_CALL.set(bound)
        try:
            self.dispatcher(self.names[name], args, session_id=self.run_id, turn_id=tool_use_id,
                            tool_call_id=tool_use_id, api_request_id=request_id, enabled_tools=list(self.names.values()))
        except BaseException:
            pass  # Actual bound SDK result/error below is authoritative, never rendered prose.
        finally:
            self.handlers._CM095_HTTP_CALL.reset(token)
        status, audit_id, result, error = _observed(bound)
        self.stopped = status != "RESPONSE"
        receipt = {**meta, "tool_name": name, "arguments": args, "status": status,
                   "audit_event_id": audit_id, "native_dispatch_attempted": bound.attempted,
                   "sdk_result": result, "sdk_error": error}
        self.receipts.append(receipt)
        return receipt

    def public_summary(self):
        return {"schema": "tessera.hermes_scripted_connector.v1", "label": "SCRIPTED CONNECTOR ONLY; NO REASONING AGENT",
                "hermes_commit": HERMES_COMMIT, "patch_sha256": PATCH_SHA256, "call_count": len(self.receipts),
                "statuses": [row["status"] for row in self.receipts], "stopped": self.stopped,
                "downstream_zero_execution": "REQUIRES_INDEPENDENT_EXECUTOR_AND_CANONICAL_AUDIT_PROOF"}

    def close(self):
        try:
            if self._profile is not None and "tools.mcp_tool_lifecycle" in sys.modules:
                sys.modules["tools.mcp_tool_lifecycle"].shutdown_mcp_servers(names={"tessera"}, timeout=5)
        finally:
            self.bearer, self.connected, self.stopped = "", False, True
            for key, previous in self._environment.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
            self._environment = {}
            if self._profile is not None:
                self._profile.cleanup()
                self._profile = None
