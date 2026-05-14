"""Cohere LLM provider for Tessera policy authoring.

Alpha. Implements PolicyAuthor + ToolCatalogAnalyzer via the Cohere v2 REST API.
command-r-plus offers strong structured-output quality at a competitive cost-per-token
for enterprise-volume usage.
"""

from __future__ import annotations

import json
import logging
import os

import httpx
import yaml

from tessera.llm._shared import build_system_prompt
from tessera.llm.base import PolicyRecommendation

logger = logging.getLogger(__name__)

_COHERE_API_URL = "https://api.cohere.com/v2/chat"


class CoherePolicyAuthor:
    """Cohere policy author. Uses the Cohere v2 REST API with command-r-plus-08-2024."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "command-r-plus-08-2024",
    ) -> None:
        self._api_key = api_key or os.environ.get("COHERE_API_KEY") or ""
        self._model = model
        self._system_prompt = build_system_prompt()

    def _parse_and_validate_response(self, text: str) -> list[PolicyRecommendation]:
        from tessera.policy.schema import Policy

        parsed = json.loads(text)
        # Cohere may return a wrapped object — unwrap array if needed
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
            else:
                parsed = []

        results: list[PolicyRecommendation] = []
        for item in parsed:
            Policy.model_validate(yaml.safe_load(item["yaml_body"]))
            results.append(PolicyRecommendation(
                filename=item["filename"],
                reason=item["reason"],
                yaml_body=item["yaml_body"],
            ))
        return results

    def _call_api(self, messages: list[dict]) -> str:
        """Call the Cohere v2 chat endpoint and return the content string.

        Cohere v2 response shape: message.content[0].text
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
        }
        with httpx.Client(timeout=60) as client:
            response = client.post(_COHERE_API_URL, headers=headers, json=payload)
            response.raise_for_status()
        data = response.json()
        # Cohere v2: data["message"]["content"][0]["text"]
        try:
            content_blocks = data["message"]["content"]
            return content_blocks[0]["text"] if content_blocks else "[]"
        except (KeyError, IndexError):
            return "[]"

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
                user_content = (
                    f"{base_prompt}\n\n"
                    f"Previous attempt produced invalid YAML: {last_error}. Fix and try again."
                )
            else:
                user_content = base_prompt

            try:
                messages = [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": user_content},
                ]
                text = self._call_api(messages)
                return self._parse_and_validate_response(text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=cohere_propose_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=cohere_propose_exhausted max_retries=%d last_error=%s",
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
                messages = [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ]
                text = self._call_api(messages)
                return self._parse_and_validate_response(text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=cohere_analyze_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=cohere_analyze_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []
