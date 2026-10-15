"""Tests for `tessera demo` — the offline, zero-config sales artifact.

Exercises the demo end-to-end (no cloud, no network, no login) and asserts:
  - the dangerous call is blocked with the demo policy's payload
  - the safe call is allowed
  - a signed, offline-verifiable evidence bundle is produced
  - env vars that could pull the demo out of offline mode are neutralised
    for the run and restored afterward
"""

from __future__ import annotations

import os
from pathlib import Path

from tessera.audit.verifier import verify_bundle_dir
from tessera.demo import DEMO_BLOCK_REASON, DEMO_POLICY_ID, run_demo


def test_demo_blocks_dangerous_call_and_allows_safe_call() -> None:
    lines: list[str] = []
    result = run_demo(echo=lines.append)

    # Three blocks — secret, GitHub force-push, Postgres DROP.
    from tessera.demo import DEMO_BLOCKS

    blocks = result["blocks"]
    assert len(blocks) == 3
    for spec, block in zip(DEMO_BLOCKS, blocks, strict=True):
        assert "POLICY_BLOCK" in block["text"]
        assert f"policy_id: {spec['policy_id']}" in block["text"]
        assert spec["reason"] in block["text"]

    block_text = result["block_text"]
    assert f"policy_id: {DEMO_POLICY_ID}" in block_text
    assert DEMO_BLOCK_REASON in block_text

    # Safe call allowed (not result.isError).
    allow_body = result["allow_response"]
    allow_result = allow_body.get("result", {})
    assert allow_result.get("isError") is not True
    assert "result" in allow_body

    # Evidence bundle: counts + files.
    manifest = result["manifest"]
    counts = manifest["event_counts"]
    assert counts["total"] >= 4
    assert counts["block"] >= 3
    assert counts["allow"] >= 1

    bundle_dir = Path(result["bundle_dir"])
    assert (bundle_dir / "events.jsonl").exists()
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.html").exists()
    assert (bundle_dir / "VERIFY.md").exists()

    # Fully offline re-verification of the bundle just produced.
    verify_result = verify_bundle_dir(str(bundle_dir))
    assert verify_result["ok"] is True
    assert verify_result["signature_ok"] is True
    assert verify_result["events_checked"] == counts["total"]

    # Printed transcript sanity.
    output = "\n".join(lines)
    assert "POLICY_BLOCK" in output
    assert "ALLOWED" in output
    assert "tessera audit verify --dir" in output
    assert str(bundle_dir) in output


def test_demo_result_matches_verify_result() -> None:
    """The verify_result returned by run_demo matches a fresh, independent verify."""
    result = run_demo(echo=lambda *_: None)
    bundle_dir = result["bundle_dir"]
    independent = verify_bundle_dir(bundle_dir)
    assert result["verify_result"]["ok"] == independent["ok"] is True


def test_demo_neutralizes_env_vars_and_restores_them() -> None:
    """A real TESSERA_BEARER_TOKEN (or similar) in the caller's shell must not
    break the demo, and must be restored exactly afterward."""
    sentinel_token = "tk_should_not_be_required_xxxxxxxx"  # noqa: S105
    sentinel_loader = "some.custom:Loader"

    original_token = os.environ.get("TESSERA_BEARER_TOKEN")
    original_loader = os.environ.get("TESSERA_POLICY_LOADER")
    os.environ["TESSERA_BEARER_TOKEN"] = sentinel_token
    os.environ["TESSERA_POLICY_LOADER"] = sentinel_loader
    try:
        result = run_demo(echo=lambda *_: None)
        assert "POLICY_BLOCK" in result["block_text"]
        # Restored to the exact sentinel values set before the call.
        assert os.environ.get("TESSERA_BEARER_TOKEN") == sentinel_token
        assert os.environ.get("TESSERA_POLICY_LOADER") == sentinel_loader
    finally:
        if original_token is None:
            os.environ.pop("TESSERA_BEARER_TOKEN", None)
        else:
            os.environ["TESSERA_BEARER_TOKEN"] = original_token
        if original_loader is None:
            os.environ.pop("TESSERA_POLICY_LOADER", None)
        else:
            os.environ["TESSERA_POLICY_LOADER"] = original_loader


def test_demo_completes_quickly() -> None:
    """Sanity budget: the whole offline demo should be nowhere near 60s."""
    import time

    t0 = time.monotonic()
    run_demo(echo=lambda *_: None)
    elapsed = time.monotonic() - t0
    assert elapsed < 30.0, f"demo took {elapsed:.2f}s — expected well under 60s offline"
