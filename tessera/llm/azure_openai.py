"""Azure OpenAI LLM provider for Tessera policy authoring.

Alpha. v0.2.0 ships Gemini as primary; other providers are functional but lightly tested.
"""

from __future__ import annotations

import json
import logging
import os

import yaml
from openai import AzureOpenAI

from tessera.llm._shared import build_system_prompt
from tessera.llm.base import PolicyRecommendation

logger = logging.getLogger(__name__)


class AzureOpenAIPolicyAuthor:
    """Alpha. Azure OpenAI policy author. v0.2.0 ships Gemini as primary; other providers are functional but lightly tested."""

    def __init__(
        self,
        api_key: str | None = None,
        azure_endpoint: str | None = None,
        api_version: str = "2024-02-01",
        deployment_name: str = "gpt-4o-mini",
    ) -> None:
        self._client = AzureOpenAI(
            api_key=api_key or os.environ.get("AZURE_OPENAI_API_KEY"),
            azure_endpoint=azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            api_version=api_version,
        )
        self._deployment = deployment_name
        self._system_prompt = build_system_prompt()

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
        condition_catalog: dict[str, object] | None = None,
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
                user_content = (
                    f"{base_prompt}\n\n"
                    f"Previous attempt produced invalid YAML: {last_error}. Fix and try again."
                )
            else:
                user_content = base_prompt

            try:
                response = self._client.chat.completions.create(
                    model=self._deployment,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                )
                text = response.choices[0].message.content or "[]"
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break
                    else:
                        parsed = []
                return self._parse_and_validate_response(json.dumps(parsed))
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=azure_openai_propose_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=azure_openai_propose_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []

    def analyze_tools(
        self,
        tools: list[dict[str, object]],
        upstream_name: str | None = None,
    ) -> list[PolicyRecommendation]:
        """Analyze an MCP tool catalog and recommend policies."""
        upstream_ctx = f" for upstream '{upstream_name}'" if upstream_name else ""
        tools_json = json.dumps(tools, indent=2)

        user_text = (
            f"Analyze the following MCP tool catalog{upstream_ctx} and recommend "
            f"Tessera firewall policies to enforce least-privilege access:\n\n"
            f"{tools_json}\n\n"
            "Return a JSON array of policy objects with fields: filename, reason, yaml_body."
        )

        last_error: str | None = None
        max_retries = 3
        for attempt in range(max_retries):
            if last_error is not None:
                prompt = user_text + f"\n\nPrevious attempt error: {last_error}. Fix and try again."
            else:
                prompt = user_text

            try:
                response = self._client.chat.completions.create(
                    model=self._deployment,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                )
                text = response.choices[0].message.content or "[]"
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break
                    else:
                        parsed = []
                return self._parse_and_validate_response(json.dumps(parsed))
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=azure_openai_analyze_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=azure_openai_analyze_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []
