"""`tessera demo` — a zero-config, fully offline demo of the policy firewall.

Spins up an in-process Tessera proxy (no real TCP listener, no cloud, no
network, no login) against one small bundled demo policy, fires one
dangerous call that gets blocked and one safe call that's allowed, then
exports a signed, offline-verifiable evidence bundle from the resulting
audit trail.

Designed to run start-to-finish in well under 60 seconds on a machine with
nothing configured: ``pip install cloudmorph-tessera && tessera demo``.

This module never touches cloud endpoints, never requires authentication,
and never reads or generates production key material — the only signing
key involved is a throwaway Ed25519 instance key generated fresh inside a
temp directory for this one run (see ``tessera.audit.signing`` for the
distinction between that per-machine instance key and Tessera's actual
production/org signing material, which this module does not touch).
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

__all__ = ["run_demo", "DEMO_POLICY_ID", "DEMO_BLOCK_REASON"]

DEMO_POLICY_ID = "demo-block-rds-delete"
DEMO_BLOCK_REASON = "Deleting an RDS instance without a final snapshot is blocked by policy"

_DEMO_POLICY_YAML = f"""\
id: {DEMO_POLICY_ID}
name: Block RDS delete without a final snapshot
description: |
  Demo policy bundled with `tessera demo`. Blocks aws_rds_DeleteDBInstance
  calls where SkipFinalSnapshot is "true" — deleting a database without
  keeping a final snapshot is an unrecoverable, one-way action, exactly the
  kind of call a deterministic policy firewall at the MCP boundary exists
  to catch before it reaches AWS.
match:
  upstream: "*"
  tool: aws_rds_DeleteDBInstance
when:
  - condition: arg_equals
    arg: SkipFinalSnapshot
    value: "true"
