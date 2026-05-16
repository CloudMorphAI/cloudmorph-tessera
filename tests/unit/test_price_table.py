"""Unit tests for tessera.cost.price_table.PriceTable."""

from __future__ import annotations

import json

import pytest

from tessera.cost.price_table import CostEstimate, PriceTable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_price_table(tmp_path, operations: dict, ceiling_bands: dict | None = None) -> object:
    """Write a minimal price-table JSON to tmp_path and return the Path."""
    data = {
        "schema_version": "1",
        "bundle_version": "v1.0.0",
        "provider": "aws",
        "generated_at": "2026-05-14T00:00:00Z",
        "operations": operations,
        "ceiling_bands": ceiling_bands or {},
    }
    p = tmp_path / "aws-prices-v1.0.0.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_price_table_loads_and_indexes(tmp_path) -> None:
    """PriceTable loads a valid JSON artifact and indexes all operations."""
    ops = {
        "aws_ec2_RunInstances": {
            "price_realms": ["on_demand"],
            "lookups": [
                {"params": {"instance_type": "t3.micro", "region": "us-east-1"}, "price_usd_per_hour": 0.0104},
                {"params": {"instance_type": "m5.large", "region": "us-east-1"}, "price_usd_per_hour": 0.096},
            ],
        },
        "aws_s3_PutObject": {
            "price_realms": ["on_demand"],
            "lookups": [
                {"params": {}, "price_usd_per_hour": 0.000005},
            ],
        },
    }
    path = _write_price_table(tmp_path, ops)
    pt = PriceTable(path)

    assert pt.operation_count == 2
    assert pt.provider == "aws"
    assert pt.bundle_version == "v1.0.0"


def test_cost_for_call_returns_match(tmp_path) -> None:
    """cost_for_call returns the correct CostEstimate for a known operation + params."""
    ops = {
        "aws_ec2_RunInstances": {
            "price_realms": ["on_demand"],
            "lookups": [
                {"params": {"instance_type": "t3.micro", "region": "us-east-1"}, "price_usd_per_hour": 0.0104},
            ],
        },
    }
    path = _write_price_table(tmp_path, ops)
    pt = PriceTable(path)

    result = pt.cost_for_call(
        "aws_ec2_RunInstances",
        {"instance_type": "t3.micro"},
        region="us-east-1",
    )

    assert result is not None
    assert isinstance(result, CostEstimate)
    assert result.operation == "aws_ec2_RunInstances"
    assert result.price_usd == pytest.approx(0.0104)
    assert result.realm == "on_demand"


def test_cost_for_call_returns_none_for_unmapped(tmp_path) -> None:
    """cost_for_call returns None for an operation not present in the table."""
    ops = {
        "aws_ec2_RunInstances": {
            "price_realms": ["on_demand"],
            "lookups": [
                {"params": {}, "price_usd_per_hour": 0.05},
            ],
        },
    }
    path = _write_price_table(tmp_path, ops)
    pt = PriceTable(path)

    result = pt.cost_for_call("aws_rds_CreateDBInstance", {"db_instance_class": "db.t3.micro"})

    assert result is None
