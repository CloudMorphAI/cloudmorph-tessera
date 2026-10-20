"""Portable, offline source payload for the fixed two-client example.

build_payload(plan) validates a complete18-file source manifest, reads exactly
five local example files and binds their hashes. bootstrap(payload, mode) is the
actual check/serve launcher: same Python PID, private0700 tmpfs, exact cleanup,
and removal of ONLY three known inherited image path variables. It creates no
cloud resources and supplies no credentials. Live use still needs an independently
reviewed plan, image manifest, tenant/role/ingress and fresh authorization.
Do not print the compressed command or place raw bearer tokens in a plan.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from pathlib import Path

from serve_external_clients import SOURCE_FILES, read, validate

SOURCE_DIR = Path(__file__).resolve().parent
PREFIX = "examples/external_client_demo/"
FILES = tuple(Path(name).name for name in SOURCE_FILES if name.startswith(PREFIX))
CANONICAL = frozenset(name for name in SOURCE_FILES if name.startswith("tessera/"))
HASH = re.compile(r"[a-f0-9]{64}\Z")


def need(value):
    if not value:
        raise ValueError("TASK_PAYLOAD_REJECTED")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def local_source_hashes():
    return {PREFIX + name: digest(read(SOURCE_DIR / name)) for name in FILES}


def build_payload(plan):
    manifest = plan.get("sourceSha256", {})
    need(type(manifest) is dict and set(manifest) == set(SOURCE_FILES)
         and all(type(value) is str and HASH.fullmatch(value) for value in manifest.values()))
    validate(plan, manifest)
    sources = {}
    for name in FILES:
        raw = read(SOURCE_DIR / name, manifest[PREFIX + name], limit=262144)
        sources[name] = {"sha256": digest(raw), "base64": base64.b64encode(raw).decode("ascii")}
    raw_plan = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {"schema": "cloudmorph.two_client_ecs_source_payload.v1", "runUuid": plan["runId"][10:],
            "serverPlanSha256": digest(raw_plan), "serverPlanBase64": base64.b64encode(raw_plan).decode("ascii"),
            "files": sources}


def bootstrap(payload, mode):
    need(mode in {"check", "serve"})
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
    # All substitution values are fixed encodings/enum values, never shell code.
    script = '''import base64,hashlib,json,os,runpy,shutil,stat,sys,tempfile,zlib
sys.dont_write_bytecode=True
def need(v):
    if not v: raise ValueError("TASK_PAYLOAD_REJECTED")
decoder=zlib.decompressobj()
raw=decoder.decompress(base64.b64decode("BLOB",validate=True),1048577)
need(len(raw)<=1048576 and decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data and hashlib.sha256(raw).hexdigest()=="RAW_SHA")
payload=json.loads(raw)
names={"attachment.py","deployment_adapter.py","external-read-allow.yaml","external_client_auth.py","serve_external_clients.py"}
need(payload["schema"]=="cloudmorph.two_client_ecs_source_payload.v1" and set(payload["files"])==names)
need(os.getuid()==10001 and any(row.split()[1:3]==["/dev/shm","tmpfs"] for row in open("/proc/mounts")))
owned=tempfile.mkdtemp(prefix="cm104-two-"+payload["runUuid"]+"-",dir="/dev/shm")
os.chmod(owned,0o700)
parent=os.path.realpath(owned)
pid=os.getpid()
removed=False
try:
    folder=os.path.join(owned,"examples","external_client_demo")
    os.mkdir(os.path.join(owned,"examples"),0o700)
    os.mkdir(folder,0o700)
    for name,item in payload["files"].items():
        source=base64.b64decode(item["base64"],validate=True)
        need(len(source)<=262144 and hashlib.sha256(source).hexdigest()==item["sha256"])
        fd=os.open(os.path.join(folder,name),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as out: out.write(source)
    plan=base64.b64decode(payload["serverPlanBase64"],validate=True)
    need(hashlib.sha256(plan).hexdigest()==payload["serverPlanSha256"])
    plan_path=os.path.join(owned,"server-plan.json")
    fd=os.open(plan_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as out: out.write(plan)
    sys.path.insert(0,folder)
    entry=os.path.join(folder,"serve_external_clients.py")
    sys.argv=[entry,"--PAYLOAD_EXEC_MODE_VALUE","--plan",plan_path,"--plan-sha256",payload["serverPlanSha256"]]
    for variable in ("TESSERA_AUDIT_PATH","TESSERA_CONFIG_PATH","TESSERA_POLICY_DIR"):
        os.environ.pop(variable,None)
    runpy.run_path(entry,run_name="__main__")
finally:
    info=os.lstat(owned)
    need(os.path.dirname(parent)=="/dev/shm" and os.path.realpath(owned)==parent and not stat.S_ISLNK(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700)
    shutil.rmtree(owned)
    removed=not os.path.lexists(owned)
    print(json.dumps({"schema":"cloudmorph.two_client_payload_cleanup.v1","executionMode":"PAYLOAD_EXEC_MODE_VALUE","ownedRootRemoved":removed,"samePythonPid":pid==os.getpid(),"sourceFiles":5}),flush=True)
'''
    script = script.replace("PAYLOAD_EXEC_MODE_VALUE", mode).replace("RAW_SHA", digest(raw)).replace("BLOB", encoded)
    need(len(script.encode("utf-8")) <= 65536)
    return script
