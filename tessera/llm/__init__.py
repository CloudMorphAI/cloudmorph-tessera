"""Tessera LLM provider abstractions — policy authoring and tool catalog analysis."""

from __future__ import annotations

from tessera.llm.base import PolicyAuthor, PolicyRecommendation, ToolCatalogAnalyzer

# Eagerly load provider submodules so they're registered as attributes of `tessera.llm`,
# which is required for `unittest.mock.patch("tessera.llm.<provider>.<sdk>...")` to find
# the patch target. Each import is wrapped because the SDK is an optional dep — if the
# SDK isn't installed, the submodule import fails but `tessera.llm` itself still works
# (callers of the failed provider will see the ImportError at instantiation time).
try:
    from tessera.llm import anthropic as anthropic  # noqa: F401
except ImportError:
    pass
try:
    from tessera.llm import openai as openai  # noqa: F401
except ImportError:
    pass
try:
    from tessera.llm import bedrock as bedrock  # noqa: F401
except ImportError:
    pass
try:
    from tessera.llm import azure_openai as azure_openai  # noqa: F401
except ImportError:
    pass
try:
    from tessera.llm import gemini as gemini  # noqa: F401
except ImportError:
    pass
try:
    from tessera.llm import mistral as mistral  # noqa: F401
except ImportError:
    pass
try:
    from tessera.llm import cohere as cohere  # noqa: F401
except ImportError:
    pass

__all__ = [
    "PolicyAuthor",
    "ToolCatalogAnalyzer",
    "PolicyRecommendation",
]