action: block
reason: "{DEMO_BLOCK_REASON}"
priority: 50
"""

# Env vars that, if already set in the operator's real shell (e.g. because
# they normally run `tessera serve --cloud` or have live pricing/blast-radius
# backends configured), would make this in-process demo reach outside the
# machine or require credentials. `tessera demo` promises zero-config,
# fully-offline behavior, so these are neutralised for the duration of the
# run and restored exactly afterward — nothing about the operator's real
# environment or config is permanently touched.
_NEUTRALIZED_ENV_VARS = (
    "TESSERA_AUTHENTICATOR",
    "TESSERA_AUDIT_SINK",
    "TESSERA_POLICY_LOADER",
    "TESSERA_BEARER_TOKENS",
    "TESSERA_BEARER_TOKENS_FILE",
    "TESSERA_BEARER_TOKEN",
    "TESSERA_INFRACOST_URL",
    "TESSERA_BLAST_RADIUS_ENABLED",
    "TESSERA_OTEL_ENABLED",
)


@contextlib.contextmanager
def _neutralized_environment(state_dir: Path):
    """Temporarily clear env vars that could pull the demo out of process / offline mode.

    Also redirects TESSERA_STATE_DIR into the demo's own temp dir so the run
    never touches the operator's real ~/.tessera/state daily-spend database.
    Everything is restored in `finally`, including on exception.
    """
    import os

    tracked = (*_NEUTRALIZED_ENV_VARS, "TESSERA_STATE_DIR")
    saved: dict[str, str | None] = {name: os.environ.get(name) for name in tracked}
    try:
        for name in _NEUTRALIZED_ENV_VARS:
            os.environ.pop(name, None)
        os.environ["TESSERA_STATE_DIR"] = str(state_dir)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextlib.contextmanager
def _quiet_tessera_logging():
    """Temporarily silence tessera's own INFO/WARNING logging during the demo.

    `create_app` logs plenty (startup, dev-mode-auth-disabled warnings,
    revocation-store notices, ...) which is exactly what you want from a
    real `tessera serve`, but is just noise in a scripted sales demo whose
    transcript is meant to be read top-to-bottom. Restores the previous
    level afterward, including on exception. Does not touch handlers, so it
    never affects logging behavior outside this one call.
    """
    import logging

    logger = logging.getLogger("tessera")
    previous_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(previous_level)


def _mock_upstream_transport() -> Any:
    """A canned httpx.MockTransport standing in for a real AWS MCP upstream."""
    import httpx

    _RESPONSE = (
        b'{"jsonrpc": "2.0", "id": 1, "result": '
        b'{"content": [{"type": "text", "text": '
        b'"2 DB instance(s) found: prod-orders-db, staging-orders-db"}]}}'
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=_RESPONSE,
        )

    return httpx.MockTransport(_handler)


def run_demo(echo: Callable[..., None] = print) -> dict[str, Any]:
    """Run the end-to-end demo, printing each step via `echo` (defaults to `print`).

    Returns a small result dict — mainly useful for tests — containing the
    block/allow response bodies, the exported manifest, and the evidence
    bundle directory.
    """
    from fastapi.testclient import TestClient

    from tessera.audit.export import export_evidence_bundle
    from tessera.audit.sinks.sqlite import SqliteSink
    from tessera.audit.verifier import verify_bundle_dir
    from tessera.config import (
        AuditConfig,
        IntentConfig,
        MetricsConfig,
        PoliciesConfig,
        PoliciesMode,
        RuntimeConfig,
        TesseraConfig,
        UpstreamConfig,
    )
    from tessera.proxy import create_app

    def _p(msg: str = "") -> None:
        echo(msg)

    t0 = time.monotonic()

    _p("=" * 72)
    _p("Tessera — a deterministic, signed policy firewall at the MCP boundary")
    _p("Zero-config demo: no cloud, no network, no login required.")
    _p("=" * 72)
    _p()

    # A persistent directory for the evidence bundle so the verify command
    # printed at the end still works after this process exits. The scratch
    # bits (policy file, audit db, throwaway signing key) live in a
    # TemporaryDirectory that's cleaned up automatically.
    bundle_dir = Path(tempfile.mkdtemp(prefix="tessera-demo-bundle-"))

    with tempfile.TemporaryDirectory(prefix="tessera-demo-scratch-") as scratch:
        scratch_dir = Path(scratch)
        policy_dir = scratch_dir / "policies"
        policy_dir.mkdir()
        (policy_dir / "demo-block-rds-delete.yaml").write_text(_DEMO_POLICY_YAML, encoding="utf-8")

        audit_db = scratch_dir / "audit.db"
        signing_key_path = scratch_dir / "signing-key.pem"
        state_dir = scratch_dir / "state"

        config = TesseraConfig(
            audit=AuditConfig(path=str(audit_db), also_stdout=False),
            policies=PoliciesConfig(
                dir=str(policy_dir),
                reload="none",
                mode=PoliciesMode.enforcement,
                default_action="allow",
            ),
            intent=IntentConfig(meta_key="tessera_intent", required=False),
            metrics=MetricsConfig(enabled=False),
            runtime=RuntimeConfig(lockdown=False),
            upstreams=[UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5)],
            deployment_id="demo",
        )

        _p("[1/4] Starting an in-process Tessera proxy with one bundled demo policy")
        _p(f"      policy: {DEMO_POLICY_ID}")
        _p('      block aws_rds_DeleteDBInstance when SkipFinalSnapshot == "true"')
        _p()

        with _neutralized_environment(state_dir), _quiet_tessera_logging():
            import httpx

            app = create_app(config)
            with TestClient(app, raise_server_exceptions=False) as client:
                app.state.http_clients["mock"] = httpx.AsyncClient(
                    transport=_mock_upstream_transport(),
                    base_url="http://mock-upstream",
                    timeout=5.0,
                )

                _p('[2/4] Dangerous call: aws_rds_DeleteDBInstance(SkipFinalSnapshot="true")')
                block_resp = client.post(
                    "/mcp/mock",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "aws_rds_DeleteDBInstance",
                            "arguments": {
                                "DBInstanceIdentifier": "prod-orders-db",
                                "SkipFinalSnapshot": "true",
                            },
                        },
                    },
                )
                block_body = block_resp.json()
                block_text = block_body["result"]["content"][0]["text"]
                _p()
                _p(block_text)
                _p()

                _p("[3/4] Safe call: aws_rds_DescribeDBInstances()")
                allow_resp = client.post(
                    "/mcp/mock",
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "aws_rds_DescribeDBInstances", "arguments": {}},
                    },
                )
                allow_body = allow_resp.json()
                allow_result = allow_body.get("result", {})
                allow_text = ""
                if allow_result.get("content"):
                    allow_text = allow_result["content"][0].get("text", "")
                _p(f"      ALLOWED — upstream responded: {allow_text!r}")
                _p()

            # TestClient's context exit runs the app lifespan shutdown, which
            # drains the async audit queue and closes the sink cleanly before
            # we open a second, independent SqliteSink for export below.

            _p("[4/4] Exporting a signed, offline-verifiable evidence bundle from the audit trail")
            sink = SqliteSink(path=str(audit_db))
            try:
                manifest = export_evidence_bundle(
                    sink,
                    scope=config.deployment_id,
                    from_ts=None,
                    to_ts=None,
                    out_dir=str(bundle_dir),
                    signing_key_path=str(signing_key_path),
                )
            finally:
                sink.close()

        # Back outside _neutralized_environment — the operator's real env is restored.

        verify_result = verify_bundle_dir(str(bundle_dir))

        counts = manifest["event_counts"]
        _p(f"      bundle written to: {bundle_dir}")
        _p(
            f"      events: {counts['total']} total "
            f"(allow={counts['allow']} block={counts['block']})"
        )
        _p("      contains: events.jsonl, manifest.json, summary.html, VERIFY.md")
        _p()
        _p("      Verify this bundle offline, any time, with:")
        _p(f"        tessera audit verify --dir {bundle_dir}")
        _p(
            f"      (self-check just now: ok={verify_result['ok']} "
            f"signature_ok={verify_result.get('signature_ok')})"
        )
        _p()

        elapsed = time.monotonic() - t0
        _p(f"Done in {elapsed:.2f}s — fully offline, no cloud account, no login.")

        return {
            "block_response": block_body,
            "block_text": block_text,
            "allow_response": allow_body,
            "manifest": manifest,
            "bundle_dir": str(bundle_dir),
            "verify_result": verify_result,
        }
