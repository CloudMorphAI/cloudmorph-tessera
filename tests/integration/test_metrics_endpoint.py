"""Integration tests for the /metrics endpoint."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from tessera.config import (
    AuditConfig,
    IntentConfig,
    MetricsConfig,
    PoliciesConfig,
    PoliciesMode,
    RuntimeConfig,
    TesseraConfig,
)
from tessera.proxy import create_app

_MAIN_TOKEN = "tk_test_alice_xxxxxxxxxxxxxxxxxx"
_METRICS_TOKEN = "tk_test_metrics_xxxxxxxxxxxxx"
_HEADERS_ALICE = {"Authorization": f"Bearer {_MAIN_TOKEN}"}
_HEADERS_METRICS = {"Authorization": f"Bearer {_METRICS_TOKEN}"}


def _make_config(tmp_path: Path, metrics_enabled: bool) -> TesseraConfig:
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir(exist_ok=True)
    (policy_dir / "allow-all.yaml").write_text(
        'id: allow-all\nname: Allow all\nmatch:\n  upstream: "*"\n  tool: "*"\naction: allow\n',
        encoding="utf-8",
    )
    return TesseraConfig(
        audit=AuditConfig(path=str(tmp_path / "audit.db"), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="allow",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=metrics_enabled, bearer_token_env="TESSERA_METRICS_TOKEN"),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[],
        deployment_id="test",
    )


def test_metrics_disabled_returns_404(tmp_path: Path) -> None:
    """/metrics returns 404 when metrics.enabled=False."""
    config = _make_config(tmp_path, metrics_enabled=False)
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/metrics", headers=_HEADERS_ALICE)
    assert resp.status_code == 404


def test_metrics_enabled_no_token_returns_401(tmp_path: Path) -> None:
    """/metrics returns 401 when metrics enabled but no token provided."""
    config = _make_config(tmp_path, metrics_enabled=True)
    app = create_app(config)
    # Ensure no TESSERA_METRICS_TOKEN is set in env
    original = os.environ.pop("TESSERA_METRICS_TOKEN", None)
    original_main = os.environ.get("TESSERA_BEARER_TOKENS")
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_MAIN_TOKEN}"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/metrics")  # no auth header
    finally:
        if original is not None:
            os.environ["TESSERA_METRICS_TOKEN"] = original
        if original_main is None:
            os.environ.pop("TESSERA_BEARER_TOKENS", None)
        else:
            os.environ["TESSERA_BEARER_TOKENS"] = original_main
    assert resp.status_code == 401


def test_metrics_enabled_with_main_token_returns_200(tmp_path: Path) -> None:
    """/metrics returns 200 when metrics enabled and main token used (no dedicated token set)."""
    config = _make_config(tmp_path, metrics_enabled=True)
    app = create_app(config)
    original = os.environ.pop("TESSERA_METRICS_TOKEN", None)
    original_main = os.environ.get("TESSERA_BEARER_TOKENS")
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_MAIN_TOKEN}"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/metrics", headers=_HEADERS_ALICE)
    finally:
        if original is not None:
            os.environ["TESSERA_METRICS_TOKEN"] = original
        if original_main is None:
            os.environ.pop("TESSERA_BEARER_TOKENS", None)
        else:
            os.environ["TESSERA_BEARER_TOKENS"] = original_main
    assert resp.status_code == 200
    assert "tessera_requests_total" in resp.text


def test_metrics_enabled_with_dedicated_token_returns_200(tmp_path: Path) -> None:
    """/metrics returns 200 when dedicated TESSERA_METRICS_TOKEN is set and used."""
    config = _make_config(tmp_path, metrics_enabled=True)
    app = create_app(config)
    original = os.environ.get("TESSERA_METRICS_TOKEN")
    original_main = os.environ.get("TESSERA_BEARER_TOKENS")
    os.environ["TESSERA_METRICS_TOKEN"] = _METRICS_TOKEN
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_MAIN_TOKEN}"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/metrics", headers=_HEADERS_METRICS)
    finally:
        if original is None:
            os.environ.pop("TESSERA_METRICS_TOKEN", None)
        else:
            os.environ["TESSERA_METRICS_TOKEN"] = original
        if original_main is None:
            os.environ.pop("TESSERA_BEARER_TOKENS", None)
        else:
            os.environ["TESSERA_BEARER_TOKENS"] = original_main
    assert resp.status_code == 200
    assert "tessera_requests_total" in resp.text


def test_metrics_enabled_dedicated_token_rejects_main_token(tmp_path: Path) -> None:
    """When TESSERA_METRICS_TOKEN is set, the main token is NOT accepted for /metrics."""
    config = _make_config(tmp_path, metrics_enabled=True)
    app = create_app(config)
    original = os.environ.get("TESSERA_METRICS_TOKEN")
    original_main = os.environ.get("TESSERA_BEARER_TOKENS")
    os.environ["TESSERA_METRICS_TOKEN"] = _METRICS_TOKEN
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_MAIN_TOKEN}"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            # Use main token, not metrics token
            resp = client.get("/metrics", headers=_HEADERS_ALICE)
    finally:
        if original is None:
            os.environ.pop("TESSERA_METRICS_TOKEN", None)
        else:
            os.environ["TESSERA_METRICS_TOKEN"] = original
        if original_main is None:
            os.environ.pop("TESSERA_BEARER_TOKENS", None)
        else:
            os.environ["TESSERA_BEARER_TOKENS"] = original_main
    assert resp.status_code == 401
