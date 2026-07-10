"""Ed25519 instance-key signing for audit evidence bundles.

This is a *local instance key* — auto-generated on first use and persisted
to disk (mode 0600) so evidence bundles produced by this Tessera install are
self-consistently signed and can be verified fully offline (the public key
is embedded in every bundle's ``manifest.json``).

This module NEVER reads or references Tessera's production/org signing
material (e.g. the Intelligence content-signing key under
``tessera/intelligence/public_key.pem`` or any private key held by
CloudMorph). It only ever touches the path resolved by
:func:`resolve_signing_key_path`, which defaults to a per-machine file under
the user's home directory.

Key resolution order (first match wins):
  1. The ``key_path`` argument passed by the caller (e.g. CLI ``--signing-key``)
  2. The ``TESSERA_AUDIT_SIGNING_KEY_PATH`` environment variable
  3. ``~/.tessera/audit-signing-key.pem`` (created on first use)

This slot is intentionally overridable so a future org-wide/customer-managed
signing key can be dropped in via config/env without any code change here —
callers just point ``TESSERA_AUDIT_SIGNING_KEY_PATH`` (or ``--signing-key``)
at that key's path instead.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

__all__ = [
    "resolve_signing_key_path",
    "load_or_create_signing_key",
    "public_key_pem",
    "sign",
    "verify",
]

_ENV_KEY_PATH = "TESSERA_AUDIT_SIGNING_KEY_PATH"  # noqa: S105 — not a secret, it's an env var name
_DEFAULT_KEY_PATH = Path.home() / ".tessera" / "audit-signing-key.pem"


def resolve_signing_key_path(key_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the signing-key path per the precedence documented on this module."""
    if key_path:
        return Path(key_path)
    env_val = os.environ.get(_ENV_KEY_PATH)
    if env_val:
        return Path(env_val)
    return _DEFAULT_KEY_PATH


def load_or_create_signing_key(
    key_path: str | os.PathLike[str] | None = None,
) -> Ed25519PrivateKey:
    """Load the instance signing key from disk, generating + persisting one if absent.

    The generated key is an unencrypted PKCS8 PEM written with mode 0600
    (best-effort on platforms where chmod is a no-op, e.g. Windows). Written
    via a temp-file + atomic rename so a crash mid-write never leaves a
    corrupt key file.
    """
    path = resolve_signing_key_path(key_path)

    if path.exists():
        pem_bytes = path.read_bytes()
        key = load_pem_private_key(pem_bytes, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"Signing key at {path} is not an Ed25519 private key")
        return key

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(pem)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        # Windows / filesystems without POSIX permissions — non-fatal.
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def public_key_pem(private_key: Ed25519PrivateKey) -> str:
    """Return the SubjectPublicKeyInfo PEM for a private key, for embedding in a manifest."""
    pub = private_key.public_key()
    pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return pem.decode("ascii")


def sign(private_key: Ed25519PrivateKey, payload: bytes) -> str:
    """Sign ``payload`` with the Ed25519 private key; return a base64-encoded signature."""
    return base64.b64encode(private_key.sign(payload)).decode("ascii")


def verify(public_key_pem_str: str, payload: bytes, signature_b64: str) -> bool:
    """Verify a base64 Ed25519 signature over ``payload`` using a PEM public key.

    Returns False (never raises) on any failure — malformed PEM, malformed
    base64, or an invalid signature all just mean "not verified".
    """
    try:
        key = load_pem_public_key(public_key_pem_str.encode("ascii"))
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(base64.b64decode(signature_b64), payload)
        return True
    except Exception:  # noqa: BLE001 — signature verification failure is a boolean, not an exception path
        return False
