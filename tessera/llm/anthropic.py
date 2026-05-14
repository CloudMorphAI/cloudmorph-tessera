"""Anthropic Claude LLM provider for Tessera policy authoring.

Alpha. v0.2.0 ships Gemini as primary; other providers are functional but lightly tested.
"""

from __future__ import annotations

import json
import logging
import os

import anthropic
import yaml

from tessera.llm._shared import build_system_prompt
from tessera.llm.base import PolicyRecommendation

logger = logging.getLogger(__name__)


class AnthropicPolicyAuthor:
    """Alpha. Anthropic Claude policy author. v0.2.0 stub — production-tested only against Gemini."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-5-sonnet-20241022",
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._model = model
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
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_text}],
                )
                # Modern Claude responses interleave thinking/tool/text blocks;
                # grab the first text block rather than assuming content[0] is one.
                text = next(
                    (
                        block.text
                        for block in response.content
                        if isinstance(block, anthropic.types.TextBlock)
                    ),
                    None,
                )
                if text is None:
                    raise ValueError("Anthropic response contained no text block")
                return self._parse_and_validate_response(text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=anthropic_propose_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=anthropic_propose_exhausted max_retries=%d last_error=%s",
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
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                # Modern Claude responses interleave thinking/tool/text blocks;
                # grab the first text block rather than assuming content[0] is one.
                text = next(
                    (
                        block.text
                        for block in response.content
                        if isinstance(block, anthropic.types.TextBlock)
                    ),
                    None,
                )
                if text is None:
                    raise ValueError("Anthropic response contained no text block")
                return self._parse_and_validate_response(text)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "event=anthropic_analyze_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

        logger.warning(
            "event=anthropic_analyze_exhausted max_retries=%d last_error=%s",
            max_retries,
            last_error,
        )
        return []
