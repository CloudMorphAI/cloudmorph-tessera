"""Unit tests for POST /intent endpoint."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
from tessera.proxy import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "allow-all.yaml").write_text(
        'id: allow-all\nname: Allow all\nmatch:\n  upstream: "*"\n  tool: "*"\naction: allow\npriority: 0\n',
        encoding="utf-8",
    )
    config = TesseraConfig(
        audit=AuditConfig(path=str(tmp_path / "audit.db"), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="allow",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[UpstreamConfig(name="mock", url="http://mock", timeout_seconds=5)],
        deployment_id="test",
    )
    with TestClient(create_app(config)) as c:
        yield c


def test_intent_happy_path(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        json={
            "tool_name": "aws.s3.delete_bucket",
            "tool_input": {"bucket": "my-bucket"},
            "command": "delete bucket",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "_meta" in body
    assert "tessera_intent" in body["_meta"]
    intent = body["_meta"]["tessera_intent"]
    assert "write.delete" in intent["verbs"]
    assert isinstance(intent["purpose"], str)
    assert len(intent["purpose"]) > 0


def test_intent_purpose_includes_command(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        json={
            "tool_name": "aws.s3.delete_bucket",
            "command": "delete bucket",
        },
    )
    assert resp.status_code == 200
    purpose = resp.json()["_meta"]["tessera_intent"]["purpose"]
    assert "delete bucket" in purpose


def test_intent_purpose_includes_tool_input_string_values(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        json={
            "tool_name": "aws.s3.put_object",
            "tool_input": {"key": "myfile.txt", "bucket": "my-bucket"},
        },
    )
    assert resp.status_code == 200
    purpose = resp.json()["_meta"]["tessera_intent"]["purpose"]
    # key=value pairs from tool_input should appear (sorted)
    assert "bucket=my-bucket" in purpose
    assert "key=myfile.txt" in purpose


def test_intent_purpose_fallback_tool_name(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        json={"tool_name": "aws.ec2.list_instances"},
    )
    assert resp.status_code == 200
    purpose = resp.json()["_meta"]["tessera_intent"]["purpose"]
    assert purpose == "tool:aws.ec2.list_instances"


def test_intent_unknown_tool_empty_verbs(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        json={"tool_name": "unknown.custom.tool"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["_meta"]["tessera_intent"]["verbs"] == []


def test_intent_verbs_sorted(client: TestClient) -> None:
    # aws.s3.put_object → {write.create, write.update} — ensure sorted order
    resp = client.post(
        "/intent",
        json={"tool_name": "aws.s3.put_object"},
    )
    assert resp.status_code == 200
    verbs = resp.json()["_meta"]["tessera_intent"]["verbs"]
    assert verbs == sorted(verbs)


def test_intent_missing_tool_name(client: TestClient) -> None:
    # tool_name is required — validation fails → 422
    resp = client.post("/intent", json={})
    assert resp.status_code == 422


def test_intent_invalid_json_body(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_intent_response_shape(client: TestClient) -> None:
    resp = client.post(
        "/intent",
        json={"tool_name": "aws_iam_list_users"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Top-level key must be _meta only
    assert set(body.keys()) == {"_meta"}
    meta = body["_meta"]
    assert set(meta.keys()) == {"tessera_intent"}
    ti = meta["tessera_intent"]
    assert "verbs" in ti
    assert "purpose" in ti
    assert isinstance(ti["verbs"], list)
    assert isinstance(ti["purpose"], str)
