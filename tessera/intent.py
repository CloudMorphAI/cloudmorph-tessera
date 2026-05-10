"""Intent extraction from MCP _meta field."""
from __future__ import annotations

from tessera.errors import PolicyError


def extract_intent(
    meta: dict | None,
    *,
    meta_key: str = "tessera_intent",
    intent_required: bool = False,
    known_verbs: frozenset[str] | None = None,
) -> dict | None:
    """Extract and validate intent from MCP _meta dict.

    Returns the intent dict or None if not present.
    Raises PolicyError if intent is malformed or required but missing.
    """
    if meta is None or meta_key not in meta:
        if intent_required:
            raise PolicyError("intent_required", reason="intent_required")
        return None

    intent = meta[meta_key]

    if not isinstance(intent, dict):
        raise PolicyError("intent must be a dict")

    # Validate 'verbs' — required when intent block is present
    if "verbs" not in intent:
        raise PolicyError("intent must contain a 'verbs' key")

    verbs = intent["verbs"]
    if not isinstance(verbs, list):
        raise PolicyError("intent 'verbs' must be a list")

    if known_verbs is not None:
        for verb in verbs:
            if verb not in known_verbs:
                raise PolicyError(
                    f"unknown intent verb: {verb!r}",
                    reason="unknown_verb",
                )

    # Validate 'purpose' — optional
    if "purpose" in intent:
        purpose = intent["purpose"]
        if not isinstance(purpose, str):
            raise PolicyError("intent 'purpose' must be a string")
        if len(purpose) > 1024:
            raise PolicyError(
                "intent 'purpose' must be at most 1024 characters",
                reason="purpose_too_long",
            )

    return intent
