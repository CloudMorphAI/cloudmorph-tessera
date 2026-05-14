"""Integration test for CoherePolicyAuthor.

Skips unless COHERE_API_KEY is set in the environment.
Run with: pytest tests/test_llm_cohere.py -v
"""

from __future__ import annotations

import os

import pytest
import yaml


@pytest.mark.integration
def test_cohere_propose_policies_basic() -> None:
    api_key = os.environ.get("COHERE_API_KEY")
    if not api_key:
        pytest.skip(reason="no API key")

    from tessera.llm.cohere import CoherePolicyAuthor

    author = CoherePolicyAuthor(api_key=api_key)
    results = author.propose_policies("block destructive ec2 calls outside business hours")

    assert isinstance(results, list), "propose_policies must return a list"
    assert len(results) > 0, "expected at least one policy recommendation"

    for rec in results:
        assert rec.filename, "filename must be non-empty"
        assert rec.reason, "reason must be non-empty"
        assert rec.yaml_body, "yaml_body must be non-empty"
        parsed = yaml.safe_load(rec.yaml_body)
        assert isinstance(parsed, dict), f"yaml_body must parse as a YAML mapping; got {type(parsed)}"
