from __future__ import annotations

import pytest

from tessera.policy import action_verbs as _action_verbs_module
from tessera.policy.action_verbs import (
    ACTION_VERBS,
    KNOWN_VERBS,
    load_user_mappings,
    merge_mappings,
    verbs_for,
)


def test_known_verbs_is_frozen() -> None:
    assert isinstance(KNOWN_VERBS, frozenset)


def test_known_verbs_count() -> None:
    assert len(KNOWN_VERBS) >= 20


def test_verbs_for_known_action_returns_correct_verbs() -> None:
    result = verbs_for("aws.s3.list_buckets")
    assert result == frozenset({"read.list"})


def test_verbs_for_unknown_action_returns_empty() -> None:
    result = verbs_for("completely.unknown.tool.xyz")
    assert result == frozenset()


def test_mcp_proxy_not_in_action_verbs() -> None:
    assert "mcp.proxy" not in ACTION_VERBS


def test_mcp_proxy_prefix_no_longer_special_cased() -> None:
    result = verbs_for("mcp.proxy.foo")
    assert result == frozenset()


def test_load_user_mappings_basic(tmp_path: pytest.TempPathFactory) -> None:
    yaml_file = tmp_path / "mappings.yaml"
    yaml_file.write_text(
        "mappings:\n  my.custom.tool: [read.list, analyze]\n  another.tool: [write.create]\n",
        encoding="utf-8",
    )
    result = load_user_mappings(yaml_file)
    assert result == {
        "my.custom.tool": frozenset({"read.list", "analyze"}),
        "another.tool": frozenset({"write.create"}),
    }


def test_load_user_mappings_rejects_unknown_verbs(tmp_path: pytest.TempPathFactory) -> None:
    yaml_file = tmp_path / "bad_verbs.yaml"
    yaml_file.write_text(
        "mappings:\n  my.tool: [unknown.verb]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unknown intent verb"):
        load_user_mappings(yaml_file)


def test_load_user_mappings_missing_mappings_key_raises(tmp_path: pytest.TempPathFactory) -> None:
    yaml_file = tmp_path / "no_mappings.yaml"
    yaml_file.write_text(
        "tools:\n  my.tool: [read.list]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mappings"):
        load_user_mappings(yaml_file)


def test_merge_mappings_user_overrides_builtin() -> None:
    builtin = {"tool.a": frozenset({"read.list"})}
    user = {"tool.a": frozenset({"write.create"})}
    result = merge_mappings(builtin, user)
    assert result["tool.a"] == frozenset({"write.create"})


def test_merge_mappings_no_mutation() -> None:
    builtin = {"tool.a": frozenset({"read.list"})}
    user = {"tool.a": frozenset({"write.create"}), "tool.b": frozenset({"analyze"})}
    builtin_before = dict(builtin)
    user_before = dict(user)
    merge_mappings(builtin, user)
    assert builtin == builtin_before
    assert user == user_before


def test_merge_mappings_disjoint() -> None:
    builtin = {"tool.a": frozenset({"read.list"})}
    user = {"tool.b": frozenset({"analyze"})}
    result = merge_mappings(builtin, user)
    assert "tool.a" in result
    assert "tool.b" in result
    assert result["tool.a"] == frozenset({"read.list"})
    assert result["tool.b"] == frozenset({"analyze"})


# ---------------------------------------------------------------------------
# A-4-2: _user_mappings dict integration (verbs_for consults user table first)
# ---------------------------------------------------------------------------


def test_user_mappings_override_builtin_via_module_dict(tmp_path: pytest.TempPathFactory) -> None:
    """verbs_for() must return user-overridden verbs when _user_mappings is populated."""
    # Inject directly into module-level dict (mimics lifespan startup)
    _action_verbs_module._user_mappings["custom_override_tool"] = frozenset({"write.create"})
    try:
        result = verbs_for("custom_override_tool")
        assert result == frozenset({"write.create"})
    finally:
        _action_verbs_module._user_mappings.pop("custom_override_tool", None)


def test_user_mappings_override_existing_builtin_key(tmp_path: pytest.TempPathFactory) -> None:
    """User mapping for an existing builtin key overrides the builtin."""
    # aws_s3_list_buckets is normally read.list — override with analyze
    _action_verbs_module._user_mappings["aws_s3_list_buckets"] = frozenset({"analyze"})
    try:
        result = verbs_for("aws_s3_list_buckets")
        assert result == frozenset({"analyze"})
    finally:
        _action_verbs_module._user_mappings.pop("aws_s3_list_buckets", None)


def test_user_mappings_file_roundtrip(tmp_path: pytest.TempPathFactory) -> None:
    """load_user_mappings → _user_mappings → verbs_for full path."""
    yaml_file = tmp_path / "_action_verbs.yaml"
    yaml_file.write_text(
        "mappings:\n"
        "  my_vendor_tool_list: [read.list]\n"
        "  my_vendor_tool_run: [execute.run, audit.log]\n",
        encoding="utf-8",
    )
    user = load_user_mappings(yaml_file)
    _action_verbs_module._user_mappings.update(user)
    try:
        assert verbs_for("my_vendor_tool_list") == frozenset({"read.list"})
        assert verbs_for("my_vendor_tool_run") == frozenset({"execute.run", "audit.log"})
    finally:
        _action_verbs_module._user_mappings.pop("my_vendor_tool_list", None)
        _action_verbs_module._user_mappings.pop("my_vendor_tool_run", None)
