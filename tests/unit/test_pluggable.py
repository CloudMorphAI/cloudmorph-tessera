"""Unit tests for tessera.pluggable."""

from __future__ import annotations

import pytest

from tessera.errors import ConfigError
from tessera.pluggable import resolve


def test_resolve_valid_class() -> None:
    cls = resolve("tessera.audit.sinks.sqlite:SqliteSink", "")
    from tessera.audit.sinks.sqlite import SqliteSink

    assert cls is SqliteSink


def test_resolve_uses_default_when_env_value_empty() -> None:
    cls = resolve("", "tessera.audit.sinks.stdout:StdoutSink")
    from tessera.audit.sinks.stdout import StdoutSink

    assert cls is StdoutSink


def test_resolve_bad_spec_no_colon_raises() -> None:
    with pytest.raises(ConfigError, match="module:Class"):
        resolve("no_colon_here", "")


def test_resolve_bad_module_raises() -> None:
    with pytest.raises(ConfigError, match="cannot import"):
        resolve("nonexistent.module.xyz:Foo", "")


def test_resolve_bad_class_raises() -> None:
    with pytest.raises(ConfigError, match="no attribute"):
        resolve("tessera.errors:NoSuchClass", "")


def test_resolve_fake_policy_loader() -> None:
    """TESSERA_POLICY_LOADER can point to tests.fakes:FakePolicyLoader."""
    cls = resolve("tests.fakes:FakePolicyLoader", "tessera.policy.loader:FilesystemPolicyLoader")
    from tests.fakes import FakePolicyLoader

    assert cls is FakePolicyLoader


def test_fake_policy_loader_returns_empty_list() -> None:
    """FakePolicyLoader.load_all() returns empty list for any scope."""
    from tests.fakes import FakePolicyLoader

    loader = FakePolicyLoader(policy_dir="/tmp/policies", reload_mode="none")
    assert loader.load_all("default") == []
    assert loader.load_all("other-scope") == []


def test_fake_policy_loader_state() -> None:
    from tests.fakes import FakePolicyLoader

    loader = FakePolicyLoader(policy_dir="/tmp/policies")
    state = loader.state()
    assert state["loaded"] == 0
    assert state["errored"] == []
