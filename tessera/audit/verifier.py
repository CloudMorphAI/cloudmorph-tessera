"""Chain-walk integrity verifier for tessera audit verify CLI command.

Three entry points, all built on the same core chain-walk
(`_walk_chain`), so the "recompute the hash chain" logic has exactly one
implementation regardless of where the events come from:

  - `verify_chain(sink, scope)`      — live SQLite sink, by scope (original,
                                        unchanged behavior/return shape)
  - `verify_events_file(path)`       — a bare JSONL file of events (e.g. an
                                        extracted events.jsonl with no
                                        manifest) — chain-only, offline
  - `verify_bundle_dir(dir_path)`    — a full evidence bundle directory
                                        (events.jsonl + manifest.json) —
                                        chain AND Ed25519 signature, offline
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from tessera.audit.chain import HashChain
from tessera.audit.export import signing_payload, summarize_events
from tessera.audit.signing import verify as verify_signature
from tessera.audit.sinks.base import AuditSink

__all__ = ["verify_chain", "verify_events_file", "verify_bundle_dir"]


def _walk_chain(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Core chain-walk: recompute event hashes + prevEventHash linkage.

    Returns the shared result shape (without a "scope"/"source" key — the
    caller adds that): events_checked, first_event_at, last_event_at, ok,
    first_failure.
    """
    events_checked = 0
    first_event_at: str | None = None
    last_event_at: str | None = None
    prev_event: dict[str, Any] | None = None

    for seq, event in enumerate(events, start=1):
        events_checked += 1
        occurred_at = event.get("occurredAt")

        if first_event_at is None:
            first_event_at = occurred_at
        last_event_at = occurred_at

        # Check individual event hash integrity first
        if not HashChain.verify_event_hash(event):
            return {
                "events_checked": events_checked,
                "first_event_at": first_event_at,
                "last_event_at": last_event_at,
                "ok": False,
                "first_failure": {
                    "seq": seq,
                    "event_id": event.get("eventId", ""),
                    "kind": "hash_mismatch",
                    "expected_event_hash": event.get("eventHash", ""),
                    "computed_event_hash": "",
                },
            }

        # Check chain linkage with previous event
        if prev_event is not None and not HashChain.verify_pair(prev_event, event):
            return {
                "events_checked": events_checked,
                "first_event_at": first_event_at,
                "last_event_at": last_event_at,
                "ok": False,
                "first_failure": {
                    "seq": seq,
                    "event_id": event.get("eventId", ""),
                    "kind": "chain_break",
                    "expected_event_hash": prev_event.get("eventHash", ""),
                    "computed_event_hash": event.get("prevEventHash", ""),
                },
            }

        prev_event = event

    return {
        "events_checked": events_checked,
        "first_event_at": first_event_at,
        "last_event_at": last_event_at,
        "ok": True,
        "first_failure": None,
    }


def verify_chain(sink: AuditSink, scope: str) -> dict[str, Any]:
    """Walk the audit chain for a scope and verify hash integrity.

    Returns:
        {
            "scope": scope,
            "events_checked": N,
            "first_event_at": str | None,
            "last_event_at": str | None,
            "ok": True/False,
            "first_failure": None | {
                "seq": int,
                "event_id": str,
                "kind": "hash_mismatch" | "chain_break",
                "expected_event_hash": str,
                "computed_event_hash": str,
            }
        }
    """
    result = _walk_chain(sink.iter_events(scope))
    return {"scope": scope, **result}


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def verify_events_file(path: str | Path) -> dict[str, Any]:
    """Verify hash-chain integrity of a bare JSONL file of audit events.

    Fully offline; no manifest/signature involved (use `verify_bundle_dir`
    for that). Returns the same shape as `verify_chain` but with "source"
    (the file path) instead of "scope".
    """
    p = Path(path)
    result = _walk_chain(_iter_jsonl(p))
    return {"source": str(p), **result}


