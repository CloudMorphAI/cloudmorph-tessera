"""Offline strict-snapshot/readiness regression check; no listener or live services.

Run from the repository with its installed runtime dependencies:
    python examples/control_layer_demo/verify_readiness_activation.py

Only the public readiness YAML is read. All synthetic cache, state and canonical
audit bytes live in a private temporary /dev/shm directory and are removed. This
proves local frozen activation and startup audit, not hosted activation, upstream
enforcement, audit upload, OAuth or independent-agent/business acceptance.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import signal
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
POLICY_ID = "readiness-diagnostic-review"
POLICY_SHA256 = "772a7f7db2b018839153e74d094f7605d793944ee10335c09baa4561b7895112"
TENANT = "offline_readiness_fixture"
ENDPOINT = "https://offline.invalid"


class OfflineCheckError(Exception):
    """Finite failure without private details."""


def require(condition: bool) -> None:
    if not condition:
        raise OfflineCheckError


def run() -> dict:
    # Do not inspect inherited values or ordinary runtime configuration/caches.
    for key in list(os.environ):
        if key.startswith("TESSERA_"):
            del os.environ[key]
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(REPO))
    forbidden_attempts = 0
    checks: list[str] = []

    def forbidden(*_args, **_kwargs):
        nonlocal forbidden_attempts
        forbidden_attempts += 1
        raise OfflineCheckError

    def forbid_routines(frame, event, _arg):
        if event != "call":
            return
        module = frame.f_globals.get("__name__", "")
        # Block credential providers, cloud SDK use, agents and cloud upload even
        # if a future startup change accidentally reaches them before a socket.
        if ((module.startswith(("boto3", "botocore", "aiobotocore", "aioboto3"))
             and frame.f_code.co_name in {"client", "resource", "create_client", "get_credentials",
                                          "get_frozen_credentials", "_make_api_call"})
                or (module.startswith("tessera.audit.cloud")
                    and frame.f_code.co_name in {"upload", "flush", "upload_batch", "_post"})
                or (module.startswith("tessera.auth.jwt")
                    and frame.f_code.co_name in {"prewarm", "authenticate", "fetch_jwks"})
                or ("control_layer_demo" in frame.f_code.co_filename
                    and frame.f_code.co_name in {"session_login", "run_agent", "run_model"})):
            forbidden()

    mounts = Path("/proc/mounts").read_text().splitlines()
    require(any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in mounts))  # noqa: S108 - verified RAM mount
    with contextlib.ExitStack() as guards:
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "socket.getaddrinfo"):
            guards.enter_context(patch(target, forbidden))
        sys.setprofile(forbid_routines)
        guards.callback(sys.setprofile, None)
        # Imports occur only after network tripwires are installed.
        import httpx

        from tessera.audit.chain import HashChain
        from tessera.cloud_sync import CloudPolicySync, snapshot_digest
        from tessera.config import TesseraConfig
        from tessera.errors import PolicyError
        from tessera.policy import cloud_loader
        from tessera.proxy import create_app

        require(Path(cloud_loader.__file__).resolve() == REPO / "tessera/policy/cloud_loader.py")
        guards.enter_context(patch.object(cloud_loader, "_read_oauth_tokens", forbidden))
        guards.enter_context(patch.object(CloudPolicySync, "fetch_and_cache", forbidden))
        original_client = httpx.AsyncClient

        def offline_client(*args, **kwargs):
            if not isinstance(kwargs.get("transport"), httpx.ASGITransport):
                forbidden()
            return original_client(*args, **kwargs)

        guards.enter_context(patch.object(httpx, "AsyncClient", offline_client))
        guards.enter_context(patch.object(httpx, "Client", forbidden))
        body = (Path(__file__).parent / "readiness-review.yaml").read_bytes()
        require(hashlib.sha256(body).hexdigest() == POLICY_SHA256)
        items = [{"policy_id": POLICY_ID, "enabled": True, "yaml_text": body.decode()}]
        digest = snapshot_digest(items)
        with tempfile.TemporaryDirectory(prefix="tessera-offline-readiness-", dir="/dev/shm") as tmp:
            ram = Path(tmp)
            require(ram.stat().st_mode & 0o077 == 0)
            local = ram / "policies"
            local.mkdir()
            cache = ram / "policy-cache.db"
            sync = CloudPolicySync(endpoint=ENDPOINT, cache_path=cache, strict=True,
                                   expected_tenant_id=TENANT)
            sync._cache_verified_snapshot({"complete": True, "tenant_id": TENANT,
                                           "items": items, "snapshot_digest": digest})
            require(cache.stat().st_mode & 0o077 == 0)
            os.environ.update({
                "TESSERA_CLOUD_STRICT": "1", "TESSERA_CLOUD_TENANT": TENANT,
                "TESSERA_CLOUD_ENDPOINT": ENDPOINT, "TESSERA_POLICY_CACHE": str(cache),
                "TESSERA_EXPECTED_SNAPSHOT": digest, "TESSERA_EXPECTED_POLICY_ID": POLICY_ID,
                "TESSERA_EXPECTED_POLICY_SHA256": POLICY_SHA256,
                "TESSERA_STATE_DIR": str(ram / "state"),
                "TESSERA_POLICY_LOADER": "tessera.policy.cloud_loader:CloudBackedPolicyLoader",
                "TESSERA_BEARER_TOKENS": "offline_readiness_fixture:public_offline_fixture_token_32chars",
            })
            checks.append("STRICT_VERIFIED_CACHE")

            async def activation():
                cfg = TesseraConfig.model_validate({
                    "deployment_id": TENANT, "upstreams": [],
                    "auth": {"type": "bearer"},
                    "audit": {"path": str(ram / "audit.db"), "also_stdout": False,
                              "payload_mode": "minimal"},
                    "policies": {"dir": str(local), "reload": "none",
                                 "mode": "enforcement", "default_action": "block"},
                    "intelligence": {"enabled": False, "selected_packs": []},
                })
                app = create_app(cfg)
                async with app.router.lifespan_context(app):
                    require(len(app.state.base_policies) == 1)
                    require(app.state.base_policies[0].id == POLICY_ID)
                    require(app.state.loader._fs.state()["loaded"] == 0)
                    require(not app.state.http_clients and not app.state.aws_clients
                            and not app.state.streamable_http_clients)
                    require(app.state.cost_backend is None
                            and app.state.blast_radius_backend is None
                            and app.state.intelligence_client is None)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url="http://offline.invalid") as client:
                        health = await client.get("/healthz")
                        ready = await client.get("/readyz")
                    require(health.status_code == 200 and ready.status_code == 200)
                    state = health.json()["policy_state"]
                    require(state["loaded"] == 1 and state["cloud_loaded"] == 1)
                    require(state["activation"] == {
                        "tenant_id": TENANT, "snapshot_digest": digest,
                        "policy_hashes": {POLICY_ID: POLICY_SHA256},
                        "verified": True, "mode": "frozen_startup"})
                # Lifespan teardown drains canonical queue before closing SQLite.
                with sqlite3.connect(ram / "audit.db") as conn:
                    rows = conn.execute("SELECT payload_json FROM audit_events ORDER BY seq").fetchall()
                events = [json.loads(row[0]) for row in rows]
                require(len(events) == 1 and events[0]["eventType"] == "startup")
                require(events[0]["tenantId"] == TENANT and events[0]["prevEventHash"] == "")
                require(HashChain.verify_event_hash(events[0]))
                require(events[0]["payload"]["mode"] == "enforcement")

            asyncio.run(asyncio.wait_for(activation(), timeout=12))
            checks.extend(["CLOUD_ONLY_HEALTH_EXACT_ACTIVATION", "CLOUD_ONLY_READYZ_200",
                           "NO_UPSTREAM_OR_OPTIONAL_CLIENTS", "CANONICAL_STARTUP_CHAIN_DRAINED"])
            # Cloud policy wins the collision; the count is the effective union.
            (local / "collision.yaml").write_text(body.decode().replace("action: require_approval", "action: allow"))
            (local / "local.yaml").write_text("id: offline-local\nname: Offline local fixture\nmatch: {}\naction: block\n")
            loader = cloud_loader.CloudBackedPolicyLoader(local, cache_path=cache, oauth_tokens={})
            try:
                merged = loader.load_all()
                require(len(merged) == loader.state()["loaded"] == 2)
                require(next(p for p in merged if p.id == POLICY_ID).action.value == "require_approval")
                observed = []
                loader.watch("default", lambda policies: observed.append((len(policies), loader.state()["loaded"])))
                (local / "another.yaml").write_text("id: offline-another\nname: Offline additional fixture\nmatch: {}\naction: block\n")
                loader._fire("default")
                require(observed == [(3, 3)])
            finally:
                loader.stop()
            checks.extend(["MERGED_COLLISION_COUNT", "WATCH_FIRE_COUNT_UPDATED"])

            for label, key, value in (
                ("MISSING_EXPECTED_POLICY_BLOCKED", "TESSERA_EXPECTED_POLICY_ID", "offline-missing"),
                ("WRONG_POLICY_HASH_BLOCKED", "TESSERA_EXPECTED_POLICY_SHA256", "0" * 64),
                ("STALE_SNAPSHOT_BLOCKED", None, None),
            ):
                if key:
                    previous = os.environ[key]
                    os.environ[key] = value
                else:
                    with sqlite3.connect(cache) as conn:
                        metadata = json.loads(conn.execute("SELECT metadata_json FROM cloud_snapshot").fetchone()[0])
                        metadata["fetched_at"] = time.time() - 301
                        conn.execute("UPDATE cloud_snapshot SET metadata_json=?", (json.dumps(metadata),))
                failed_loader = cloud_loader.CloudBackedPolicyLoader(local, cache_path=cache, oauth_tokens={})
                try:
                    try:
                        failed_loader.load_all()
                    except PolicyError:
                        require(failed_loader.state()["loaded"] == 0)
                        require(not failed_loader.state().get("activation", {}).get("verified"))
                    else:
                        raise OfflineCheckError
                finally:
                    failed_loader.stop()
                    if key:
                        os.environ[key] = previous
                checks.append(label)
        require(not ram.exists())
        require(forbidden_attempts == 0)
        checks.extend(["PRIVATE_RAM_REMOVED", "NO_NETWORK_OAUTH_PROVIDER_OR_UPLOAD_ATTEMPTS"])
    return {"schema": "tessera.offline_readiness_activation.v1", "status": "PASS",
            "checks": checks, "check_count": len(checks), "forbidden_attempts": forbidden_attempts}


def main() -> int:
    # Include import/startup/teardown in the bound; never echo exceptions/logs.
    signal.signal(signal.SIGALRM, lambda *_args: (_ for _ in ()).throw(OfflineCheckError()))
    signal.alarm(25)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run()
    except BaseException:
        result = {"schema": "tessera.offline_readiness_activation.v1", "status": "FAIL",
                  "reason_code": "OFFLINE_READINESS_CHECK_FAILED"}
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
