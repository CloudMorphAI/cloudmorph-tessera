"""Offline regression tests for the bounded post-call model runner."""
from __future__ import annotations

import copy
import os
import shutil
import unittest
from typing import Any

from examples.post_call_demo import model_runner

DEAL_ID = "DEAL-ACME-001"
CALL_ID = "CALL-ACME-001"
REASON = "POLICY_BLOCK: requested discount exceeds the synthetic 10% policy cap."
ARGUMENTS = [
    {"call_id": CALL_ID},
    {"deal_id": DEAL_ID},
    {"deal_id": DEAL_ID, "fields": {"notes": "Call requested an engineering evaluation.", "next_step": "Schedule engineering evaluation."}},
    {"task_kind": "engineering_follow_up", "deal_id": DEAL_ID, "title": "Engineering evaluation", "details": "Review the requested technical evaluation.", "idempotency_key": "run-task-1"},
    {"draft_kind": "follow_up", "deal_id": DEAL_ID, "title": "Evaluation follow-up", "body": "We will follow up after the engineering evaluation.", "idempotency_key": "run-draft-1"},
    {"deal_id": DEAL_ID, "fields": {"discount_pct": 30}},
    {"task_kind": "internal_discount_review", "deal_id": DEAL_ID, "title": "Review requested discount", "details": f"30% request is pending review. Policy block: {REASON}", "idempotency_key": "run-task-2", "requested_discount_pct": 30},
    {"deal_id": DEAL_ID},
]


def _catalog() -> list[dict[str, Any]]:
    string = {"type": "string"}
    deal_id = {"type": "string", "enum": [DEAL_ID]}
    schemas = {
        "meetings.get_call": {
            "type": "object", "additionalProperties": False, "required": ["call_id"],
            "properties": {"call_id": {"type": "string", "enum": [CALL_ID]}},
        },
        "crm.get_deal": {
            "type": "object", "additionalProperties": False, "required": ["deal_id"],
            "properties": {"deal_id": deal_id},
        },
        "crm.update_deal": {
            "type": "object", "additionalProperties": False, "required": ["deal_id", "fields"],
            "properties": {
                "deal_id": deal_id,
                "fields": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "notes": string, "next_step": string,
                        "discount_pct": {"type": "number", "minimum": 0, "maximum": 100},
                    },
                },
            },
        },
        "tasks.create": {
            "type": "object", "additionalProperties": False,
            "required": ["task_kind", "deal_id", "title", "details", "idempotency_key"],
            "properties": {
                "task_kind": {"type": "string", "enum": ["engineering_follow_up", "internal_discount_review"]},
                "deal_id": deal_id, "title": string, "details": string, "idempotency_key": string,
                "requested_discount_pct": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "allOf": [{"if": {"properties": {"task_kind": {"const": "internal_discount_review"}}},
                       "then": {"required": ["requested_discount_pct"]}}],
        },
        "drafts.create": {
            "type": "object", "additionalProperties": False,
            "required": ["draft_kind", "deal_id", "title", "body", "idempotency_key"],
            "properties": {
                "draft_kind": {"type": "string", "enum": ["follow_up", "proposal"]},
                "deal_id": deal_id, "title": string, "body": string, "idempotency_key": string,
                "commercial_terms": {"type": "object", "additionalProperties": False},
            },
            "allOf": [{"if": {"properties": {"draft_kind": {"const": "proposal"}}},
                       "then": {"required": ["commercial_terms"]}}],
        },
    }
    return [
        {
            "name": name,
            "description": f"Synthetic {name} fixture tool.",
            "inputSchema": schemas[name],
        }
        for name in sorted({step["tool"] for step in model_runner.STEPS})
    ]


