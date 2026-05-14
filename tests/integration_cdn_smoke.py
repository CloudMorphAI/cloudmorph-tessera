"""CDN tier-matrix integration smoke tests against the production license gate.

Tests make real HTTPS requests to https://intelligence.tessera.cloudmorph.ai
and require valid license JWTs supplied via environment variables.  Each test
skips gracefully when its required env var is absent, so the suite can be
committed and run in CI without secrets wired in.

How to run:

    export TESSERA_DEV_JWT=<developer-tier JWT from admin.cloudmorph.io>
    export TESSERA_TEAM_JWT=<team-tier JWT>
    export TESSERA_ENTERPRISE_JWT=<enterprise-tier JWT>
    export TESSERA_EXPIRED_JWT=<any expired JWT>
    pytest -m cdn_integration tests/integration_cdn_smoke.py -v

Env vars are intentionally **not** checked in.  Mint test JWTs from
admin.cloudmorph.io after B-5 (admin license-issuance UI) is live.

The mark cdn_integration allows selective runs:

    pytest -m cdn_integration tests/integration_cdn_smoke.py

All 8 scenarios are independent; any that lack their env var simply skip.
"""

from __future__ import annotations

import os

import httpx
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CDN_BASE = "https://intelligence.tessera.cloudmorph.ai"
_V = "v1.0.0"

# ---------------------------------------------------------------------------
# Fixture: shared httpx client + base URL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cdn_client() -> httpx.Client:
    """Return a module-scoped httpx.Client pointed at the production CDN."""
    with httpx.Client(
        base_url=CDN_BASE,
        follow_redirects=False,
        timeout=30,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jwt(env_var: str) -> str | None:
    """Return the JWT from *env_var*, or None when unset."""
    return os.environ.get(env_var)


def _auth(jwt: str) -> dict[str, str]:
    return {"X-Tessera-License": jwt}


# ---------------------------------------------------------------------------
# 8-scenario CDN tier-matrix
# ---------------------------------------------------------------------------


@pytest.mark.cdn_integration
def test_01_no_header_catalog_401(cdn_client: httpx.Client) -> None:
    """Anonymous request to catalog returns 401 (no license header)."""
    resp = cdn_client.get(f"/{_V}/catalogs/pack-index.json")
    assert resp.status_code == 401, f"expected 401, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_02_no_header_pack_401(cdn_client: httpx.Client) -> None:
    """Anonymous request to a pack tarball returns 401 (no license header)."""
    resp = cdn_client.get(f"/{_V}/packs/aws-cost-aware-defaults.tar.gz")
    assert resp.status_code == 401, f"expected 401, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_03_developer_catalog_200(cdn_client: httpx.Client) -> None:
    """developer-tier JWT can fetch the pack catalog (200)."""
    jwt = _jwt("TESSERA_DEV_JWT")
    if jwt is None:
        pytest.skip(reason="TESSERA_DEV_JWT not set — mint a developer JWT from admin.cloudmorph.io")
    resp = cdn_client.get(f"/{_V}/catalogs/pack-index.json", headers=_auth(jwt))
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_04_developer_pack_200(cdn_client: httpx.Client) -> None:
    """developer-tier JWT can fetch aws-cost-aware-defaults (200)."""
    jwt = _jwt("TESSERA_DEV_JWT")
    if jwt is None:
        pytest.skip(reason="TESSERA_DEV_JWT not set — mint a developer JWT from admin.cloudmorph.io")
    resp = cdn_client.get(f"/{_V}/packs/aws-cost-aware-defaults.tar.gz", headers=_auth(jwt))
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_05_developer_hipaa_403(cdn_client: httpx.Client) -> None:
    """developer-tier JWT is blocked from hipaa-guardrails (403 — above-tier pack)."""
    jwt = _jwt("TESSERA_DEV_JWT")
    if jwt is None:
        pytest.skip(reason="TESSERA_DEV_JWT not set — mint a developer JWT from admin.cloudmorph.io")
    resp = cdn_client.get(f"/{_V}/packs/hipaa-guardrails.tar.gz", headers=_auth(jwt))
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_06_team_hipaa_200(cdn_client: httpx.Client) -> None:
    """team-tier JWT can fetch hipaa-guardrails (200)."""
    jwt = _jwt("TESSERA_TEAM_JWT")
    if jwt is None:
        pytest.skip(reason="TESSERA_TEAM_JWT not set — mint a team JWT from admin.cloudmorph.io")
    resp = cdn_client.get(f"/{_V}/packs/hipaa-guardrails.tar.gz", headers=_auth(jwt))
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_07_enterprise_fintech_200(cdn_client: httpx.Client) -> None:
    """enterprise-tier JWT can fetch fintech-pack (200)."""
    jwt = _jwt("TESSERA_ENTERPRISE_JWT")
    if jwt is None:
        pytest.skip(reason="TESSERA_ENTERPRISE_JWT not set — mint an enterprise JWT from admin.cloudmorph.io")
    resp = cdn_client.get(f"/{_V}/packs/fintech-pack.tar.gz", headers=_auth(jwt))
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


@pytest.mark.cdn_integration
def test_08_expired_jwt_401(cdn_client: httpx.Client) -> None:
    """Expired JWT is rejected by the CloudFront license gate (401)."""
    jwt = _jwt("TESSERA_EXPIRED_JWT")
    if jwt is None:
        pytest.skip(reason="TESSERA_EXPIRED_JWT not set — provide any expired JWT")
    resp = cdn_client.get(f"/{_V}/packs/aws-cost-aware-defaults.tar.gz", headers=_auth(jwt))
    # CloudFront Function returns 401 for expired tokens (per intelligence-auth.js).
    # If the function ever changes this to 403, update the assertion here and in
    # intelligence-auth.js together so the two stay in sync.
    assert resp.status_code == 401, f"expected 401 for expired JWT, got {resp.status_code}"
