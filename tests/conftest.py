"""Shared pytest fixtures for Tessera integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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

# ── Test policy directory ─────────────────────────────────────────────────────

# Test policies that proxy round-trip tests use.
# These are written into a temp dir by the policy_dir fixture.

_ALLOW_READS_YAML = """\
id: test-allow-reads
name: Allow read tools
match:
  upstream: "*"
  tool: "aws_s3_list_buckets"
action: allow
priority: 10
"""

_BLOCK_DELETES_YAML = """\
id: test-block-deletes
name: Block delete tools
match:
  upstream: "*"
  tool: "aws_s3_delete_bucket"
action: block
reason: "destructive action blocked"
priority: 20
"""

_REQUIRE_APPROVAL_YAML = """\
id: test-require-approval
name: Require approval for deploy
match:
  upstream: "*"
  tool: "deploy_to_production"
action: require_approval
reason: "production deploy requires approval"
priority: 5
"""


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    """Write test policies into a temp directory and return its path."""
    (tmp_path / "allow-reads.yaml").write_text(_ALLOW_READS_YAML, encoding="utf-8")
    (tmp_path / "block-deletes.yaml").write_text(_BLOCK_DELETES_YAML, encoding="utf-8")
    (tmp_path / "require-approval.yaml").write_text(_REQUIRE_APPROVAL_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def audit_db(tmp_path: Path) -> Path:
    """Return a path for a temporary SQLite audit database."""
    return tmp_path / "audit.db"


@pytest.fixture()
def test_config(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    """Minimal TesseraConfig for integration tests.

    Uses:
    - enforcement mode
    - a temp policy dir (allow-reads + block-deletes + require-approval)
    - a temp SQLite audit db
    - no real upstreams (configured separately per test)
    - no auth tokens (dev mode / anonymous)
    """
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )


@pytest.fixture()
def test_config_log_only(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    """TesseraConfig in log_only mode."""
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.log_only,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )


@pytest.fixture()
def test_config_observation(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    """TesseraConfig in observation mode."""
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.observation,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )


@pytest.fixture()
def test_config_lockdown(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    """TesseraConfig with lockdown=True."""
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=True),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )


@pytest.fixture()
def test_config_intent_required(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    """TesseraConfig with intent.required=True."""
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=True),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )


@pytest.fixture()
def test_config_metrics(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    """TesseraConfig with metrics enabled."""
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=True, bearer_token_env="TESSERA_METRICS_TOKEN"),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )
