"""Gemini LLM provider for Tessera policy authoring.

Primary production provider for v0.2.0. Uses google-genai SDK with structured
JSON output and schema-driven system prompts.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import yaml  # type: ignore[import-untyped]
from google import genai
from google.genai import types as genai_types

from tessera.llm._shared import build_system_prompt
from tessera.llm.base import PolicyRecommendation

logger = logging.getLogger(__name__)


class GeminiPolicyAuthor:
    """Gemini-backed policy author and tool catalog analyzer.

    Implements both PolicyAuthor and ToolCatalogAnalyzer protocols.
    Uses Gemini's JSON mode to return structured policy recommendations.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.0-flash-exp",
        temperature: float = 0.1,
        top_k: int = 20,
    ) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client = genai.Client(api_key=resolved_key)
        self._model = model
        self._temperature = temperature
        self._top_k = top_k
        self._system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Auto-generate system prompt from the Tessera policy schema."""
        return build_system_prompt()

    def _parse_and_validate_response(self, text: str) -> list[PolicyRecommendation]:
        """Parse JSON response and validate each yaml_body against the Policy schema."""
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
        condition_catalog: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> list[PolicyRecommendation]:
        """Generate draft policies from a natural-language intent description.

        Retries up to max_retries times when validation fails, feeding the
        validation error back into the prompt to guide correction.
        """
        catalog_note = ""
        if condition_catalog:
            catalog_note = f"\n\nAdditional condition catalog:\n{json.dumps(condition_catalog, indent=2)}"

        user_prompt = (
            f"Customer intent: {intent}{catalog_note}\n\n"
            "Return YAML policies as a JSON array of objects with fields: "
            "filename, reason, yaml_body."
        )

        last_error: str | None = None
        for attempt in range(max_retries):
            if last_error is not None:
                prompt_with_error = (
                    f"{user_prompt}\n\n"
                    f"Previous attempt produced invalid YAML: {last_error}. Fix and try again."
                )
            else:
                prompt_with_error = user_prompt

            contents = [
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=prompt_with_error)],
                )
            ]

            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=self._system_prompt,
                        temperature=self._temperature,
                        top_k=self._top_k,
                        response_mime_type="application/json",
                    ),
                )
                if response.text is None:
                    raise ValueError("Gemini returned empty response")
                return self._parse_and_validate_response(response.text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=gemini_propose_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=gemini_propose_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []

    def analyze_tools(
        self,
        tools: list[dict[str, Any]],
        upstream_name: str | None = None,
    ) -> list[PolicyRecommendation]:
        """Analyze an MCP server's tool catalog and recommend policies.

        tools: the list of tool objects from tools/list response.
        upstream_name: optional name of the upstream for context.
        """
        upstream_ctx = f" for upstream '{upstream_name}'" if upstream_name else ""
        tools_json = json.dumps(tools, indent=2)

        user_prompt = (
            f"Analyze the following MCP tool catalog{upstream_ctx} and recommend "
            f"Tessera firewall policies to enforce least-privilege access:\n\n"
            f"{tools_json}\n\n"
            "Return a JSON array of objects with fields: filename, reason, yaml_body."
        )

        contents = [
            genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=user_prompt)],
            )
        ]

        last_error: str | None = None
        max_retries = 3
        for attempt in range(max_retries):
            if last_error is not None:
                contents = [
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part(
                            text=user_prompt + f"\n\nPrevious attempt error: {last_error}. Fix and try again."
                        )],
                    )
                ]

            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=self._system_prompt,
                        temperature=self._temperature,
                        top_k=self._top_k,
                        response_mime_type="application/json",
                    ),
                )
                if response.text is None:
                    raise ValueError("Gemini returned empty response")
                return self._parse_and_validate_response(response.text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=gemini_analyze_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=gemini_analyze_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []
