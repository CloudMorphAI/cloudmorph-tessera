from __future__ import annotations

import json

import pytest

from tessera.cost import CostResult, register_price_table, cost_for_call, _PRICE_TABLE_REGISTRY
from tessera.cost.price_table import PriceTable


def _write_price_table(tmp_path, provider, operations, ceiling_bands=None, version='v1.0.0'):
    data = {
        'schema_version': '1',
        'bundle_version': version,
        'provider': provider,
        'generated_at': '2026-05-16T00:00:00Z',
        'operations': operations,
        'ceiling_bands': ceiling_bands or {},
    }
    p = tmp_path / f'{provider}-prices-{version}.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


def _make_table(tmp_path, provider, operations, **kwargs):
    path = _write_price_table(tmp_path, provider, operations, **kwargs)
    return PriceTable(path)


@pytest.fixture(autouse=True)
def clear_registry():
    _PRICE_TABLE_REGISTRY.clear()
    yield
    _PRICE_TABLE_REGISTRY.clear()


def test_cost_for_call_empty_registry_returns_miss():
    result = cost_for_call('aws_ec2_RunInstances', {'InstanceType': 't3.micro'})
    assert result is not None
    assert result.source == 'miss'
    assert result.price_usd is None
    assert result.operation == 'aws_ec2_RunInstances'


def test_cost_for_call_routes_to_aws_table(tmp_path):
    ops = {
        'aws_ec2_RunInstances': {
            'price_realms': ['on_demand'],
            'confidence_band': 'high',
            'lookups': [{'params': {}, 'price_usd_per_hour': 0.0104}],
        },
    }
    register_price_table('aws', _make_table(tmp_path, 'aws', ops))
    result = cost_for_call('aws_ec2_RunInstances', {'InstanceType': 't3.micro'})
    assert result.source == 'price_table'
    assert result.price_usd == pytest.approx(0.0104)
    assert result.confidence_band == 'high'
    assert result.operation == 'aws_ec2_RunInstances'


def test_cost_for_call_routes_to_azure_table(tmp_path):
    ops = {
        'azure_compute_VirtualMachines_CreateOrUpdate': {
            'price_realms': ['on_demand'],
            'confidence_band': 'medium',
            'lookups': [{'params': {}, 'price_usd_per_hour': 0.096}],
        },
    }
    register_price_table('azure', _make_table(tmp_path, 'azure', ops))
    result = cost_for_call('azure_compute_VirtualMachines_CreateOrUpdate', {'vmSize': 'Standard_D2s_v3'})
    assert result.source == 'price_table'
    assert result.price_usd == pytest.approx(0.096)
    assert result.confidence_band == 'medium'


def test_cost_for_call_routes_to_gcp_table(tmp_path):
    ops = {
        'gcp_compute_instances_insert': {
            'price_realms': ['on_demand'],
            'confidence_band': 'medium',
            'lookups': [{'params': {}, 'price_usd_per_hour': 0.047}],
        },
    }
    register_price_table('gcp', _make_table(tmp_path, 'gcp', ops))
    result = cost_for_call('gcp_compute_instances_insert', {'machineType': 'n1-standard-2'})
    assert result.source == 'price_table'
    assert result.price_usd == pytest.approx(0.047)


def test_cost_for_call_unknown_prefix_returns_miss():
    result = cost_for_call('unknown_SomeService_SomeOp', {})
    assert result.source == 'miss'
    assert result.price_usd is None
    assert result.operation == 'unknown_SomeService_SomeOp'


def test_cost_result_miss_factory():
    result = CostResult.miss('aws_ec2_RunInstances')
    assert result.source == 'miss'
    assert result.price_usd is None
    assert result.unit == ''
    assert result.confidence_band == ''
    assert result.operation == 'aws_ec2_RunInstances'


def test_ceiling_band_confidence_returned(tmp_path):
    ops = {
        'aws_bedrock_invokeModel': {
            'price_realms': ['on_demand'],
            'confidence_band': 'ceiling',
            'lookups': [{'params': {}, 'price_usd_per_hour': 0.50}],
        },
    }
    register_price_table('aws', _make_table(tmp_path, 'aws', ops))
    result = cost_for_call('aws_bedrock_invokeModel', {'modelId': 'claude-3-5-sonnet'})
    assert result.source == 'price_table'
    assert result.confidence_band == 'ceiling'
    assert result.price_usd == pytest.approx(0.50)


def test_aws_op_not_in_table_returns_miss(tmp_path):
    ops = {
        'aws_s3_PutObject': {
            'price_realms': ['on_demand'],
            'confidence_band': 'high',
            'lookups': [{'params': {}, 'price_usd_per_hour': 0.000005}],
        },
    }
    register_price_table('aws', _make_table(tmp_path, 'aws', ops))
    result = cost_for_call('aws_rds_CreateDBInstance', {'DBInstanceClass': 'db.t3.micro'})
    assert result.source == 'miss'
    assert result.price_usd is None