class FakeGateway:
    def __init__(self, *, block_discount: bool = True) -> None:
        self.run_id = "run-postcall-test"
        self.catalog = _catalog()
        self.events: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any], str, str]] = []
        self.block_discount = block_discount
        self.deal = {
            "deal_id": DEAL_ID,
            "label": "SYNTHETIC LOCAL FIXTURE - NO VENDOR CONNECTION",
            "discount_pct": 0,
            "notes": "",
            "next_step": "",
        }

    def invoke(self, tool_name: str, arguments: dict[str, Any], tool_use_id: str, *, request_id: str) -> dict[str, Any]:
        self.calls.append((tool_name, copy.deepcopy(arguments), tool_use_id, request_id))
        index = len(self.calls) - 1
        decision = "allow"
        reason = "Synthetic fixture action allowed."
        contacts = 1
        if index == 0:
            result = {"call_id": CALL_ID, "summary": "Engineering evaluation requested; 30% discount asked about."}
        elif index == 1:
            result = copy.deepcopy(self.deal)
        elif index == 2:
            self.deal.update(arguments["fields"])
            result = {"deal": copy.deepcopy(self.deal), "changed_fields": ["notes", "next_step"]}
        elif index == 3:
            result = {"task_kind": "engineering_follow_up", "status": "open"}
        elif index == 4:
            result = {"draft_kind": "follow_up", "delivery_status": "not_sent"}
        elif index == 5 and self.block_discount:
            decision = "block"
            reason = "Policy receipt pending audit reconciliation"
            contacts = 0
            result = None
        elif index == 5:
            self.deal.update(arguments["fields"])
            result = {"deal": copy.deepcopy(self.deal), "changed_fields": ["discount_pct"]}
        elif index == 6:
            result = {"task_kind": "internal_discount_review", "requested_discount_pct": 30, "approval_status": "pending"}
        else:
            result = copy.deepcopy(self.deal)
        event = {
            "run_id": self.run_id,
            "request_id": request_id,
            "tool_use_id": tool_use_id,
            "tool": tool_name,
            "decision": decision,
            "reason": reason,
            "audit_id": f"audit-{index + 1}",
            "gate_result": {"content": [{"type": "text", "text": REASON if decision == "block" else reason}]},
            "result": result,
            "adapter_contacts": contacts,
        }
        self.events.append(event)
        return event


class OfflineModel:
    def __init__(
        self,
        *,
        duplicate_tool_use_id: bool = False,
        empty_task_key: bool = False,
        unexpected_commercial_terms: bool = False,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.duplicate_tool_use_id = duplicate_tool_use_id
        self.empty_task_key = empty_task_key
        self.unexpected_commercial_terms = unexpected_commercial_terms

    def __call__(self, **request: Any) -> dict[str, Any]:
        index = len(self.requests)
        self.requests.append(copy.deepcopy(request))
        tool_config = request["toolConfig"]
        tool_spec = tool_config["tools"][0]["toolSpec"]
        self.assert_tool_choice(tool_config, tool_spec["name"])
        arguments = copy.deepcopy(ARGUMENTS[index])
        if self.empty_task_key and index == 3:
            arguments["idempotency_key"] = "  "
        if self.unexpected_commercial_terms and index == 4:
            arguments["commercial_terms"] = {}
        if index == 6:
            prior_results = [
                block["toolResult"]["content"][0]["json"]
                for message in request["messages"]
                for block in message.get("content", [])
                if isinstance(block, dict) and "toolResult" in block
            ]
            if (
                prior_results[-1]["decision"] != "block"
                or not prior_results[-1]["gate_result"]["content"][0]["text"].startswith("POLICY_BLOCK")
                or not isinstance(prior_results[-1].get("audit_id"), str)
                or not prior_results[-1]["audit_id"]
            ):
                raise AssertionError("The separate review turn did not receive the actual policy-block result.")
            actual_block_text = prior_results[-1]["gate_result"]["content"][0]["text"]
            arguments["details"] = f"30% request is pending review. Policy block: {actual_block_text}"
        tool_use_id = "use-1" if self.duplicate_tool_use_id and index == 1 else f"use-{index + 1}"
        return {
            "stopReason": "tool_use",
            "usage": {"inputTokens": 40 + index, "outputTokens": 15},
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": tool_use_id,
                                "name": tool_spec["name"],
                                "input": arguments,
                            }
                        }
                    ],
                }
            },
        }

    @staticmethod
    def assert_tool_choice(tool_config: dict[str, Any], name: str) -> None:
        if len(tool_config["tools"]) != 1 or tool_config["toolChoice"] != {"tool": {"name": name}}:
            raise AssertionError("Each turn must force exactly its one selected tool.")


