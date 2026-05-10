"""Unit tests for tessera.policy.matchers."""
from __future__ import annotations

import pytest

from tessera.policy.matchers import match_upstream, match_tool


# ── match_upstream ────────────────────────────────────────────────────────────


def test_upstream_wildcard_matches_any() -> None:
    assert match_upstream("*", "aws") is True
    assert match_upstream("*", "github") is True
    assert match_upstream("*", "") is True


def test_upstream_exact_match() -> None:
    assert match_upstream("aws", "aws") is True


def test_upstream_no_match() -> None:
    assert match_upstream("aws", "github") is False
    assert match_upstream("aws", "aws-dev") is False
    assert match_upstream("aws", "") is False


# ── match_tool ────────────────────────────────────────────────────────────────


def test_tool_wildcard_matches_any() -> None:
    assert match_tool("*", None, "aws_s3_list_buckets") is True
    assert match_tool("*", None, "any_tool_name") is True
    assert match_tool("*", None, "") is True


def test_tool_glob_prefix() -> None:
    assert match_tool("aws_s3_*", None, "aws_s3_list_buckets") is True
    assert match_tool("aws_s3_*", None, "aws_s3_delete_object") is True


def test_tool_glob_no_match() -> None:
    assert match_tool("aws_s3_*", None, "gcp_storage_list_buckets") is False
    assert match_tool("aws_s3_*", None, "aws_ec2_list_instances") is False


def test_tool_none_matches_any() -> None:
    """Both tool and tool_pattern None → no restriction → True."""
    assert match_tool(None, None, "any_tool") is True
    assert match_tool(None, None, "") is True


def test_tool_pattern_regex_matches() -> None:
    assert match_tool(None, r"^aws_s3_", "aws_s3_list_buckets") is True
    assert match_tool(None, r"delete|destroy", "aws_s3_delete_object") is True


def test_tool_pattern_no_match() -> None:
    assert match_tool(None, r"^aws_s3_", "gcp_storage_list_buckets") is False
    assert match_tool(None, r"^write_", "read_data") is False
