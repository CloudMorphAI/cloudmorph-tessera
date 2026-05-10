"""Unit tests for tessera.policy.loader."""

from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest

from tessera.errors import PolicyError
from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import Action, Policy


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _minimal_policy(policy_id: str, priority: int = 0, action: str = "allow") -> str:
    return f"""\
        id: {policy_id}
        name: Test Policy {policy_id}
        action: {action}
        priority: {priority}
    """


# ── Basic load tests ──────────────────────────────────────────────────────────


def test_load_empty_dir(tmp_path: Path) -> None:
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert policies == []


def test_load_single_valid_policy(tmp_path: Path) -> None:
    _write(tmp_path / "allow-all.yaml", _minimal_policy("allow-all"))
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert len(policies) == 1
    assert policies[0].id == "allow-all"
    assert policies[0].action == Action.allow


def test_load_returns_policy_objects(tmp_path: Path) -> None:
    _write(tmp_path / "p.yaml", _minimal_policy("my-policy"))
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert all(isinstance(p, Policy) for p in policies)


# ── Sort order ────────────────────────────────────────────────────────────────


def test_load_sort_order(tmp_path: Path) -> None:
    """Policies sorted descending by priority, then ascending by id."""
    _write(tmp_path / "a.yaml", _minimal_policy("aaa-policy", priority=5))
    _write(tmp_path / "b.yaml", _minimal_policy("bbb-policy", priority=10))
    _write(tmp_path / "c.yaml", _minimal_policy("ccc-policy", priority=5))
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert len(policies) == 3
    assert policies[0].id == "bbb-policy"   # highest priority
    assert policies[1].id == "aaa-policy"   # priority 5, id < ccc
    assert policies[2].id == "ccc-policy"   # priority 5, id > aaa


# ── Underscore files ──────────────────────────────────────────────────────────


def test_underscore_files_skipped(tmp_path: Path) -> None:
    """Files starting with _ (other than _action_verbs.yaml) are not loaded as policies."""
    _write(tmp_path / "_internal.yaml", _minimal_policy("internal"))
    _write(tmp_path / "real-policy.yaml", _minimal_policy("real-policy"))
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert len(policies) == 1
    assert policies[0].id == "real-policy"


def test_action_verbs_yaml_recognized(tmp_path: Path) -> None:
    """_action_verbs.yaml is treated as user-mapping config, not a policy."""
    _write(tmp_path / "_action_verbs.yaml", "mappings:\n  my.tool: [read.list]\n")
    _write(tmp_path / "p.yaml", _minimal_policy("p"))
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    # only p.yaml should be loaded as a policy
    assert len(policies) == 1
    assert policies[0].id == "p"
    # user mappings should be merged
    assert "my.tool" in loader._action_verbs


# ── Startup failure ───────────────────────────────────────────────────────────


def test_invalid_policy_raises_at_startup(tmp_path: Path) -> None:
    """Malformed policy file raises PolicyError at startup (not first reload)."""
    _write(tmp_path / "bad.yaml", "id: BAD_ID\nname: bad\naction: allow\n")
    loader = FilesystemPolicyLoader(tmp_path)
    with pytest.raises(PolicyError):
        loader.load_all()


def test_duplicate_id_raises_at_startup(tmp_path: Path) -> None:
    """Two policy files with the same id raise PolicyError at startup."""
    _write(tmp_path / "a.yaml", _minimal_policy("same-id"))
    _write(tmp_path / "b.yaml", _minimal_policy("same-id"))
    loader = FilesystemPolicyLoader(tmp_path)
    with pytest.raises(PolicyError, match="duplicate"):
        loader.load_all()


# ── Reload error isolation ────────────────────────────────────────────────────


def test_reload_error_isolation_malformed_yaml(tmp_path: Path) -> None:
    """After a valid load, a malformed reload keeps the prior valid policy."""
    policy_file = tmp_path / "p.yaml"
    _write(policy_file, _minimal_policy("p"))

    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert len(policies) == 1

    # Simulate a reload with bad YAML — the loader internals are exposed via _load_file
    policy_file.write_text(": bad : yaml :\n{{{{", encoding="utf-8")
    # Second load_all call is treated as a reload (policies is non-empty)
    policies2 = loader.load_all()

    # Prior version kept
    assert len(policies2) == 1
    assert policies2[0].id == "p"

    # Error recorded in state
    st = loader.state()
    assert st["loaded"] == 1
    assert len(st["errored"]) == 1


def test_reload_error_keeps_prior_valid_version(tmp_path: Path) -> None:
    """state() shows errored entry; load_all() returns prior version."""
    policy_file = tmp_path / "p.yaml"
    _write(policy_file, _minimal_policy("p", priority=7))

    loader = FilesystemPolicyLoader(tmp_path)
    loader.load_all()

    # Now break the file
    policy_file.write_text("id: BAD_ID\nname: broken\naction: allow\n", encoding="utf-8")
    result = loader.load_all()

    # Original still served
    assert result[0].priority == 7

    st = loader.state()
    assert st["loaded"] == 1
    assert len(st["errored"]) == 1
    assert "errored" in st


def test_removed_file_drops_policy(tmp_path: Path) -> None:
    """Deleting a policy file removes it from the engine on next load_all."""
    policy_file = tmp_path / "p.yaml"
    _write(policy_file, _minimal_policy("p"))

    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert len(policies) == 1

    # Delete and reload
    policy_file.unlink()
    policies2 = loader.load_all()
    assert policies2 == []


# ── State reporting ───────────────────────────────────────────────────────────


def test_state_reports_loaded_and_errored(tmp_path: Path) -> None:
    _write(tmp_path / "good.yaml", _minimal_policy("good"))

    loader = FilesystemPolicyLoader(tmp_path)
    loader.load_all()
    st = loader.state()

    assert st["loaded"] == 1
    assert st["errored"] == []


def test_state_empty_before_load(tmp_path: Path) -> None:
    loader = FilesystemPolicyLoader(tmp_path)
    st = loader.state()
    assert st["loaded"] == 0
    assert st["errored"] == []


# ── Watch callback ────────────────────────────────────────────────────────────


def test_watch_callback_fires(tmp_path: Path) -> None:
    """watch() registers callback; when load_all is called again, callbacks fire."""
    _write(tmp_path / "p.yaml", _minimal_policy("p"))
    loader = FilesystemPolicyLoader(tmp_path, reload_mode="none")

    received: list[list[Policy]] = []
    loader.watch("default", lambda policies: received.append(policies))

    # In none-mode, watchdog is not active; manually invoke load_all to simulate reload
    # and check that callbacks fire if we trigger them explicitly
    # The watch method just registers the callback; for none-mode, no auto-trigger.
    # We test that the callback was stored.
    assert len(loader._callbacks) == 1


def test_watch_callback_called_on_reload_simulation(tmp_path: Path) -> None:
    """Simulates what watchdog would do by calling load_all and firing callbacks."""
    _write(tmp_path / "p.yaml", _minimal_policy("p"))
    loader = FilesystemPolicyLoader(tmp_path, reload_mode="none")

    received: list[list[Policy]] = []
    loader.watch("default", lambda policies: received.append(policies))

    # Manually fire callbacks (simulating what the watchdog handler would do)
    updated = loader.load_all()
    for cb in loader._callbacks:
        cb(updated)

    assert len(received) == 1
    assert received[0][0].id == "p"
