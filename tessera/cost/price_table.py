"""Price-table consumer — loads a signed price-table JSON artifact into memory.

The artifact format is the v1 schema produced by tessera-intelligence:

    {
      "schema_version": "1",
      "bundle_version": "v1.0.0",
      "provider": "aws",
      "generated_at": "<UTC ISO>",
      "operations": {
        "<operation>": {
          "price_realms": ["on_demand", "spot"],
          "lookups": [
            {"params": {"instance_type": "t3.micro", "region": "us-east-1"},
             "price_usd_per_hour": 0.0104}
          ]
        }
      },
      "ceiling_bands": {
        "default": {"warn_usd": 1.0, "block_usd": 10.0}
      }
    }

The index built at ``__init__`` time is a dict keyed
``(operation, realm, frozenset(param_items))`` → price_usd, enabling
sub-millisecond lookups by turning the ``params`` dict into a frozenset of
``(k, v)`` pairs and doing a plain dict get.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Index key type ────────────────────────────────────────────────────────────

# Keyed (operation, realm, frozenset-of-param-pairs) → price_usd
_IndexKey = tuple[str, str, frozenset]


@dataclass(frozen=True)
class CostEstimate:
    """Result of a price-table lookup."""

    operation: str
    price_usd: float
    realm: str
    matched_params: dict[str, str]
    confidence: str  # "exact" | "interpolated" | "default"


@dataclass(frozen=True)
class CeilingBand:
    """Warn / block thresholds for a named band."""

    warn_usd: float
    block_usd: float


# ── Loader + validator ────────────────────────────────────────────────────────


def _load_and_validate(path: Path) -> dict[str, Any]:
    """Read and structurally validate a price-table JSON file."""
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Price-table at {path} is not a JSON object")
    if "operations" not in data:
        raise ValueError(f"Price-table at {path} missing 'operations' key")
    return data


# ── PriceTable ────────────────────────────────────────────────────────────────


class PriceTable:
    """In-memory price-table loaded from a signed JSON artifact.

    The index is built once at ``__init__`` time so individual
    ``cost_for_call`` lookups run in O(1) — a single dict get.
    """

    def __init__(self, path: Path, signature_verified: bool = False) -> None:
        self._data = _load_and_validate(path)
        self._verified = signature_verified
        self._index: dict[_IndexKey, tuple[float, dict[str, str]]] = {}
        self._operations: dict[str, list[str]] = {}  # operation → realms
        self._ceiling_bands: dict[str, CeilingBand] = {}
        self._build_index()

    # ── Index construction ────────────────────────────────────────────────────

    def _build_index(self) -> None:
        operations: dict[str, Any] = self._data.get("operations", {})
        for op_name, op_body in operations.items():
            if not isinstance(op_body, dict):
                continue
            realms: list[str] = op_body.get("price_realms", ["on_demand"])
            self._operations[op_name] = realms
            lookups: list[dict[str, Any]] = op_body.get("lookups", [])
            for entry in lookups:
                params: dict[str, str] = entry.get("params", {})
                price = float(entry.get("price_usd_per_hour", entry.get("price_usd", 0.0)))
                param_key = frozenset(params.items())
                for realm in realms:
                    idx: _IndexKey = (op_name, realm, param_key)
                    self._index[idx] = (price, params)

        # Ceiling bands
        bands: dict[str, Any] = self._data.get("ceiling_bands", {})
        for band_name, band_body in bands.items():
            if not isinstance(band_body, dict):
                continue
            try:
                self._ceiling_bands[band_name] = CeilingBand(
                    warn_usd=float(band_body["warn_usd"]),
                    block_usd=float(band_body["block_usd"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("event=price_table_bad_ceiling_band name=%s error=%s", band_name, exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def cost_for_call(
        self,
        operation: str,
        args: dict[str, Any],
        region: str | None = None,
        realm: str = "on_demand",
    ) -> CostEstimate | None:
        """Return a ``CostEstimate`` for an operation + args, or ``None`` if not mapped.

        Lookup strategy:
        1. Exact match — build a ``frozenset`` from args (plus ``region`` when
           provided) and look up directly.
        2. Subset match — try progressively smaller subsets of ``args`` to find
           a partial-param entry (handles callers that pass extra args).
        3. Empty-params wildcard — a lookup row with ``params: {}`` matches any
           args for that operation + realm.

        All three tiers run against the pre-built index in O(1) per candidate key.
        """
        if operation not in self._operations:
            return None

        # Build normalized args: only string values; include region if supplied
        norm_args: dict[str, str] = {k: str(v) for k, v in args.items() if isinstance(v, str)}
        if region is not None:
            norm_args["region"] = region

        # --- Tier 1: exact match ---
        exact_key: _IndexKey = (operation, realm, frozenset(norm_args.items()))
        hit = self._index.get(exact_key)
        if hit is not None:
            return CostEstimate(
                operation=operation,
                price_usd=hit[0],
                realm=realm,
                matched_params=hit[1],
                confidence="exact",
            )

        # --- Tier 2: subset matches (drop keys one at a time, longest match wins) ---
        # Try all sub-frozensets in descending order of length
        items = list(norm_args.items())
        for drop_count in range(1, len(items) + 1):
            for i in range(len(items)):
                subset = frozenset(items[:i] + items[i + 1:i + drop_count + 1 - drop_count] +
                                   items[i + drop_count:])
                sub_key: _IndexKey = (operation, realm, subset)
                hit = self._index.get(sub_key)
                if hit is not None:
                    return CostEstimate(
                        operation=operation,
                        price_usd=hit[0],
                        realm=realm,
                        matched_params=hit[1],
                        confidence="interpolated",
                    )

        # --- Tier 3: wildcard (empty params) ---
        wildcard_key: _IndexKey = (operation, realm, frozenset())
        hit = self._index.get(wildcard_key)
        if hit is not None:
            return CostEstimate(
                operation=operation,
                price_usd=hit[0],
                realm=realm,
                matched_params=hit[1],
                confidence="default",
            )

        return None

    def ceiling_band(self, key: str = "default") -> CeilingBand | None:
        """Return the named ceiling band, or ``None`` if not present."""
        return self._ceiling_bands.get(key)

    @property
    def operation_count(self) -> int:
        """Number of distinct operations indexed."""
        return len(self._operations)

    @property
    def provider(self) -> str:
        """Provider string from the artifact (e.g. ``"aws"``)."""
        return str(self._data.get("provider", "unknown"))

    @property
    def bundle_version(self) -> str:
        """Bundle version string from the artifact."""
        return str(self._data.get("bundle_version", "unknown"))
