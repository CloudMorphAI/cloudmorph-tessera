"""Unit tests for CloudBackedPolicyLoader — the cache→engine bridge.

Proves a policy authored in the web dashboard and synced to
``~/.tessera/policy-cache.db`` is merged into the enforcement policy set, while
disabled / body-less / malformed cloud rows are handled safely and local YAML
still hard-fails the boot on error.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tessera.errors import PolicyError
from tessera.policy.cloud_loader import CloudBackedPolicyLoader

# ── Fixtures / helpers ────────────────────────────────────────────────────────

LOCAL_POLICY = """
id: local-readonly
name: Local read-only guard
description: Local YAML policy.
match:
  upstream: "*"
  tool: "drop_table"
when:
  - condition: arg_equals
    arg: confirm
    value: "yes"
action: block
reason: "drop_table blocked by local policy"
priority: 50
""".strip()

CLOUD_POLICY = """
id: cloud-block-delete-db
name: Block delete_database (dashboard-authored)
description: Authored in the web dashboard, synced down.
match:
  upstream: "*"
  tool: "aws_rds_DeleteDBInstance"
when:
  - condition: arg_equals
    arg: SkipFinalSnapshot
    value: "true"
action: block
reason: "RDS deletion blocked by your dashboard policy"
priority: 80
""".strip()

# Same id as LOCAL_POLICY but different action/priority — used for override test.
CLOUD_OVERRIDE = """
id: local-readonly
name: Dashboard override of local-readonly
description: Cloud version wins on id collision.
match:
  upstream: "*"
  tool: "drop_table"
when:
  - condition: arg_equals
    arg: confirm
    value: "yes"
action: require_approval
reason: "drop_table now needs approval (dashboard)"
priority: 95
""".strip()


def _seed_cache(db_path: Path, rows: list[dict]) -> None:
    """Create a policy-cache.db matching cloud_sync's schema and insert *rows*."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS policies (
                policy_id TEXT PRIMARY KEY, pack_id TEXT, enabled INTEGER,
                priority INTEGER, action_summary TEXT, match_summary TEXT,
                yaml_text TEXT, fetched_at_unix INTEGER NOT NULL)"""
        )
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO policies
                   (policy_id, pack_id, enabled, priority, action_summary,
                    match_summary, yaml_text, fetched_at_unix)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r["policy_id"], r.get("pack_id", ""), 1 if r.get("enabled", True) else 0,
                    r.get("priority", 0), r.get("action_summary", ""),
                    r.get("match_summary", ""), r.get("yaml_text", ""), int(time.time()),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _write_local(policy_dir: Path, name: str, body: str) -> None:
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / name).write_text(body, encoding="utf-8")


def _ids(policies) -> set[str]:
    return {p.id for p in policies}


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_load_all_merges_local_and_cloud(tmp_path: Path) -> None:
    pdir = tmp_path / "policies"
    _write_local(pdir, "local.yaml", LOCAL_POLICY)
    db = tmp_path / "policy-cache.db"
    _seed_cache(db, [{"policy_id": "cloud-block-delete-db", "enabled": True, "yaml_text": CLOUD_POLICY}])

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    policies = loader.load_all()

    assert _ids(policies) == {"local-readonly", "cloud-block-delete-db"}
    # Sorted by desc priority: cloud (80) before local (50).
    assert [p.id for p in policies] == ["cloud-block-delete-db", "local-readonly"]
    assert loader.state()["cloud_loaded"] == 1


def test_disabled_cloud_policy_excluded(tmp_path: Path) -> None:
    pdir = tmp_path / "policies"
    _write_local(pdir, "local.yaml", LOCAL_POLICY)
    db = tmp_path / "policy-cache.db"
    _seed_cache(db, [{"policy_id": "cloud-block-delete-db", "enabled": False, "yaml_text": CLOUD_POLICY}])

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    policies = loader.load_all()

    assert _ids(policies) == {"local-readonly"}
    assert loader.state()["cloud_loaded"] == 0


def test_empty_yaml_text_skipped_and_surfaced(tmp_path: Path) -> None:
    """The current backend omits yaml_text — a body-less row must be surfaced, not silently enforced-as-nothing."""
    pdir = tmp_path / "policies"
    _write_local(pdir, "local.yaml", LOCAL_POLICY)
    db = tmp_path / "policy-cache.db"
    _seed_cache(db, [{"policy_id": "cloud-metadata-only", "enabled": True, "yaml_text": ""}])

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    policies = loader.load_all()

    assert _ids(policies) == {"local-readonly"}
    errored = loader.state()["cloud_errored"]
    assert any(e["policy_id"] == "cloud-metadata-only" and "empty yaml_text" in e["error"] for e in errored)


def test_bad_cloud_yaml_isolated_local_intact(tmp_path: Path) -> None:
    pdir = tmp_path / "policies"
    _write_local(pdir, "local.yaml", LOCAL_POLICY)
    db = tmp_path / "policy-cache.db"
    _seed_cache(db, [{"policy_id": "cloud-broken", "enabled": True, "yaml_text": "id: cloud-broken\nmatch: NOT_A_MAPPING"}])

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    policies = loader.load_all()  # must NOT raise — remote error is isolated

    assert _ids(policies) == {"local-readonly"}
    assert any(e["policy_id"] == "cloud-broken" for e in loader.state()["cloud_errored"])


def test_cloud_overrides_local_on_id_collision(tmp_path: Path) -> None:
    pdir = tmp_path / "policies"
    _write_local(pdir, "local.yaml", LOCAL_POLICY)  # action=block, priority=50
    db = tmp_path / "policy-cache.db"
    _seed_cache(db, [{"policy_id": "local-readonly", "enabled": True, "yaml_text": CLOUD_OVERRIDE}])

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    policies = loader.load_all()

    assert len(policies) == 1  # merged by id
    merged = policies[0]
    assert merged.id == "local-readonly"
    assert merged.priority == 95           # cloud value wins
    assert merged.action.value == "require_approval"  # cloud action wins


def test_no_cache_file_is_fs_only(tmp_path: Path) -> None:
    pdir = tmp_path / "policies"
    _write_local(pdir, "local.yaml", LOCAL_POLICY)
    db = tmp_path / "does-not-exist.db"

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    policies = loader.load_all()

    assert _ids(policies) == {"local-readonly"}
    assert loader.state()["cloud_loaded"] == 0


def test_local_startup_error_still_hard_fails(tmp_path: Path) -> None:
    """A broken LOCAL policy must still hard-fail the boot (cloud loader must not swallow it)."""
    pdir = tmp_path / "policies"
    _write_local(pdir, "broken.yaml", "id: broken\nmatch: 12345")  # match must be a mapping
    db = tmp_path / "policy-cache.db"
    _seed_cache(db, [{"policy_id": "cloud-block-delete-db", "enabled": True, "yaml_text": CLOUD_POLICY}])

    loader = CloudBackedPolicyLoader(pdir, cache_path=db, oauth_tokens=None)
    with pytest.raises(PolicyError):
        loader.load_all()
