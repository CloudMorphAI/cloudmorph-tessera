"""Minimal tests for OAuth 2.1 Resource Server endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app():
    from tessera.auth.oauth_rs import make_metadata_route
    from fastapi import FastAPI

    test_app = FastAPI()
    make_metadata_route(test_app)
    return test_app


@pytest.fixture()
def client(app):
    return TestClient(app)


def test_oauth_metadata_shape(client: TestClient) -> None:
    response = client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    data = response.json()
    assert "resource" in data
    assert "authorization_servers" in data
    assert "scopes_supported" in data
    assert "bearer_methods_supported" in data
    assert data["bearer_methods_supported"] == ["header"]
    assert isinstance(data["scopes_supported"], list)


def test_jwks_stub_shape(client: TestClient) -> None:
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    data = response.json()
    assert "keys" in data
    assert data["keys"] == []
