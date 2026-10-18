"""One bounded joint local fixture, never hosted/OAuth/AWS/model acceptance.

Actual original runner, canonical proxy CLI, isolated Hermes child/SDK and local
TCP are used. Only hosted snapshot/ACK and the adapter's AWS boundary are fake.
RAM-only sitecustomize tripwires restrict all fixture child network/credentials.
No production source is edited. Run once after root review; no automatic retry.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[1]
PINS = {
    "original_flow.py": "2e835d225d0cc5ee4e058c91874684db6a5e43baca9ce6c6dfbb33c8848c4e68",
    "original_flow_hermes_child.py": "fa701a0e4b62ffa0a84aa9e821eb8f12eb0ff6a0b86a4e142c74c2c89a3d3ef2",
    "hermes_client.py": "9c11dedab0d8ddbf35e327521e35b6e55b2cebb342ca413658899480b2fe9772",
    "hermes_source.py": "b4e16c78aad3c253aff8560ebbf6b12ffe41164099e926149936787a3a1f874c",
    "orchestrator.py": "0d095f200cd65e28d5852197d943476b6a3bb59deda35ec4b68c6e396a1e0315",
    "aws_readiness_server.py": "318b3c399564c1a834a49aaf9be5e853e1a207bb1efc47a0bcfe5f372a4d7589",
    "readiness-review.yaml": "772a7f7db2b018839153e74d094f7605d793944ee10335c09baa4561b7895112",
}
TENANT = "offline_original_joint"
PRIOR_ATTEMPT_SOURCE_SHA256 = "1b97e41b813605f5ef9b432243c792c86309f425f74d937543ac45441d0761d5"
IPV6_PROBE_SOURCE_SHA256 = "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"
DIAGNOSTIC_CODEC = r'''
import os,re,sys
KINDS={"CONNECT","BIND","DNS","WRITE","CREDENTIAL","PROCESS","PROVIDER","MODEL_AUTH","UNKNOWN"}
HOSTS={"LOOPBACK_V4","LOOPBACK_V6","UNSPECIFIED_V4","UNSPECIFIED_V6","OTHER_HOST","UNIX_OR_OTHER","NOT_APPLICABLE"}
PORTS={"ZERO","OWNED","OTHER_VALID_PORT","OTHER_PORT","NOT_APPLICABLE"}
PREFIXES={"OWNED_RAM","OTHER_RAM","OUTSIDE_TMP","OUTSIDE_VAR_TMP","OUTSIDE_HOME","OUTSIDE_VAR","OUTSIDE_USR","OUTSIDE_ETC","DEVICE","PROC","RUN","OTHER","NOT_APPLICABLE"}
ROOTS={"RUNTIME","HERMES","ENVIRONMENT","STDLIB","FIXTURE","OTHER"}
def host_port(address):
    if address is None: return "NOT_APPLICABLE","NOT_APPLICABLE"
    if not isinstance(address,tuple) or len(address)<2: return "UNIX_OR_OTHER","NOT_APPLICABLE"
    host,port=address[:2]
    host_class={"127.0.0.1":"LOOPBACK_V4","::1":"LOOPBACK_V6","0.0.0.0":"UNSPECIFIED_V4","::":"UNSPECIFIED_V6"}.get(host,"OTHER_HOST") if isinstance(host,str) else "OTHER_HOST"
    port_class="ZERO" if type(port) is int and port==0 else "OWNED" if type(port) is int and port in {8786,8787} else "OTHER_VALID_PORT" if type(port) is int and 1<=port<=65535 else "OTHER_PORT"
    return host_class,port_class
def write_prefix(path):
    if path is None: return "NOT_APPLICABLE"
    path=os.path.abspath(os.fsdecode(path))
    prefixes=((root,"OWNED_RAM"),("/dev/shm","OTHER_RAM"),("/var/tmp","OUTSIDE_VAR_TMP"),("/tmp","OUTSIDE_TMP"),("/home","OUTSIDE_HOME"),("/var","OUTSIDE_VAR"),("/usr","OUTSIDE_USR"),("/etc","OUTSIDE_ETC"),("/dev","DEVICE"),("/proc","PROC"),("/run","RUN"))
    return next((label for prefix,label in prefixes if path==prefix or path.startswith(prefix+os.sep)),"OTHER")
def public_frames():
    frame=sys._getframe(1);values=[];steps=0
    public_roots=((root+"/bootstrap","FIXTURE"),(runtime,"RUNTIME"),(root+"/hermes-source","HERMES"),(sys.prefix,"ENVIRONMENT"),("/usr/lib/python"+str(sys.version_info.major)+"."+str(sys.version_info.minor),"STDLIB"))
    while frame is not None and len(values)<4 and steps<32:
        filename=os.path.abspath(frame.f_code.co_filename);name=frame.f_code.co_name
        steps+=1
        if name not in {"public_frames","diagnostic","reject","audit","observe","parent_audit","parent_reject"}:
            for prefix,label in public_roots:
                if filename.startswith(prefix+os.sep):
                    relative=filename[len(prefix)+1:]
                    if relative in public_sources.get(label,()) and relative.endswith(".py") and len(relative)<=240 and re.fullmatch(r"[A-Za-z0-9_./+-]+",relative) and ".." not in relative.split("/"):
                        function=name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}",name) or name in {"<module>","<lambda>","<listcomp>","<dictcomp>","<setcomp>","<genexpr>"} else None
                        line=frame.f_lineno
                        values.append({"root":label,"file":relative,"line":line if type(line) is int and 1<=line<=1000000 else None,"function":function})
                    break
        frame=frame.f_back
    return values or [{"root":"OTHER","file":None,"line":None,"function":None}]
def diagnostic(code,kind="UNKNOWN",address=None,path=None):
    host,port=host_port(address)
    return {"code":code,"kind":kind if kind in KINDS else "UNKNOWN","host_class":host,"port_class":port,"write_prefix_class":write_prefix(path),"source_frames":public_frames()}
'''
GUARD = DIAGNOSTIC_CODEC + r'''
import atexit,json,os,socket,sys,threading
root=os.environ["CM095_FIXTURE_ROOT"]
runtime=os.environ["CM095_FIXTURE_RUNTIME"]
with open(os.path.join(root,"public-source-files.json")) as catalogue:
    public_sources=json.load(catalogue)
fd=os.open(os.path.join(root,"guard-"+str(os.getpid())+".jsonl"),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
def reject(code,kind="UNKNOWN",address=None,path=None):
    os.write(fd,(json.dumps(diagnostic(code,kind,address,path))+"\n").encode())
    raise RuntimeError("FIXTURE_BOUNDARY_REJECTED")
def audit(event,args):
    if event in {"socket.connect","socket.bind"}:
        address=args[1]
        if event=="socket.bind" and isinstance(address,tuple) and address[0] in {"::1","127.0.0.1"} and address[1]==0: reject("LOOPBACK_FEATURE_PROBE","BIND",address)
        if not(isinstance(address,tuple) and len(address)==2 and address[0]=="127.0.0.1" and address[1] in {8786,8787}): reject("EXTERNAL_NETWORK","CONNECT" if event=="socket.connect" else "BIND",address)
    if event=="socket.getaddrinfo" and not(args[0]=="127.0.0.1" and args[1] in {8786,8787}): reject("EXTERNAL_DNS","DNS",args[:2])
    if event in {"subprocess.Popen","os.system","os.exec","os.posix_spawn"}: reject("CHILD_PROCESS_FORBIDDEN","PROCESS")
    if event=="open" and args and isinstance(args[0],(str,bytes)):
        path=os.path.abspath(os.fsdecode(args[0]))
        if any(part in path.split(os.sep) for part in (".aws",".azure",".codex",".ssh")) or os.path.basename(path) in {".netrc","oauth.json","credentials"}: reject("CREDENTIAL_PATH","CREDENTIAL",path=path)
        mode=args[1] or ""
        flags=args[2] if len(args)>2 else 0
        writing=any(x in mode for x in "wax+") or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        if writing and path!="/dev/null" and not(path==root or path.startswith(root+os.sep)): reject("NON_RAM_WRITE","WRITE",path=path)
def observe(frame,event,arg):
    if event!="call": return
    module=frame.f_globals.get("__name__","");name=frame.f_code.co_name
    if module.startswith(("boto3","botocore")) and name in {"client","resource","create_client","get_credentials","_make_api_call"}: reject("PROVIDER_ATTEMPT","PROVIDER")
    if name in {"run_agent","session_login"}: reject("MODEL_OR_AUTH_ATTEMPT","MODEL_AUTH")
sys.addaudithook(audit)
sys.setprofile(observe);threading.setprofile(observe)
atexit.register(lambda:os.write(fd,b'{"code":"GUARD_EXIT"}\n'))
os.write(fd,b'{"code":"GUARD_READY"}\n')
'''
ADAPTER = r'''
import importlib.util,json,sys
from pathlib import Path
path=Path(sys.argv.pop(1))
spec=importlib.util.spec_from_file_location("fixture_actual_adapter",path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class FakeAWSReadOnly:
    def __init__(self,workload_mode=False):
        if workload_mode: raise RuntimeError("FIXTURE_MODE_REJECTED")
    def discover(self):
        return [{"name":"aws___list_regions","description":"Synthetic AWS boundary; actual loopback SDK", "inputSchema":{"type":"object","properties":{},"required":[],"additionalProperties":False}}]+module._fixture_tools()
    def call(self,name,args):
        if name!="aws___list_regions" or args!={}: raise RuntimeError("FIXTURE_READ_REJECTED")
        return {"isError":False,"content":[{"type":"text","text":json.dumps({"regions":["us-east-1","eu-west-1"]})}]}
module.AWSReadOnly=FakeAWSReadOnly
module.main()
'''


class FixtureError(Exception):
    pass


def require(value):
    if not value:
        raise FixtureError


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def closed(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) != 0


GUARD_CODES = {"EXTERNAL_NETWORK", "EXTERNAL_DNS", "CHILD_PROCESS_FORBIDDEN", "CREDENTIAL_PATH",
               "NON_RAM_WRITE", "PROVIDER_ATTEMPT", "MODEL_OR_AUTH_ATTEMPT", "LOOPBACK_FEATURE_PROBE"}


def public_source_files(roots):
    """Inventory public Python source names; never export an arbitrary filename."""
    result = {}
    deadline = time.monotonic() + 20
    entries = 0
    directories_seen = 0
    for label, directories in roots.items():
        names = set()
        for directory, display_root in directories:
            require(directory.is_dir() and not directory.is_symlink())
            for current, children, files in os.walk(directory, followlinks=False,
                                                     onerror=lambda _error: require(False)):
                directories_seen += 1
                require(directories_seen <= 20000 and time.monotonic() <= deadline)
                children[:] = [name for name in children if not name.startswith(".")
                               and name not in {"__pycache__", "node_modules", "venv", "build", "dist", "cache", "caches"}
                               and not (Path(current) / name).is_symlink()]
                for name in files:
                    entries += 1
                    require(entries <= 100000 and time.monotonic() <= deadline)
                    if not name.endswith(".py") or name.startswith("."):
                        continue
                    path = Path(current) / name
                    relative = str(path.relative_to(display_root))
                    if (not path.is_symlink() and relative.endswith(".py") and len(relative) <= 240
                            and re.fullmatch(r"[A-Za-z0-9_./+-]+", relative) and ".." not in relative.split("/")):
                        names.add(relative)
        result[label] = sorted(names)
    return result


def safe_diagnostic(value, codec, public_sources):
    """Reject malformed telemetry wholesale; never return a raw fallback."""
    keys = {"code", "kind", "host_class", "port_class", "write_prefix_class", "source_frames"}
    require(type(value) is dict and set(value) == keys and type(value["code"]) is str and value["code"] in GUARD_CODES)
    for key, catalogue in (("kind", "KINDS"), ("host_class", "HOSTS"), ("port_class", "PORTS"),
                           ("write_prefix_class", "PREFIXES")):
        require(type(value[key]) is str and value[key] in codec[catalogue])
    frames = value["source_frames"]
    require(type(frames) is list and 1 <= len(frames) <= 4)
    for frame in frames:
        require(type(frame) is dict and set(frame) == {"root", "file", "line", "function"})
        require(type(frame["root"]) is str and frame["root"] in codec["ROOTS"])
        if frame["root"] == "OTHER":
            require(frame == {"root": "OTHER", "file": None, "line": None, "function": None})
        else:
            require(type(frame["file"]) is str and frame["file"] in public_sources[frame["root"]])
            require(frame["line"] is None or type(frame["line"]) is int and 1 <= frame["line"] <= 1000000)
            name = frame["function"]
            require(name is None or type(name) is str and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", name)
                    or name in {"<module>", "<lambda>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"}))
    return value


def unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def guard_records(lines, codec, public_sources, *, verified_sigterm=False):
    require((1 if verified_sigterm else 2) <= len(lines) <= 64 and all(len(line) <= 8192 for line in lines))
    records = [json.loads(line, object_pairs_hook=unique_json_object) for line in lines]
    require(records.count({"code": "GUARD_READY"}) == 1 and records[0] == {"code": "GUARD_READY"})
    exits = records.count({"code": "GUARD_EXIT"})
    require(exits == 1 or exits == 0 and verified_sigterm is True)
    return [safe_diagnostic(record, codec, public_sources) for record in records
            if record not in ({"code": "GUARD_READY"}, {"code": "GUARD_EXIT"})]


def expected_ipv6_probe(packet, role, seen_roles=()):
    expected_codes = {"PARENT": "EXTERNAL_NETWORK", "ACTUAL_CANONICAL_PROXY": "LOOPBACK_FEATURE_PROBE"}
    return (role in expected_codes and role not in seen_roles and packet["code"] == expected_codes[role] and packet["kind"] == "BIND"
            and packet["host_class"] == "LOOPBACK_V6" and packet["port_class"] == "ZERO"
            and packet["write_prefix_class"] == "NOT_APPLICABLE" and packet["source_frames"][0] == {
                "root": "ENVIRONMENT", "file": "lib/python3.12/site-packages/urllib3/util/connection.py",
                "function": "_has_ipv6", "line": 127})


def verified_signal_stop(role, signals, returncode):
    return (role in {"ADAPTER_FAKE_AWS_BOUNDARY", "ACTUAL_CANONICAL_PROXY"}
            and signals == [signal.SIGTERM] and returncode == -signal.SIGTERM)


def check():
    """Pure reusable checks: no hooks, source copies, processes or sockets."""
    checks = 0

    def ok(value):
        nonlocal checks
        require(value)
        checks += 1

    def rejected(action):
        nonlocal checks
        try:
            action()
        except (FixtureError, ValueError, TypeError, KeyError):
            checks += 1
        else:
            raise FixtureError

    codec = {"root": "/public-fixture", "runtime": "/public-runtime", "public_sources": {}}
    exec(compile(DIAGNOSTIC_CODEC, "<fixed-pure-codec>", "exec"), codec)  # noqa: S102 -- definitions only; no hooks/I/O
    ready, exited = '{"code":"GUARD_READY"}', '{"code":"GUARD_EXIT"}'
    ok(guard_records([ready, exited], codec, {}) == [])
    ok(guard_records([ready], codec, {}, verified_sigterm=True) == [])
    for lines, allowed in [([], False), ([ready], False), ([exited], True), ([ready, ready], True),
                           ([ready, exited, exited], True), ([exited, ready], False),
                           ([ready, '{"code":"GUARD_READY","code":"GUARD_READY"}'], True),
                           ([ready, "malformed"], True), ([ready, '{"code":"UNKNOWN"}'], True),
                           (['{"code":"GUARD_READY","extra":true}', exited], False)]:
        rejected(lambda lines=lines, allowed=allowed: guard_records(lines, codec, {}, verified_sigterm=allowed))
    private_packet = codec["diagnostic"]("NON_RAM_WRITE", "WRITE", path="/private-marker-value")
    ok("private-marker-value" not in json.dumps(private_packet))
    ok(guard_records([ready, json.dumps(private_packet), exited], codec, {}) == [private_packet])
    packet = {"code": "EXTERNAL_NETWORK", "kind": "BIND", "host_class": "LOOPBACK_V6", "port_class": "ZERO",
        "write_prefix_class": "NOT_APPLICABLE", "source_frames": [{"root": "ENVIRONMENT",
            "file": "lib/python3.12/site-packages/urllib3/util/connection.py", "function": "_has_ipv6", "line": 127}]}
    for role, code in (("PARENT", "EXTERNAL_NETWORK"), ("ACTUAL_CANONICAL_PROXY", "LOOPBACK_FEATURE_PROBE")):
        value = {**packet, "code": code}
        ok(expected_ipv6_probe(value, role))
        ok(not expected_ipv6_probe(value, role, {role}))
        other = "PARENT" if role == "ACTUAL_CANONICAL_PROXY" else "ACTUAL_CANONICAL_PROXY"
        ok(expected_ipv6_probe(value, role, {other}))
        ok(not expected_ipv6_probe({**value, "code": packet["code"] if role != "PARENT" else "LOOPBACK_FEATURE_PROBE"}, role))
        for key, replacement in (("kind", "CONNECT"), ("host_class", "LOOPBACK_V4"), ("port_class", "OWNED"),
                                 ("write_prefix_class", "OUTSIDE_TMP")):
            ok(not expected_ipv6_probe({**value, key: replacement}, role))
        for key, replacement in (("root", "RUNTIME"), ("file", "other.py"), ("function", "other"), ("line", 128)):
            ok(not expected_ipv6_probe({**value, "source_frames": [{**value["source_frames"][0], key: replacement}]}, role))
        for wrong_role in ("ADAPTER_FAKE_AWS_BOUNDARY", "ACTUAL_HERMES_CHILD", "UNKNOWN"):
            ok(not expected_ipv6_probe(value, wrong_role))
    for role, sent, returncode, expected in (("ACTUAL_CANONICAL_PROXY", [15], -15, True),
            ("ADAPTER_FAKE_AWS_BOUNDARY", [15], -15, True), ("ACTUAL_HERMES_CHILD", [15], -15, False),
            ("ACTUAL_CANONICAL_PROXY", [15], 0, False), ("ACTUAL_CANONICAL_PROXY", [15], -9, False),
            ("ACTUAL_CANONICAL_PROXY", [], -15, False), ("ACTUAL_CANONICAL_PROXY", [15, 15], -15, False)):
        ok(verified_signal_stop(role, sent, returncode) == expected)

    # Exact public functions, inert Popen-compatible object: no os.kill or child.
    orchestrator = HERE / "orchestrator.py"
    source = orchestrator.read_bytes()
    ok(hashlib.sha256(source).hexdigest() == PINS["orchestrator.py"])
    stop = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "stop_process")
    recorder = next(node for node in ast.walk(ast.parse(Path(__file__).read_bytes()))
                    if isinstance(node, ast.FunctionDef) and node.name == "observed_signal")

    class InertProcess:
        returncode = None
        pid = 12345

        def poll(self):
            return self.returncode

        def send_signal(self, value):
            self.returncode = -value

        def wait(self, timeout):
            require(timeout == 15)
            return self.returncode

    process, proof = InertProcess(), {}
    namespace = {"subprocess": subprocess, "Any": object, "signal": signal, "sys": sys, "HERE": HERE,
        "phase": "ACTUAL_ORIGINAL_RUNNER", "normal_stop_signals": proof,
        "real_send_signal": process.send_signal, "child": process}
    exec(compile(ast.Module(body=[stop], type_ignores=[]), str(orchestrator), "exec"), namespace)  # noqa: S102 -- pinned public function; inert process
    exec(compile(ast.Module(body=[recorder], type_ignores=[]), str(Path(__file__).resolve()), "exec"), namespace)  # noqa: S102 -- this exact recorder; inert process
    process.send_signal = namespace["observed_signal"]
    namespace["stop_process"](process)
    ok(proof == {12345: [signal.SIGTERM]} and process.returncode == -signal.SIGTERM)
    process.returncode = None
    proof.clear()
    subprocess.Popen.terminate(process)
    ok(proof == {} and process.returncode == -signal.SIGTERM)
    return {"schema": "tessera.original_loopback_pure_checks.v1", "status": "PASS", "checks": checks,
        "actualProcessLaunches": 0, "actualSignals": 0, "actualNetworkCalls": 0, "actualListeners": 0,
        "actualProviderCalls": 0, "actualOAuthCalls": 0, "actualModelCalls": 0, "sourceCopies": 0,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def verify():
    logging.disable(logging.CRITICAL)
    sys.dont_write_bytecode = True
    os.umask(0o077)
    started = time.monotonic()
    phase = "SOURCE_PINS"
    reason = None
    run_uuid = str(uuid.uuid4())
    owned = Path("/dev/shm") / ("cm095-workload1-" + run_uuid)  # noqa: S108 -- exact new owned tmpfs fixture
    created = False
    processes = []
    roles = []
    counters = {"hosted_fetches_mocked": 0, "canonical_posts_mocked": 0}
    result = None
    checks = []
    violations = []
    guard_diagnostics = []
    guard_receipts = []
    expected_blocked_probes = []
    normal_stop_signals = {}
    ipv6_probe_source_verified = False
    codec = {}
    public_sources = {}
    old_handler = signal.signal(signal.SIGALRM, lambda *_args: (_ for _ in ()).throw(FixtureError()))
    signal.alarm(100)
    try:
        for name, expected in PINS.items():
            require(hashlib.sha256((HERE / name).read_bytes()).hexdigest() == expected)
        ipv6_source = Path(sys.prefix) / "lib/python3.12/site-packages/urllib3/util/connection.py"
        require(not ipv6_source.is_symlink() and ipv6_source.is_file() and ipv6_source.stat().st_size <= 65536
                and hashlib.sha256(ipv6_source.read_bytes()).hexdigest() == IPV6_PROBE_SOURCE_SHA256)
        ipv6_probe_source_verified = True
        require(closed(8786) and closed(8787))
        checks.append("ports_initially_closed")
        require(any(row.split()[1:3] == [str(owned.parent), "tmpfs"] for row in Path("/proc/mounts").read_text().splitlines()))
        owned.mkdir(mode=0o700, exist_ok=False)
        created = True
        home, bootstrap = owned / "home", owned / "bootstrap"
        home.mkdir(mode=0o700)
        bootstrap.mkdir(mode=0o700)
        (bootstrap / "sitecustomize.py").write_text(GUARD)
        adapter_path = bootstrap / "fixture_adapter.py"
        adapter_path.write_text(ADAPTER)
        phase = "PREPARE_AUTHENTICATED_SOURCE"
        factory = load("_joint_source_factory", HERE / "hermes_source.py")
        real_popen = subprocess.Popen
        def preparation_process(command, *args, **kwargs):
            require(isinstance(command, list) and command[0] == "/usr/bin/patch"
                    and kwargs.get("cwd") == owned / "hermes-source")
            child = real_popen(command, *args, **kwargs)
            processes.append(child)
            roles.append("AUTHENTICATED_SOURCE_PATCH")
            return child
        with patch.object(subprocess, "Popen", preparation_process):
            proof = factory.prepare_source(owned / "hermes-source", run_uuid)
        require(proof.get("status") == "PASS")
        checks.append("full_authenticated_source_prepared_in_owned_ram")
        phase = "PUBLIC_SOURCE_INVENTORY"
        version_dir = "python" + str(sys.version_info.major) + "." + str(sys.version_info.minor)
        environments = [Path(sys.prefix), Path("/tmp/nakul-cm104-hermes-venv-o2wxq6ao/venv")]  # noqa: S108 -- existing pinned venv, read-only source inventory
        stdlib = Path("/usr/lib") / version_dir
        public_sources = public_source_files({"FIXTURE": [(bootstrap, bootstrap)],
            "RUNTIME": [(RUNTIME / "tessera", RUNTIME), (HERE, RUNTIME)],
            "HERMES": [(owned / "hermes-source", owned / "hermes-source")],
            "ENVIRONMENT": [(environment / "lib" / version_dir / "site-packages", environment) for environment in environments],
            "STDLIB": [(stdlib, stdlib)]})
        (owned / "public-source-files.json").write_text(json.dumps(public_sources))
        codec = {"root": str(owned), "runtime": str(RUNTIME), "public_sources": public_sources}
        exec(compile(DIAGNOSTIC_CODEC, "<fixed-diagnostic-codec>", "exec"), codec)  # noqa: S102 -- fixed definitions only; no hooks/I/O
        checks.append("bounded_public_source_inventory")
        phase = "PARENT_GUARD_AND_IMPORTS"

        def parent_reject(code, kind, address=None, path=None):
            packet = safe_diagnostic(codec["diagnostic"](code, kind, address, path), codec, public_sources)
            seen_roles = {probe["process_role"] for probe in expected_blocked_probes}
            if ipv6_probe_source_verified and expected_ipv6_probe(packet, "PARENT", seen_roles):
                expected_blocked_probes.append({"process_role": "PARENT", **packet,
                                               "classification": "EXPECTED_BLOCKED_IPV6_FEATURE_PROBE"})
            else:
                violations.append(code)
                guard_diagnostics.append({"process_role": "PARENT", **packet})
            raise FixtureError

        def parent_audit(event, args):
            if event in {"socket.connect", "socket.bind"}:
                address = args[1]
                if not (isinstance(address, tuple) and len(address) == 2 and address[0] == "127.0.0.1" and address[1] in {8786, 8787}):
                    parent_reject("EXTERNAL_NETWORK", "CONNECT" if event == "socket.connect" else "BIND", address)
            if event == "socket.getaddrinfo" and not (args[0] == "127.0.0.1" and args[1] in {8786, 8787}):
                parent_reject("EXTERNAL_DNS", "DNS", args[:2])
            if event == "open" and args and isinstance(args[0], (str, bytes)):
                path = os.path.abspath(os.fsdecode(args[0]))
                if any(part in path.split(os.sep) for part in (".aws", ".azure", ".codex", ".ssh")) or os.path.basename(path) in {".netrc", "oauth.json", "credentials"}:
                    parent_reject("CREDENTIAL_PATH", "CREDENTIAL", path=path)
        sys.addaudithook(parent_audit)
        flow = load("_joint_original_flow", HERE / "original_flow.py")
        sys.path.insert(0, str(RUNTIME))
        import httpx

        from tessera.audit.canonical_json import canonical_json
        from tessera.audit.chain import HashChain
        from tessera.cloud_sync import CloudPolicySync, snapshot_digest

        real_popen, real_async_client = subprocess.Popen, httpx.AsyncClient

        def controlled_process(command, *args, **kwargs):
            require(isinstance(command, list))
            argv = list(command)
            if len(argv) >= 3 and argv[:2] == [sys.executable, "-B"] and argv[2] == str(HERE / "aws_readiness_server.py"):
                argv = [sys.executable, "-B", str(adapter_path), str(HERE / "aws_readiness_server.py"), *argv[3:]]
                role = "ADAPTER_FAKE_AWS_BOUNDARY"
            elif argv[:5] == [sys.executable, "-B", "-m", "tessera.cli", "serve"]:
                role = "ACTUAL_CANONICAL_PROXY"
            elif argv == [str(flow.HERMES_PYTHON), "-B", str(HERE / "original_flow_hermes_child.py")]:
                role = "ACTUAL_HERMES_CHILD"
            else:
                raise FixtureError
            env = dict(kwargs.get("env", {}))
            env["CM095_FIXTURE_ROOT"] = str(owned)
            env["CM095_FIXTURE_RUNTIME"] = str(RUNTIME)
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(bootstrap) + (os.pathsep + existing if existing else "")
            # Preserve production source/arguments; these are test-only safety
            # barriers and a private HOME, never SDK/dispatcher/transport mocks.
            env["HOME"] = str(home) if role != "ACTUAL_HERMES_CHILD" else env["HOME"]
            for key in list(env):
                if key.startswith("AWS_") or key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                    env.pop(key)
            kwargs["env"] = env
            child = real_popen(argv, *args, **kwargs)
            processes.append(child)
            roles.append(role)
            real_send_signal = child.send_signal
            def observed_signal(value, original=real_send_signal, process=child):
                caller = sys._getframe(1)
                production_stop = (caller.f_code.co_name == "stop_process"
                    and caller.f_code.co_filename == str(HERE / "orchestrator.py")
                    and phase == "ACTUAL_ORIGINAL_RUNNER" and process.poll() is None)
                original(value)
                if production_stop:
                    normal_stop_signals.setdefault(process.pid, []).append(value)
            child.send_signal = observed_signal
            return child

        async def synthetic_fetch(sync):
            counters["hosted_fetches_mocked"] += 1
            items = [{"policy_id": "readiness-diagnostic-review", "enabled": True,
                      "yaml_text": (HERE / "readiness-review.yaml").read_text()},
                     {"policy_id": "workload-tessera-read", "enabled": True,
                      "yaml_text": (HERE / "workload-read-allow.yaml").read_text()}]
            return sync._cache_verified_snapshot({"complete": True, "tenant_id": TENANT, "items": items,
                                                  "snapshot_digest": snapshot_digest(items)})

        def hosted_ack(request):
            require(str(request.url) == "https://auth.tessera.cloudmorph.ai/api/tessera/audit/ingest"
                    and request.method == "POST")
            body = json.loads(request.content)
            previous = ""
            for index, part in enumerate(body["events"], start=1):
                event = part["event"]
                require(part["seq"] == index and event["tenantId"] == TENANT and HashChain.verify_event_hash(event)
                        and event.get("prevEventHash", "") == previous)
                previous = event["eventHash"]
            evidence = body["run_evidence"]
            require(evidence["provenance"] == "client_reported" and evidence["verification"]["accepted"] is True)
            require(len(evidence["tool_trace"]) == 3 and evidence["verification"]["denied_contacts"] == 0)
            counters["canonical_posts_mocked"] += 1
            return httpx.Response(200, json={"accepted_event_ids": [part["event"]["eventId"] for part in body["events"]],
                "skipped": 0, "tenant_id": TENANT, "stream_id": body["stream_id"], "chain_head": previous,
                "verification": "canonical_verified", "written": len(body["events"]), "duplicates": 0,
                "run_evidence_stored": True, "run_evidence_digest": hashlib.sha256(canonical_json(evidence)).hexdigest()})

        def bounded_hosted_client(*args, **kwargs):
            kwargs.update(transport=httpx.MockTransport(hosted_ack), trust_env=False, follow_redirects=False)
            return real_async_client(*args, **kwargs)

        admission = {"schema": "tessera.original_flow_admission.v1", "sourcePins": {key: PINS[key] for key in flow.SOURCE_NAMES},
            "hermesPython": str(flow.HERMES_PYTHON), "hermesSource": proof["source_path"],
            "hermesFullSourceReceiptSha256": proof["source_receipt_sha256"],
            "hermesDependencyReceiptSha256": proof["dependency_receipt_sha256"],
            "hermesFullSourceAuthenticated": True, "hermesDependencyReviewed": True}
        fixture_env = {"HOME": str(home), "XDG_CACHE_HOME": str(home / "cache"), "XDG_STATE_HOME": str(home / "state")}
        phase = "ACTUAL_ORIGINAL_RUNNER"
        with (patch.dict(os.environ, fixture_env), patch.object(subprocess, "Popen", controlled_process),
              patch.object(CloudPolicySync, "fetch_and_cache", synthetic_fetch),
              patch.object(httpx, "AsyncClient", bounded_hosted_client)):
            for key in list(os.environ):
                if key.startswith(("AWS_", "TESSERA_")) or key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                    del os.environ[key]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = flow.run_original_flow(oauth_token="synthetic-no-real-grant",  # noqa: S106 -- explicit fixture
                    verified_access_claims={"tenant_id": TENANT, "client_id": "tessera-cli",
                                           "scope": "tessera:policies:read tessera:audit:write"},
                    output=owned / "original", run_id="original1-" + run_uuid, admission=admission, runtime_admitted=True)
        require(result["status"] == "ACKNOWLEDGED" and result["accepted"] is True
                and result["canonical_upload_acknowledged"] is True and result["all_children_stopped"] is True)
        require(counters == {"hosted_fetches_mocked": 1, "canonical_posts_mocked": 1})
        checks.append("actual_runner_real_proxy_real_hermes_three_correlated_decisions")
        checks.append("canonical_sqlite_chain_and_exact_client_projection_mock_ack")
        with sqlite3.connect(owned / "original/audit.db") as connection:
            events = [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM audit_events ORDER BY seq")]
        decisions = [event["payload"]["decision"] for event in events if event.get("payload", {}).get("decision") in {"allow", "block", "require_approval"}]
        require(decisions == ["allow", "block", "require_approval"])
        checks.append("actual_allow_block_review_order_and_no_model")
        phase = "COMPLETE"
    except BaseException as error:
        reason = getattr(error, "code", None)
        if reason not in {"HERMES_TASK_FAILED", "HERMES_OUTPUT_REJECTED", "ACTIVATION_REJECTED", "OUTCOME_REJECTED",
                          "ACK_REJECTED", "CLEANUP_REQUIRED", "FLOW_FAILED", "DEADLINE"}:
            reason = "JOINT_FIXTURE_FAILED"
    finally:
        signal.alarm(20)
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
        if created:
            process_roles = {child.pid: role for child, role in zip(processes, roles, strict=True)
                             if role != "AUTHENTICATED_SOURCE_PATCH"}
            logs_by_pid = {pid: [] for pid in process_roles}
            for guardfile in owned.glob("guard-*.jsonl"):
                match = re.fullmatch(r"guard-([0-9]{1,10})\.jsonl", guardfile.name)
                if match is None or int(match[1]) not in logs_by_pid:
                    violations.append("GUARD_RECEIPT_UNEXPECTED")
                    continue
                logs_by_pid[int(match[1])].append(guardfile)
            for pid, role in process_roles.items():
                logs = logs_by_pid[pid]
                if len(logs) != 1:
                    code = "GUARD_RECEIPT_MISSING" if not logs else "GUARD_RECEIPT_DUPLICATE"
                    violations.append(code)
                    guard_receipts.append({"process_role": role, "status": code})
                    continue
                try:
                    guardfile = logs[0]
                    require(not guardfile.is_symlink() and guardfile.stat().st_size <= 262144)
                    lines = guardfile.read_text().splitlines()
                    stopped_by_signal = verified_signal_stop(role, normal_stop_signals.get(pid, []),
                        next(child.returncode for child in processes if child.pid == pid))
                    for packet in guard_records(lines, codec, public_sources, verified_sigterm=stopped_by_signal):
                        seen_roles = {probe["process_role"] for probe in expected_blocked_probes}
                        if ipv6_probe_source_verified and expected_ipv6_probe(packet, role, seen_roles):
                            expected_blocked_probes.append({"process_role": role, **packet,
                                "classification": "EXPECTED_BLOCKED_IPV6_FEATURE_PROBE"})
                            continue
                        violations.append(packet["code"])
                        require(len(guard_diagnostics) < 64)
                        guard_diagnostics.append({"process_role": role, **packet})
                    exit_seen = any(json.loads(line) == {"code": "GUARD_EXIT"} for line in lines)
                    guard_receipts.append({"process_role": role,
                        "status": "READY_AND_EXIT_VERIFIED" if exit_seen else "READY_AND_VERIFIED_SIGTERM_STOP"})
                except (FixtureError, ValueError, TypeError, KeyError, OSError):
                    violations.append("GUARD_RECEIPT_MALFORMED_OR_INCOMPLETE")
                    guard_receipts.append({"process_role": role, "status": "GUARD_RECEIPT_MALFORMED_OR_INCOMPLETE"})
            if owned.exists():
                require(owned.parent == Path("/dev/shm") and owned.name == "cm095-workload1-" + run_uuid  # noqa: S108 -- exact checked cleanup boundary
                        and owned.resolve() == owned and not owned.is_symlink() and owned.stat().st_uid == os.getuid())
                shutil.rmtree(owned)
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
    stopped = all(child.poll() is not None for child in processes)
    ports_closed = closed(8786) and closed(8787)
    passed = phase == "COMPLETE" and reason is None and not violations and stopped and ports_closed and not owned.exists()
    return {"schema": "tessera.original_joint_loopback_fixture.v2", "status": "PASS" if passed else "FAIL",
        "phase": phase, "reason_code": reason, "checks": checks, "guard_violations": violations,
        "guard_diagnostics": guard_diagnostics, "prior_attempt_source_sha256": PRIOR_ATTEMPT_SOURCE_SHA256,
        "guard_receipts": guard_receipts,
        "expected_blocked_probes": expected_blocked_probes,
        "ipv6_probe_source": {"sha256": IPV6_PROBE_SOURCE_SHA256, "verified": ipv6_probe_source_verified},
        "actual_local_processes": len(processes), "process_roles": roles, "all_owned_children_stopped": stopped,
        "ports_8786_8787_closed": ports_closed, "owned_ram_removed": not owned.exists(),
        "elapsed_seconds": round(time.monotonic() - started, 3), **counters,
        "actualExternalNetworkCalls": 0, "blockedExternalAttempts": sum(code in {"EXTERNAL_NETWORK", "EXTERNAL_DNS"} for code in violations),
        "actualProviderCalls": violations.count("PROVIDER_ATTEMPT"), "actualOAuthCalls": 0, "actualModelCalls": 0,
        "actualCloudWrites": 0, "fixture_grant_only": True,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "component_pins": PINS,
        "scope": "REAL LOCAL LOOPBACK/SDK/PROXY; SYNTHETIC HOSTED AND AWS BOUNDARIES; NOT LIVE ACCEPTANCE"}


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            report = check()
        elif not sys.argv[1:]:
            report = verify()
        else:
            print('{"status":"FAIL","reason_code":"ARGUMENTS_REJECTED"}', flush=True)
            raise SystemExit(2)
        print(json.dumps(report, sort_keys=True), flush=True)
    except SystemExit:
        raise
    except BaseException:
        print('{"status":"FAIL","reason_code":"JOINT_FIXTURE_FINALIZATION_FAILED"}', flush=True)
        raise SystemExit(1) from None
