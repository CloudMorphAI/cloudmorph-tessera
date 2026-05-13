"""AWS Bedrock LLM provider for Tessera policy authoring.

Alpha. v0.2.0 ships Gemini as primary; other providers are functional but lightly tested.
"""

from __future__ import annotations

import json
import logging

import boto3
import yaml

from tessera.llm._shared import build_system_prompt
from tessera.llm.base import PolicyRecommendation

logger = logging.getLogger(__name__)


class BedrockPolicyAuthor:
    """Alpha. AWS Bedrock policy author. v0.2.0 ships Gemini as primary; other providers are functional but lightly tested."""

    def __init__(
        self,
        model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        region_name: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=region_name)
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._system_prompt = build_system_prompt()

    def _invoke(self, messages: list[dict]) -> str:
        """Call Bedrock invoke_model and return the response text."""
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._max_tokens,
            "system": self._system_prompt,
            "messages": messages,
        })
        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        response_body = json.loads(response["body"].read())
        return response_body["content"][0]["text"]

    def _parse_and_validate_response(self, text: str) -> list[PolicyRecommendation]:
        from tessera.policy.schema import Policy

        items = json.loads(text)
        results: list[PolicyRecommendation] = []
        for item in items:
            Policy.model_validate(yaml.safe_load(item["yaml_body"]))
            results.append(PolicyRecommendation(
                filename=item["filename"],
                reason=item["reason"],
                yaml_body=item["yaml_body"],
            ))
        return results

    def propose_policies(
        self,
        intent: str,
        condition_catalog: dict | None = None,
        max_retries: int = 3,
    ) -> list[PolicyRecommendation]:
        """Generate draft policies from a natural-language intent description."""
        catalog_note = ""
        if condition_catalog:
            catalog_note = f"\n\nAdditional condition catalog:\n{json.dumps(condition_catalog, indent=2)}"

        base_prompt = (
            f"Customer intent: {intent}{catalog_note}\n\n"
            "Return YAML policies as a JSON array of objects with fields: "
            "filename, reason, yaml_body."
        )

        last_error: str | None = None
        for attempt in range(max_retries):
            if last_error is not None:
                user_text = (
                    f"{base_prompt}\n\n"
                    f"Previous attempt produced invalid YAML: {last_error}. Fix and try again."
                )
            else:
                user_text = base_prompt

            try:
                text = self._invoke([{"role": "user", "content": user_text}])
                return self._parse_and_validate_response(text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=bedrock_propose_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=bedrock_propose_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []

    def analyze_tools(
        self,
        tools: list[dict],
        upstream_name: str | None = None,
    ) -> list[PolicyRecommendation]:
        """Analyze an MCP tool catalog and recommend policies."""
        upstream_ctx = f" for upstream '{upstream_name}'" if upstream_name else ""
        tools_json = json.dumps(tools, indent=2)

        user_text = (
            f"Analyze the following MCP tool catalog{upstream_ctx} and recommend "
            f"Tessera firewall policies to enforce least-privilege access:\n\n"
            f"{tools_json}\n\n"
            "Return a JSON array of objects with fields: filename, reason, yaml_body."
        )

        last_error: str | None = None
        max_retries = 3
        for attempt in range(max_retries):
            if last_error is not None:
                prompt = user_text + f"\n\nPrevious attempt error: {last_error}. Fix and try again."
            else:
                prompt = user_text

            try:
                text = self._invoke([{"role": "user", "content": prompt}])
                return self._parse_and_validate_response(text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=bedrock_analyze_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=bedrock_analyze_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []
