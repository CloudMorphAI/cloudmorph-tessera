"""Offline feasibility proof for the Hermes CRM nested-field demo policy."""

from __future__ import annotations

import json
from pathlib import Path

from tessera.policy.engine import PolicyEngine
from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import Action, ArgPathMatchesRegex, MatchSpec, Policy


def main() -> None:
    policy_dir = Path(__file__).parent
    policies = FilesystemPolicyLoader(policy_dir).load_all()
    # The current filesystem loader accepts one mapping per YAML file. Add the
    # companion allow policies in memory so this proof can stay self-contained.
    policies.extend([
        Policy(
            id="hermes-crm-allow-call-notes-update",
            name="Allow call notes updates",
            match=MatchSpec(upstream="crm", tool="crm.update_contact"),
            when=[ArgPathMatchesRegex(arg_path="fields.call_notes", pattern="(?s).*")],
            action=Action.allow,
            reason="call_notes_update_allowed",
            priority=10,
        ),
        Policy(
            id="hermes-crm-allow-contact-read",
            name="Allow contact reads",
            match=MatchSpec(upstream="crm", tool="crm.get_contact"),
            action=Action.allow,
            reason="contact_read_allowed",
            priority=10,
        ),
    ])
    policies.sort(key=lambda policy: (-policy.priority, policy.id))
    engine = PolicyEngine(policies, default_action=Action.block)

    scenarios = [
        (
            "call_notes update",
            "crm.update_contact",
            {"record_id": "crm-demo-001", "fields": {"call_notes": "Follow up"}},
        ),
        (
            "paid billing update",
            "crm.update_contact",
            {"record_id": "crm-demo-001", "fields": {"billing_status": "paid"}},
        ),
        (
            "mixed call_notes and paid billing update",
            "crm.update_contact",
            {
                "record_id": "crm-demo-001",
                "fields": {"call_notes": "Follow up", "billing_status": "paid"},
            },
        ),
        (
            "contact read",
            "crm.get_contact",
            {"record_id": "crm-demo-001"},
        ),
    ]

    report = []
    for label, tool_name, arguments in scenarios:
        decision = engine.evaluate(
            {
                "tool_call": {"name": tool_name, "arguments": arguments},
                "upstream": "crm",
                "runtime": {"lockdown": False},
                "intent": None,
                "cost_cache": {},
            }
        )
        report.append(
            {
                "scenario": label,
                "action": decision.action.value,
                "policy_id": decision.policy_id,
                "reason": decision.reason,
            }
        )

    print(json.dumps({
        "scope": "local synthetic policy-engine feasibility only",
        "results": report,
        "limits": [
            "arg_path_matches_regex reads a parsed dot-path leaf but stringifies its value.",
            "This proves the named scalar-string cases, not general typed field authorization.",
            "Current loader does not precompile or safety-validate arg_path_matches_regex patterns.",
            "The proxy integration test separately proves upstream suppression and audit behavior.",
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
