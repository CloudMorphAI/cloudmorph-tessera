"""Credential-free exact-image startup regression for a clean checkout.

python examples/external_client_demo/startup_regression.py --image EXISTING_TAG \
    --image-digest sha256:ACTUAL_REPO_DIGEST --receipt NEW_PATH.json

Requires Linux Docker and an existing image containing Tessera, boto3 and uvicorn.
For an unpublished local image use --image-id sha256:EXACT_CONFIG_ID instead of
--image-digest. Those distinct identities are never substituted for one another.
Never pulls/builds an image, reads AWS credentials, or uses an approval packet.
The image's canonical13-file manifest is discovered inside that exact image;
the five local source hashes are then bound to a new synthetic plan. Discovery
is local evidence, not an admission of those bytes for a live deployment.
All containers use --network=none/read-only/nonroot/cap-drop. The actual shared
payload bootstrap runs --check, an unknown auth override, and --serve with an
explicit in-process synthetic STS/Amplify provider. Only private health is probed;
SIGTERM must drain the real server and remove both owned RAM trees. No model or
two-client acceptance is claimed. Receipt creation is exclusive; keep old files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import task_payload as builder

FINITE_KEYS = {
    "status", "errorCode", "failure", "serverStopped", "portsClosed", "privateRamRemoved", "schema",
    "ownedRootRemoved", "samePythonPid", "executionMode", "synthetic_startup_health200", "termination_signal",
    "synthetic_final", "health200", "monitor_finished", "ports_closed", "owned_ram_absent",
    "synthetic_sts_calls", "synthetic_amplify_calls", "synthetic_provider_clients", "negative_before_provider",
    "real_sdk_calls", "SDKCalls", "networkCalls", "loopbackProbes", "externalNetworkCalls", "listeners",
    "loopback_health_probes", "loopback_cleanup_port_probes",
}

# Explicit synthetic provider, installed before executing the actual bootstrap.
# No real boto3 client can be constructed through this Session implementation.
SYNTHETIC_PROVIDER = '''import json,os,signal,threading,time,urllib.request,urllib.error,socket,glob
import boto3
counts={"synthetic_sts_calls":0,"synthetic_amplify_calls":0,"synthetic_provider_clients":0,"real_sdk_calls":0}
class Stub:
    def get_caller_identity(self):
        counts["synthetic_sts_calls"]+=1
        return {"Account":"237509402889","Arn":"arn:aws:sts::237509402889:assumed-role/local-startup-fixture/synthetic"}
    def get_job(self,**kwargs):
        counts["synthetic_amplify_calls"]+=1
        raise AssertionError("Deployment reads do not belong in startup regression")
    def close(self): pass
class Session:
    def __init__(self,**kwargs): pass
    def client(self,name,**kwargs):
        counts["synthetic_provider_clients"]+=1
        if name not in {"sts","amplify"}: raise AssertionError("Unexpected synthetic provider")
        return Stub()
boto3.Session=Session
health={"health200":False,"monitor_finished":False,"loopback_health_probes":0}
def monitor():
    end=time.monotonic()+25
    while time.monotonic()<end:
        try:
            health["loopback_health_probes"]+=1
            with urllib.request.urlopen("http://127.0.0.1:8080/healthz",timeout=0.3) as reply:
                if reply.status==200:
                    health["health200"]=True
                    print(json.dumps({"synthetic_startup_health200":True,"termination_signal":"SIGTERM"}),flush=True)
                    time.sleep(0.2)
                    os.kill(os.getpid(),signal.SIGTERM)
                    break
        except (OSError,urllib.error.URLError): pass
        time.sleep(0.1)
    health["monitor_finished"]=True
'''


def require(value):
    if not value:
        raise ValueError("LOCAL_REGRESSION_REJECTED")


class OutputLimitExceeded(subprocess.SubprocessError):
    """Finite diagnostic; never include a command/blob or captured output."""


def bounded_run(args, *, timeout, limit=65536):
    """Drain both pipes while running; cap EACH stream and kill the owned PID.

    Linux selectors avoid reader threads and unbounded communicate() buffering.
    At most limit bytes per retained stream plus one8192-byte read are allocated.
    Overflow/timeout kills and reaps this CLI; execute() still removes its exact
    container in finally. Return CompletedProcess with the existing bytes API.
    """
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)  # noqa: S603 -- shell-free explicit local command
    retained = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("owned local CLI", timeout)
                for key, _mask in selector.select(remaining):
                    chunk = os.read(key.fd, min(8192, limit - len(retained[key.data]) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(retained[key.data]) + len(chunk) > limit:
                        raise OutputLimitExceeded("LOCAL_CLI_" + key.data.upper() + "_LIMIT_EXCEEDED")
                    retained[key.data].extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("owned local CLI", timeout)
            process.wait(timeout=remaining)
        return subprocess.CompletedProcess(args, process.returncode, bytes(retained["stdout"]), bytes(retained["stderr"]))
    finally:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        finally:
            process.stdout.close()
            process.stderr.close()


class IsolatedDocker:
    def __init__(self, image, digest, docker, *, image_id=None):
        require(type(image) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}", image))
        require((digest is None) != (image_id is None))
        require(type(digest or image_id) is str and re.fullmatch(r"sha256:[a-f0-9]{64}", digest or image_id))
        self.docker, self.image, self.owned_containers_absent = docker, image, True
        result = self.call(["image", "inspect", image], timeout=10)
        require(result.returncode == 0)
        images = json.loads(result.stdout)
        require(len(images) == 1)
        if image_id is not None:
            require(images[0].get("Id") == image_id)
        else:
            require(any(ref.endswith("@" + digest) for ref in images[0].get("RepoDigests", [])))
        self.image_id = images[0]["Id"]
        require(type(self.image_id) is str and re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_id))

    def call(self, args, timeout):
        return bounded_run([self.docker, *args], timeout=timeout)

    def execute(self, command, extra=()):
        name = "tessera-startup-" + uuid.uuid4().hex
        args = ["run", "--rm", "--name=" + name, "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user=10001:10001", "--pids-limit=64", "--memory=1024m", "--cpus=0.5",
                "--env=PYTHONDONTWRITEBYTECODE=1", "--env=TMPDIR=/dev/shm", "--env=AWS_EC2_METADATA_DISABLED=true",
                "--env=AWS_SHARED_CREDENTIALS_FILE=/dev/null", "--env=AWS_CONFIG_FILE=/dev/null",
                *extra, "--entrypoint=python", self.image_id, "-B", "-c", command]
        try:
            result = self.call(args, timeout=50)
            require(len(result.stdout) <= 65536 and len(result.stderr) <= 65536)
            records = []
            for line in result.stdout.splitlines():
                try:
                    value = json.loads(line)
                    if type(value) is dict:
                        records.append(value)
                except ValueError:
                    pass
            return result, records
        finally:
            # Only the exact generated owned container name is ever removed.
            try:
                self.call(["rm", "-f", name], timeout=10)
                inspected = self.call(["container", "inspect", name], timeout=10)
                daemon = self.call(["info", "--format", "{{.ServerVersion}}"], timeout=10)
                absent = (inspected.returncode == 1 and inspected.stdout == b"[]\n"
                          and inspected.stderr == ("Error response from daemon: No such container: " + name + "\n").encode()
                          and daemon.returncode == 0 and bool(daemon.stdout.strip()))
            except (OSError, subprocess.SubprocessError):
                absent = False
            self.owned_containers_absent = self.owned_containers_absent and absent


def synthetic_plan(manifest):
    """Nonsecret fixture only: verifiers have no corresponding client credential."""
    return {"schema": "tessera.external_client_plan.v1", "runId": "external2-" + str(uuid.uuid4()),
            "tenant": "local_startup_fixture", "publicOrigin": "https://local-startup.invalid",
            "listenHost": "0.0.0.0", "listenPort": 8080, "adapterPort": 8786,  # noqa: S104 -- isolated Docker network namespace
            "maxSeconds": 60, "expiresAtEpoch": int(time.time()) + 1700,
            "clientTokenSha256": {name: hashlib.sha256(("synthetic-verifier-only-" + name).encode()).hexdigest()
                                  for name in ("opencode", "codex")},
            "expectedCallerArnPrefix": "arn:aws:sts::237509402889:assumed-role/local-startup-fixture/",
            "sourceSha256": manifest}


def startup_command(payload):
    actual = repr(builder.bootstrap(payload, "serve"))
    return SYNTHETIC_PROVIDER + '''
threading.Thread(target=monitor,daemon=True).start()
try:
    exec(compile(ACTUAL_BOOTSTRAP,'<actual-payload-bootstrap>','exec'))
except SystemExit as error:
    code=error.code
finally:
    closed=[]
    for port in (8080,8786):
        with socket.socket() as s:
            s.settimeout(0.2);closed.append(s.connect_ex(('127.0.0.1',port))!=0)
    print(json.dumps({'synthetic_final':True,**counts,**health,'loopback_cleanup_port_probes':2,'externalNetworkCalls':0,
        'ports_closed':all(closed),'owned_ram_absent':not glob.glob('/dev/shm/cm104-two-*') and not glob.glob('/dev/shm/cm-external2-*')}),flush=True)
'''.replace("ACTUAL_BOOTSTRAP", actual)


def case_passed(label, code, records):
    cleanup = any(row.get("schema") == "cloudmorph.two_client_payload_cleanup.v1"
                  and row.get("ownedRootRemoved") is True and row.get("samePythonPid") is True for row in records)
    if label == "normal_check":
        return code == 0 and cleanup and any(row.get("status") == "LOCAL_COMPONENTS_VALID"
            and row.get("planValidated") is True
            and all(type(row.get(key)) is int and row[key] == 2 for key in ("networkCalls", "loopbackProbes"))
            and all(type(row.get(key)) is int and row[key] == 0 for key in ("externalNetworkCalls", "SDKCalls", "listeners")) for row in records)
    if label == "unknown_auth_override":
        return code == 2 and cleanup and any(row.get("status") == "BLOCKED" for row in records) and any(
            row.get("negative_before_provider") is True and row.get("synthetic_provider_clients") == 0 for row in records)
    return code == 0 and cleanup and any(row.get("synthetic_final") and row.get("health200") and row.get("ports_closed")
        and row.get("owned_ram_absent") and row.get("synthetic_sts_calls") == 1
        and row.get("synthetic_amplify_calls") == 0 and row.get("real_sdk_calls") == 0 for row in records) and any(
            row.get("schema") == "tessera.external_client_server_receipt.v1" and "failure" in row and row["failure"] is None
            and all(row.get(key) is True for key in ("serverStopped", "portsClosed", "privateRamRemoved")) for row in records)


def run(*, image, receipt, image_digest=None, image_id=None, docker="docker"):
    """Write a NEW finite receipt; return true only for all cases and cleanup."""
    executable = shutil.which(docker)
    require(executable is not None)
    # Reserve output before any container starts; never replace a prior receipt.
    fd = os.open(Path(receipt), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    result = {"schema": "tessera.external_client_local_startup_regression.v1", "status": "LOCAL_STARTUP_REGRESSION_BLOCKED",
              "synthetic": True, "liveTwoClientAcceptanceClaimed": False, "image": image, "imageDigest": image_digest,
              "imageBinding": {"type": "local_config_id" if image_id is not None else "repository_manifest_digest",
                               "expected": image_id if image_id is not None else image_digest},
              "externalNetworkCalls": 0, "loopbackTrafficUsed": True, "actualCredentialsUsed": False,
              "realSDKCalls": 0, "AWSCalls": 0, "modelCalls": 0, "OAuthCalls": 0, "deploymentCalls": 0,
              "ownedContainersAbsent": False, "cases": {}}
    isolated = None
    try:
        isolated = IsolatedDocker(image, image_digest, executable, image_id=image_id)
        result["localImageId"] = isolated.image_id
        discovery = ("import hashlib,json,pathlib,tessera;root=pathlib.Path(tessera.__file__).resolve().parent.parent;"
                     "print(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in "
                     + repr(sorted(builder.CANONICAL)) + "}))")
        checked, records = isolated.execute(discovery)
        require(checked.returncode == 0 and len(records) == 1 and set(records[0]) == builder.CANONICAL)
        manifest = {**records[0], **builder.local_source_hashes()}
        payload = builder.build_payload(synthetic_plan(manifest))
        result["executionSourceSha256"] = manifest
        result["helperSourceSha256"] = {path.name: builder.digest(builder.read(path)) for path in (
            Path(__file__), Path(builder.__file__))}
        for label in ("normal_check", "unknown_auth_override", "synthetic_startup"):
            extra = ()
            if label == "synthetic_startup":
                command = startup_command(payload)
            elif label == "unknown_auth_override":
                extra = ("--env=TESSERA_AUTHENTICATOR=synthetic.invalid:NeverLoad",)
                command = SYNTHETIC_PROVIDER + "\ntry:\n    exec(compile(" + repr(builder.bootstrap(payload, "check")) + ",'<actual-payload-bootstrap>','exec'))\nfinally:\n    print(json.dumps({'negative_before_provider':counts['synthetic_provider_clients']==0,**counts}),flush=True)\n"
            else:
                command = builder.bootstrap(payload, "check")
            checked, records = isolated.execute(command, extra)
            result["cases"][label] = {"pass": case_passed(label, checked.returncode, records), "exitCode": checked.returncode,
                "finiteRecords": [{key: value for key, value in row.items() if key in FINITE_KEYS} for row in records],
                "stderrPresent": bool(checked.stderr), "stderrSha256": builder.digest(checked.stderr)}
        if all(row["pass"] for row in result["cases"].values()) and isolated.owned_containers_absent:
            result["status"] = "LOCAL_STARTUP_REGRESSION_PASS"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result["failure"] = "LOCAL_IMAGE_SOURCE_STARTUP_OR_CLEANUP_UNPROVED"
    finally:
        result["ownedContainersAbsent"] = isolated is not None and isolated.owned_containers_absent
        with os.fdopen(fd, "w") as out:
            json.dump(result, out, indent=2, sort_keys=True)
            out.write("\n")
    print(json.dumps({"status": result["status"], "cases": {key: row["pass"] for key, row in result["cases"].items()},
                      "ownedContainersAbsent": result["ownedContainersAbsent"],
                      "receiptSha256": builder.digest(Path(receipt).read_bytes())}, sort_keys=True))
    return result["status"] == "LOCAL_STARTUP_REGRESSION_PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--image-digest")
    identity.add_argument("--image-id")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    return 0 if run(image=args.image, image_digest=args.image_digest, image_id=args.image_id,
                   receipt=args.receipt, docker=args.docker) else 1


if __name__ == "__main__":
    raise SystemExit(main())
