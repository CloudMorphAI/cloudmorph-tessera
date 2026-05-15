"""Quick sanity tests for the 0.2.1 cross-repo audit fixes.

These cover the tier-ordering alias and the PyJWT explicit dependency.
The manifest-signature-verify path is exercised end-to-end by the existing
test_intelligence_client.py integration tests (which now use base64
signatures matching the producer-side sign_pack.py).
"""

from __future__ import annotations


def test_tier_scale_and_team_same_rank() -> None:
    """`scale` is the canonical 0.2.1 tier name; `team` is kept as alias at the same rank."""
    from tessera.intelligence.client import _TIER_ORDER

    assert _TIER_ORDER["scale"] == 2
    assert _TIER_ORDER["team"] == 2
    assert _TIER_ORDER["scale"] == _TIER_ORDER["team"]
    # Ranking sanity
    assert _TIER_ORDER["free"] < _TIER_ORDER["developer"] < _TIER_ORDER["scale"] < _TIER_ORDER["enterprise"]


def test_pack_manifest_has_manifest_url_and_tarball_sha256() -> None:
    """Cross-repo audit added 2 new fields to PackManifest (default-empty)."""
    from tessera.intelligence.client import PackManifest

    m = PackManifest(
        name="x", version="1.0.0", min_tier="free",
        content_hash="", signature="", pack_url="", status="active",
    )
    assert m.manifest_url == ""
    assert m.tarball_sha256 == ""


def test_pyjwt_importable() -> None:
    """PyJWT must be importable as 'jwt' — explicit dep since 0.2.1 (was transitive)."""
    import jwt

    assert hasattr(jwt, "__version__")


def test_verify_signature_uses_base64_not_hex() -> None:
    """The producer emits base64 signatures; the consumer must base64-decode them.

    This regression-guards against the silent hex/base64 mismatch that masked
    catalog signature verification before P0-17 made it mandatory.
    """
    import base64
    import inspect

    from tessera.intelligence import client

    src = inspect.getsource(client.IntelligenceClient._verify_signature)
    assert "base64.b64decode" in src or "b64decode" in src, (
        "_verify_signature must use base64 decoding to match producer-side sign_pack.py"
    )
    # Sanity: base64 module itself imports cleanly (always true on CPython but cheap).
    assert callable(base64.b64decode)
