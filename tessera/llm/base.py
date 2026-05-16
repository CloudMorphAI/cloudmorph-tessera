"""LLM provider Protocols for Tessera policy authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class PolicyRecommendation:
    filename: str
    reason: str
    yaml_body: str


@runtime_checkable
class PolicyAuthor(Protocol):
    def propose_policies(
        self,
        intent: str,
        condition_catalog: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> list[PolicyRecommendation]:
        ...


@runtime_checkable
class ToolCatalogAnalyzer(Protocol):
    def analyze_tools(
        self,
        tools: list[dict[str, object]],
        upstream_name: str | None = None,
    ) -> list[PolicyRecommendation]:
        ...
