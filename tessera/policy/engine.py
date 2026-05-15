"""PolicyEngine — evaluate policies against a request context."""

from __future__ import annotations

from typing import Any

from tessera.policy.conditions import (
    clear_decision_errors,
    evaluate_conditions,
    get_decision_errors,
)
from tessera.policy.matchers import match_tool, match_upstream
from tessera.policy.schema import (
    Action,
    AnyOf,
    BlastRadius,
    ConditionType,
    DataVolume,
    Decision,
    NoneOf,
    Policy,
)


class PolicyEngine:
    def __init__(
        self,
        policies: list[Policy],
        default_action: Action = Action.block,
    ) -> None:
        self._policies = policies  # already sorted by loader
        self._default_action = default_action

    def evaluate(self, context: dict[str, Any]) -> Decision:
        """Evaluate sorted policy list. First-match-wins. Mode-agnostic.

        context: same shape as conditions.evaluate_condition expects, plus:
        - context["runtime"]["lockdown"]: bool — checked BEFORE policy loop

        Returns Decision. Never raises.
        """
        # 1. Lockdown short-circuit
        if context.get("runtime", {}).get("lockdown"):
            return Decision(Action.block, "lockdown_active", None)

        # 2. Walk sorted policies
        tool_call = context.get("tool_call", {})
        tool_name = tool_call.get("name", "")
        request_upstream = context.get("upstream", "")
        intent = context.get("intent")

        for policy in self._policies:
            # a. Upstream match
            if not match_upstream(policy.match.upstream, request_upstream):
                continue

            # b. Tool match
            if not match_tool(policy.match.tool, policy.match.tool_pattern, tool_name):
                continue

            # c. require_intent: skip if intent is required but absent
            if policy.match.require_intent and intent is None:
                continue

            # d. Evaluate when conditions
            eval_context = {**context, "policy_id": policy.id}
            clear_decision_errors()
            matched = evaluate_conditions(policy.when, eval_context)

            # e. First match wins
            if matched:
                errors = get_decision_errors()
                return Decision(
                    action=policy.action,
                    reason=policy.reason,
                    policy_id=policy.id,
                    decision_error=errors[0] if errors else None,
                )

        # 3. No match
        return Decision(self._default_action, "default", None)

    # ── Pre-fetch helpers (P0-14, P0-15) ──────────────────────────────────────
    # The proxy hot path calls these before building the request context. When
    # they return True / a non-empty set, the proxy runs the matching boto3
    # call(s) via asyncio.to_thread so the event loop stays free during the
    # network round-trip.

    def policies_need_blast_radius(
        self, tool_name: str, upstream_name: str
    ) -> bool:
        """Return True if any matching policy uses a BlastRadius condition that
        targets `tool_name`. Used by proxy.py to gate the IAM-read prefetch.
        """
        for policy in self._policies:
            if not match_upstream(policy.match.upstream, upstream_name):
                continue
            if not match_tool(policy.match.tool, policy.match.tool_pattern, tool_name):
                continue
            for cond in policy.when:
                if self._has_blast_radius_for(cond, tool_name):
                    return True
        return False

    def _has_blast_radius_for(
        self, cond: ConditionType, tool_name: str
    ) -> bool:
        """Recursively check a condition tree for a BlastRadius targeting tool_name."""
        if isinstance(cond, BlastRadius):
            resource_types = getattr(cond, "resource_types", None)
            if not resource_types:
                return True
            return tool_name in resource_types
        if isinstance(cond, (AnyOf, NoneOf)):
            return any(
                self._has_blast_radius_for(c, tool_name) for c in cond.conditions
            )
        return False

    def policies_need_data_volume(
        self, tool_name: str, upstream_name: str
    ) -> set[str]:
        """Return the set of DataVolume estimators used by any matching policy.

        Empty set means no DataVolume prefetch is required for this request.
        """
        estimators: set[str] = set()
        for policy in self._policies:
            if not match_upstream(policy.match.upstream, upstream_name):
                continue
            if not match_tool(policy.match.tool, policy.match.tool_pattern, tool_name):
                continue
            for cond in policy.when:
                self._collect_data_volume_estimators(cond, estimators)
        return estimators

    def _collect_data_volume_estimators(
        self, cond: ConditionType, out: set[str]
    ) -> None:
        if isinstance(cond, DataVolume):
            out.add(cond.estimator)
        elif isinstance(cond, (AnyOf, NoneOf)):
            for c in cond.conditions:
                self._collect_data_volume_estimators(c, out)
