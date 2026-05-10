"""Test ported _keep decision fixtures against the policy engine directly."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tessera.policy.engine import PolicyEngine
from tessera.policy.schema import Action, MatchSpec, Policy

DECISIONS_DIR = Path(__file__).parent.parent / "fixtures" / "decisions"


# ── Test policies matching the fixture scenarios ──────────────────────────────
# Fixtures 01, 04, 05 expect "allow" — we create an allow-read policy.
# Fixtures 02, 03 expect "block" — default block + specific block policy.
# Fixture 06 expects "block" due to lockdown=true.

def _build_fixture_engine() -> PolicyEngine:
    """Build a PolicyEngine that supports the decision fixtures."""
    allow_read = Policy(
        id="allow-read",
        name="Allow S3 read",
        match=MatchSpec(upstream="*", tool="aws_s3_list_buckets"),
        when=[],
        action=Action.allow,
        reason="allow_read_first",
        priority=10,
    )
    block_destructive = Policy(
        id="block-destructive",
        name="Block S3 delete",
        match=MatchSpec(upstream="*", tool="aws_s3_delete_bucket"),
        when=[],
        action=Action.block,
        reason="destructive_action_blocked",
        priority=20,
    )
    # Sort by descending priority, ascending id
    policies = sorted([allow_read, block_destructive], key=lambda p: (-p.priority, p.id))
    return PolicyEngine(policies, default_action=Action.block)


def _load_fixtures() -> list[tuple[str, dict]]:
    return [
        (f.stem, json.loads(f.read_text(encoding="utf-8")))
        for f in sorted(DECISIONS_DIR.glob("*.json"))
    ]


@pytest.mark.parametrize("name,fixture", _load_fixtures())
def test_decision_fixture(name: str, fixture: dict) -> None:
    """Run each ported decision fixture against the engine directly."""
    engine = _build_fixture_engine()
    inp = fixture["input"]

    context = {
        "tool_call": inp["tool_call"],
        "runtime": inp["runtime"],
        "intent": inp.get("intent"),
        "upstream": "aws",
        "mode": "enforcement",
        "policy_id": None,
    }

    decision = engine.evaluate(context)
    expected_outcome = fixture["expected"]["outcome"]

    assert decision.action.value == expected_outcome, (
        f"Fixture {name!r}: expected outcome={expected_outcome!r}, "
        f"got action={decision.action.value!r} reason={decision.reason!r}"
    )