class ModelRunnerTests(unittest.TestCase):
    def test_full_eight_turn_flow_uses_actual_gateway_block_and_readback(self) -> None:
        gateway = FakeGateway()
        model = OfflineModel()

        result = model_runner.run_model(gateway, converse_fn=model)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["mode"], "injected_converse_fn")
        self.assertIsNone(result["provider"])
        self.assertIsNone(result["model_id"])
        self.assertEqual(result["usage"]["attempted_calls"], 8)
        self.assertTrue(result["usage"]["usage_complete"])
        self.assertEqual(len(gateway.calls), 8)
        self.assertEqual([call[0] for call in gateway.calls], [step["tool"] for step in model_runner.STEPS])
        self.assertEqual([event["decision"] for event in gateway.events], ["allow"] * 5 + ["block", "allow", "allow"])
        self.assertEqual(gateway.events[5]["adapter_contacts"], 0)
        self.assertIn(REASON, gateway.calls[6][1]["details"])
        self.assertEqual(gateway.events[7]["result"]["discount_pct"], 0)
        self.assertEqual(len({call[2] for call in gateway.calls}), 8)
        self.assertEqual(len({call[3] for call in gateway.calls}), 8)
        self.assertEqual(gateway.calls[6][3], f"{gateway.run_id}-review")
        self.assertTrue(all(request["inferenceConfig"]["maxTokens"] == 256 for request in model.requests))
        self.assertTrue(all(len(request["toolConfig"]["tools"]) == 1 for request in model.requests))
        request_schemas = [request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"] for request in model.requests]
        notes_fields = request_schemas[2]["properties"]["fields"]
        self.assertEqual(notes_fields["required"], ["notes", "next_step"])
        self.assertEqual(set(notes_fields["properties"]), {"notes", "next_step"})
        engineering_schema = request_schemas[3]
        self.assertEqual(engineering_schema["properties"]["task_kind"]["enum"], ["engineering_follow_up"])
        self.assertNotIn("requested_discount_pct", engineering_schema["properties"])
        draft_schema = request_schemas[4]
        self.assertEqual(draft_schema["properties"]["draft_kind"]["enum"], ["follow_up"])
        self.assertNotIn("commercial_terms", draft_schema["properties"])
        discount_field = request_schemas[5]["properties"]["fields"]["properties"]["discount_pct"]
        self.assertEqual(discount_field, {"type": "integer", "enum": [30]})
        review_schema = request_schemas[6]
        self.assertEqual(review_schema["properties"]["task_kind"]["enum"], ["internal_discount_review"])
        self.assertEqual(review_schema["properties"]["requested_discount_pct"], {"type": "integer", "enum": [30]})

    def test_no_review_or_readback_if_exact_discount_is_not_blocked(self) -> None:
        gateway = FakeGateway(block_discount=False)
        model = OfflineModel()

        with self.assertRaises(model_runner.ModelRunError) as raised:
            model_runner.run_model(gateway, converse_fn=model)

        self.assertIn("not blocked", str(raised.exception))
        self.assertEqual(len(gateway.calls), 6)
        self.assertEqual(len(model.requests), 6)
        self.assertNotIn("internal_discount_review", [call[1].get("task_kind") for call in gateway.calls])

    def test_duplicate_model_tool_use_id_stops_before_second_gateway_call(self) -> None:
        gateway = FakeGateway()
        model = OfflineModel(duplicate_tool_use_id=True)

        with self.assertRaisesRegex(model_runner.ModelRunError, "reused a tool-use ID"):
            model_runner.run_model(gateway, converse_fn=model)

        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(len(model.requests), 2)

    def test_empty_idempotency_key_stops_before_task_adapter_call(self) -> None:
        gateway = FakeGateway()
        model = OfflineModel(empty_task_key=True)

        with self.assertRaisesRegex(model_runner.ModelRunError, "idempotency keys"):
            model_runner.run_model(gateway, converse_fn=model)

        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual(len(model.requests), 4)

    def test_unauthorized_commercial_terms_are_rejected_without_adapter_call(self) -> None:
        gateway = FakeGateway()
        model = OfflineModel(unexpected_commercial_terms=True)

        with self.assertRaisesRegex(model_runner.ModelRunError, "plain, unsent follow_up draft"):
            model_runner.run_model(gateway, converse_fn=model)

        offered_schema = model.requests[4]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
        self.assertNotIn("commercial_terms", offered_schema["properties"])
        self.assertEqual(offered_schema["properties"]["draft_kind"]["enum"], ["follow_up"])
        self.assertEqual(len(gateway.calls), 4)
        self.assertEqual(len(model.requests), 5)

    def test_default_live_transport_requires_startup_opt_in(self) -> None:
        gateway = FakeGateway()

        with self.assertRaisesRegex(model_runner.ModelRunError, "startup opt-in"):
            model_runner.run_model(gateway)

        self.assertEqual(gateway.calls, [])

    def test_actual_policy_gateway_eight_turn_requests_stay_within_byte_limit(self) -> None:
        if os.name != "posix" or shutil.which("node") is None:
            self.skipTest("The actual fixture bridge integration test requires Linux Node in WSL.")
        from examples.post_call_demo.policy_integration import PolicyGateway

        model = OfflineModel()
        with PolicyGateway() as gateway:
            result = model_runner.run_model(gateway, converse_fn=model)
            receipt = gateway.finish()

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["usage"]["attempted_calls"], 8)
        self.assertEqual(len(receipt["events"]), 8)
        self.assertTrue(receipt["audit"]["chain_verified"])
        self.assertLessEqual(
            max(call["request_json_bytes"] for call in result["provider_calls"]),
            model_runner.MAX_REQUEST_JSON_BYTES,
        )


if __name__ == "__main__":
    unittest.main()
