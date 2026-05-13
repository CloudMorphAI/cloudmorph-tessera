"""Unit tests for the data_volume condition."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import DataVolume


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ctx(args: dict | None = None) -> dict:
    return {
        "tool_call": {"name": "aws_s3_GetObject", "arguments": args or {}, "_meta": None},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_static_arg_size_greater_than_blocks():
    """Large args serialised to JSON > threshold → True."""
    large_key = "x" * 2000
    args = {"Bucket": "my-bucket", "Key": large_key}
    serialized_size = len(json.dumps(args).encode("utf-8"))
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=serialized_size - 100,
        estimator="static_arg_size",
    )
    ctx = _ctx(args)
    assert evaluate_condition(cond, ctx) is True


def test_static_arg_size_under_threshold_allows():
    """Small args < threshold → False."""
    args = {"Bucket": "b", "Key": "k"}
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=100_000,
        estimator="static_arg_size",
    )
    ctx = _ctx(args)
    assert evaluate_condition(cond, ctx) is False


def test_s3_byte_estimate_uses_head_object():
    """s3_get_byte_estimate reads ContentLength from s3.head_object."""
    args = {"Bucket": "my-bucket", "Key": "big-file.tar.gz"}

    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {"ContentLength": 500_000_000}

    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=100_000_000,
        estimator="s3_get_byte_estimate",
    )

    with patch("tessera.policy.conditions.boto3") as mock_boto:
        mock_boto.client.return_value = mock_s3
        result = evaluate_condition(cond, _ctx(args))

    assert result is True  # 500MB > 100MB


def test_s3_byte_estimate_fallback_on_error():
    """s3_get_byte_estimate falls back to static size when boto3 raises."""
    args = {"Bucket": "my-bucket", "Key": "some-file.txt"}
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=100_000_000,
        estimator="s3_get_byte_estimate",
    )

    with patch("tessera.policy.conditions.boto3") as mock_boto:
        mock_boto.client.return_value = MagicMock(side_effect=Exception("NoCredentials"))
        result = evaluate_condition(cond, _ctx(args))

    # Falls back to static size of small args — should NOT block
    assert result is False


def test_operator_less_than():
    """bytes_threshold operator=less_than: small args < threshold → True."""
    args = {"Bucket": "b", "Key": "k"}
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=100_000,
        operator="less_than",
        estimator="static_arg_size",
    )
    ctx = _ctx(args)
    assert evaluate_condition(cond, ctx) is True


def test_rds_query_fallback_without_params():
    """rds_query_result_estimate falls back when cluster_arn is missing."""
    args = {"Statement": "SELECT * FROM users", "database": "mydb"}
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=100_000_000,
        estimator="rds_query_result_estimate",
    )
    ctx = _ctx(args)
    # No resourceArn → falls back to static size, which is tiny
    assert evaluate_condition(cond, ctx) is False
