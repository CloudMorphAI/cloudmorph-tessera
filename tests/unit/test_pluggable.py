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