def _public_key_identity(pem: str) -> bytes | None:
    """Return canonical DER identity for an Ed25519 public key, if valid."""
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except Exception:  # noqa: BLE001 — malformed key material is a verification result
        return None
    if not isinstance(key, Ed25519PublicKey):
        return None
    return key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def verify_bundle_dir(
    dir_path: str | Path,
    *,
    trusted_public_key_pem: str | None = None,
) -> dict[str, Any]:
    """Verify a full evidence bundle: events.jsonl chain + manifest.json Ed25519 signature.

    The signature is checked against a signing payload *recomputed from
    events.jsonl* (not read from manifest.json) — so an attacker who edits
    events.jsonl AND patches manifest.json's stated counts/hashes to match
    still fails verification, because the recomputed payload no longer
    matches the original signature.

    Returns the same shape as `verify_chain`/`verify_events_file` plus:
        "signature_ok": True | False | None   (None if no manifest.json present)
        "signature_trusted": True only when the signature verifies and the
            embedded Ed25519 key matches `trusted_public_key_pem`; False when
            a signature is present but no trusted anchor is supplied or trust
            fails; None when no manifest is present and no anchor is requested.
    If the signature is invalid but the chain itself is intact, `ok` is
    forced to False and `first_failure` is set to a `signature_invalid` entry
    (chain breaks, if any, still take priority as the "first" failure).
    """
    d = Path(dir_path)
    events_path = d / "events.jsonl"
    manifest_path = d / "manifest.json"
    trusted_identity = (
        _public_key_identity(trusted_public_key_pem)
        if trusted_public_key_pem is not None
        else None
    )
    if trusted_public_key_pem is not None and trusted_identity is None:
        return {
            "source": str(d),
            "events_checked": 0,
            "first_event_at": None,
            "last_event_at": None,
            "ok": False,
            "first_failure": {
                "seq": None,
                "event_id": "",
                "kind": "trusted_key_invalid",
                "expected_event_hash": "",
                "computed_event_hash": "",
            },
            "signature_ok": None,
            "signature_trusted": False,
            "manifest_consistent": None,
        }

    if not events_path.exists():
        return {
            "source": str(d),
            "events_checked": 0,
            "first_event_at": None,
            "last_event_at": None,
            "ok": False,
            "first_failure": {
                "seq": None,
                "event_id": "",
                "kind": "missing_events_file",
                "expected_event_hash": "",
                "computed_event_hash": "",
            },
            "signature_ok": None,
            "signature_trusted": None,
        }

    result = _walk_chain(_iter_jsonl(events_path))
    result["source"] = str(d)

    signature_ok: bool | None = None
    signature_trusted: bool | None = None
    manifest_consistent: bool | None = None
    trusted_key_matches = False
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None

        if manifest is None:
            signature_ok = False
            manifest_consistent = False
        else:
            signing_block = manifest.get("signing") or {}
            public_key_pem = signing_block.get("public_key_pem", "")
            signature = signing_block.get("signature", "")
            scope = manifest.get("scope", "")
            rng = manifest.get("range", {}) or {}

            # The signature is verified against a payload *recomputed from
            # events.jsonl* (not read from manifest.json) — this is what
            # actually proves events.jsonl is untampered: any edit to any
            # event changes that event's hash, which (once the chain is
            # rebuilt self-consistently) necessarily changes the recomputed
            # last_event_hash, which no longer matches what the original
            # private key signed.
            summary = summarize_events(_iter_jsonl(events_path))
            event_counts = {
                "total": summary["total"],
                "allow": summary["allow"],
                "block": summary["block"],
                "approval": summary["approval"],
                "log_only": summary["log_only"],
                "other": summary["other"],
            }
            chain = {
                "first_event_id": summary["first_event_id"],
                "first_event_hash": summary["first_event_hash"],
                "first_event_prev_hash": summary["first_event_prev_hash"],
                "last_event_id": summary["last_event_id"],
                "last_event_hash": summary["last_event_hash"],
            }
            payload_bytes = signing_payload(
                scope=scope,
                from_ts=rng.get("from", ""),
                to_ts=rng.get("to", ""),
                event_counts=event_counts,
                chain=chain,
            )
            signature_ok = bool(public_key_pem and signature) and verify_signature(
                public_key_pem, payload_bytes, signature
            )
            signature_trusted = False
            if trusted_public_key_pem is not None:
                embedded_identity = _public_key_identity(public_key_pem)
                if embedded_identity is not None:
                    trusted_key_matches = trusted_identity == embedded_identity
                    signature_trusted = bool(signature_ok and trusted_key_matches)

            # Separate check: does manifest.json's own *displayed*
            # event_counts/chain match what events.jsonl actually contains?
            # This catches a bundle where events.jsonl was left untouched
            # but manifest.json's human-readable numbers were hand-edited
            # (without re-signing) — the signature check above alone can't
            # catch that, since it never trusts manifest.json's declared
            # counts in the first place; it recomputes them.
            declared_counts = manifest.get("event_counts") or {}
            declared_chain = manifest.get("chain") or {}
            declared_chain_core = {k: declared_chain.get(k, "") for k in chain}
            manifest_consistent = declared_counts == event_counts and declared_chain_core == chain

        if result["ok"]:
            if not signature_ok:
                result = {
                    **result,
                    "ok": False,
                    "first_failure": {
                        "seq": None,
                        "event_id": "",
                        "kind": "signature_invalid",
                        "expected_event_hash": "",
                        "computed_event_hash": "",
                    },
                }
            elif trusted_public_key_pem is not None and not trusted_key_matches:
                result = {
                    **result,
                    "ok": False,
                    "first_failure": {
                        "seq": None,
                        "event_id": "",
                        "kind": "signer_untrusted",
                        "expected_event_hash": "",
                        "computed_event_hash": "",
                    },
                }
            elif not manifest_consistent:
                result = {
                    **result,
                    "ok": False,
                    "first_failure": {
                        "seq": None,
                        "event_id": "",
                        "kind": "manifest_mismatch",
                        "expected_event_hash": "",
                        "computed_event_hash": "",
                    },
                }

    elif trusted_public_key_pem is not None:
        signature_trusted = False
        result = {
            **result,
            "ok": False,
            "first_failure": {
                "seq": None,
                "event_id": "",
                "kind": "signature_missing",
                "expected_event_hash": "",
                "computed_event_hash": "",
            },
        }

    result["signature_ok"] = signature_ok
    result["signature_trusted"] = signature_trusted
    result["manifest_consistent"] = manifest_consistent
    return result
