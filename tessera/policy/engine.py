"""PolicyEngine — evaluate policies against a request context."""

from __future__ import annotations

from typing import Any

from tessera.policy.conditions import (
    clear_decision_errors,
    evaluate_conditions,
    get_decision_errors,
)
from tessera.policy.matchers import match_tool, match_upstream
from tessera.policy.schema import Action, Decision, Policy


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
