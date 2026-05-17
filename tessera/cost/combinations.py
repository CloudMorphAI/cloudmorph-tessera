"""CombinationTracker — track multi-op call chains and compute aggregate cost.

v0.6.0 (2026-05-17). Loads combination definitions from tessera-intelligence and
tracks active chains per (tenant, scope) with sliding windows. Exposed to the
policy engine via four new condition types in tessera.policy.conditions:

- combination_aggregate_cost_usd_gt
- combination_ops_count_gt
- combination_window_seconds_lt
- combination_id_matches

Design notes
------------
- Async-safe: no I/O in the hot path; tracker operations are CPU-only.
- Memory-bounded: per-tenant cap (default 1000 active chains), LRU eviction.
- Backwards-compatible: existing CostEstimator usage unchanged. Tracker is a
  separate class loaded only when combinations.enabled=true in tessera.yaml.
- Single-op cost evaluation reuses the existing cost_for_call() pipeline.
"""

from __future__ import annotations

import time
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


# ── In-memory combination definition (loaded from tessera-intelligence) ──────


@dataclass
class TriggerOp:
    tool_name: str
    role: str
    must_follow: list[str] = field(default_factory=list)


@dataclass
class CombinationDef:
    combination_id: str
    name: str
    description: str
    cloud: str
    trigger_ops: list[TriggerOp]
    window_seconds: int
    cost_runaway_severity: str
    blast_radius_severity: str
    ops_count: int

    @classmethod
    def from_yaml_dict(cls, data: dict) -> CombinationDef:
        ops = [
            TriggerOp(
                tool_name=op["tool_name"],
                role=op.get("role", "op"),
                must_follow=list(op.get("must_follow", [])),
            )
            for op in data.get("trigger_ops", [])
        ]
        rp = data.get("risk_profile", {})
        pp = data.get("policy_primitives", {})
        return cls(
            combination_id=data["combination_id"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            cloud=data.get("cloud", ""),
            trigger_ops=ops,
            window_seconds=int(data.get("window_seconds", 3600)),
            cost_runaway_severity=rp.get("cost_runaway_severity", "low"),
            blast_radius_severity=rp.get("blast_radius_severity", "low"),
            ops_count=int(pp.get("ops_count", len(ops))),
        )


# ── Active chain state ───────────────────────────────────────────────────────


@dataclass
class ActiveChain:
    combination_id: str
    started_at: float
    last_updated: float
    observed_ops: list[tuple[str, float, dict[str, Any]]] = field(default_factory=list)
    aggregate_cost_usd: float = 0.0
    principals: set[str] = field(default_factory=set)

    def is_complete(self, defn: CombinationDef) -> bool:
        """All ops in the combination's trigger_ops list have been observed."""
        seen = {op_name for op_name, _, _ in self.observed_ops}
        required = {op.tool_name for op in defn.trigger_ops}
        return required.issubset(seen)

    def is_expired(self, defn: CombinationDef, now: float) -> bool:
        return (now - self.started_at) > defn.window_seconds

    def ops_count(self) -> int:
        return len(self.observed_ops)

    def window_seconds(self, now: float) -> float:
        return now - self.started_at


# ── The tracker ──────────────────────────────────────────────────────────────


class CombinationTracker:
    """Track active call chains per (tenant_id, scope_id, combination_id) tuple."""

    DEFAULT_MAX_CHAINS_PER_TENANT = 1000

    def __init__(
        self,
        combinations: list[CombinationDef] | None = None,
        max_chains_per_tenant: int = DEFAULT_MAX_CHAINS_PER_TENANT,
        time_fn=time.time,
    ):
        self._defs_by_id: dict[str, CombinationDef] = {}
        self._defs_by_op: dict[str, list[CombinationDef]] = {}
        if combinations:
            for d in combinations:
                self._register(d)
        # OrderedDict acts as LRU; key = (tenant_id, scope_id, combination_id)
        self._active: OrderedDict[tuple[str, str, str], ActiveChain] = OrderedDict()
        self._max_chains_per_tenant = max_chains_per_tenant
        self._time_fn = time_fn
        self._lock = threading.RLock()

    def _register(self, defn: CombinationDef) -> None:
        self._defs_by_id[defn.combination_id] = defn
        for op in defn.trigger_ops:
            self._defs_by_op.setdefault(op.tool_name, []).append(defn)

    def load_from_yaml_docs(self, docs: list[dict]) -> None:
        """Load combinations from parsed YAML dicts (e.g. via IntelligenceClient)."""
        with self._lock:
            for d in docs:
                if "combination_id" not in d:
                    continue
                self._register(CombinationDef.from_yaml_dict(d))

    def get_definition(self, combination_id: str) -> CombinationDef | None:
        return self._defs_by_id.get(combination_id)

    def known_combination_ids(self) -> list[str]:
        return list(self._defs_by_id.keys())

    # ── Hot path: record an op ────────────────────────────────────────────────

    def record_op(
        self,
        tenant_id: str,
        scope_id: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
        per_op_cost_usd: float = 0.0,
        principal: str | None = None,
    ) -> list[ActiveChain]:
        """Record an op invocation. Returns list of active chains updated by this op.

        Caller is responsible for converting MCP tool call -> tool_name.
        per_op_cost_usd should come from existing cost_for_call().
        """
        args = args or {}
        now = self._time_fn()
        matching_defs = self._defs_by_op.get(tool_name, [])
        if not matching_defs:
            return []
        affected: list[ActiveChain] = []
        with self._lock:
            for defn in matching_defs:
                key = (tenant_id, scope_id, defn.combination_id)
                chain = self._active.get(key)
                if chain is None or chain.is_expired(defn, now):
                    chain = ActiveChain(
                        combination_id=defn.combination_id,
                        started_at=now,
                        last_updated=now,
                    )
                    self._active[key] = chain
                chain.observed_ops.append((tool_name, now, args))
                chain.aggregate_cost_usd += float(per_op_cost_usd)
                chain.last_updated = now
                if principal:
                    chain.principals.add(principal)
                # LRU touch
                self._active.move_to_end(key)
                affected.append(chain)
            self._evict_if_needed(tenant_id)
        return affected

    def _evict_if_needed(self, tenant_id: str) -> None:
        """Evict oldest chains for tenant if over cap. Assumes lock held."""
        tenant_chains = [(k, v) for k, v in self._active.items() if k[0] == tenant_id]
        if len(tenant_chains) <= self._max_chains_per_tenant:
            return
        # Remove oldest (front of OrderedDict for this tenant)
        to_remove = len(tenant_chains) - self._max_chains_per_tenant
        keys_to_remove = [k for k, _ in tenant_chains[:to_remove]]
        for k in keys_to_remove:
            self._active.pop(k, None)

    # ── Queries used by policy conditions ─────────────────────────────────────

    def get_active_chain(
        self, tenant_id: str, scope_id: str, combination_id: str
    ) -> ActiveChain | None:
        with self._lock:
            chain = self._active.get((tenant_id, scope_id, combination_id))
            if chain is None:
                return None
            defn = self._defs_by_id.get(combination_id)
            if defn is None:
                return None
            now = self._time_fn()
            if chain.is_expired(defn, now):
                # Lazy cleanup
                self._active.pop((tenant_id, scope_id, combination_id), None)
                return None
            return chain

    def aggregate_cost_usd(
        self, tenant_id: str, scope_id: str, combination_id: str
    ) -> float:
        c = self.get_active_chain(tenant_id, scope_id, combination_id)
        return c.aggregate_cost_usd if c else 0.0

    def ops_count(self, tenant_id: str, scope_id: str, combination_id: str) -> int:
        c = self.get_active_chain(tenant_id, scope_id, combination_id)
        return c.ops_count() if c else 0

    def window_seconds_elapsed(
        self, tenant_id: str, scope_id: str, combination_id: str
    ) -> float | None:
        c = self.get_active_chain(tenant_id, scope_id, combination_id)
        return c.window_seconds(self._time_fn()) if c else None

    def matches_combination_id(
        self,
        tenant_id: str,
        scope_id: str,
        combination_id: str,
    ) -> bool:
        """True iff there is an active chain for the given combination_id."""
        return self.get_active_chain(tenant_id, scope_id, combination_id) is not None

    def all_active_chains(
        self, tenant_id: str | None = None, scope_id: str | None = None
    ) -> list[ActiveChain]:
        with self._lock:
            out: list[ActiveChain] = []
            now = self._time_fn()
            for (tid, sid, cid), chain in self._active.items():
                if tenant_id is not None and tid != tenant_id:
                    continue
                if scope_id is not None and sid != scope_id:
                    continue
                defn = self._defs_by_id.get(cid)
                if defn is None or chain.is_expired(defn, now):
                    continue
                out.append(chain)
            return out

    # ── Maintenance ───────────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Remove all expired chains. Returns number removed."""
        removed = 0
        now = self._time_fn()
        with self._lock:
            stale_keys = []
            for key, chain in self._active.items():
                defn = self._defs_by_id.get(chain.combination_id)
                if defn is None or chain.is_expired(defn, now):
                    stale_keys.append(key)
            for k in stale_keys:
                self._active.pop(k, None)
                removed += 1
        return removed


# ── Module-level helpers ─────────────────────────────────────────────────────


_global_tracker: CombinationTracker | None = None


def get_global_tracker() -> CombinationTracker | None:
    """Return the process-wide tracker, or None if not initialized."""
    return _global_tracker


def set_global_tracker(tracker: CombinationTracker | None) -> None:
    """Install (or replace, or clear) the process-wide tracker."""
    global _global_tracker
    _global_tracker = tracker
