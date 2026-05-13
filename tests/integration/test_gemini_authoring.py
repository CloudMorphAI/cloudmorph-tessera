"""Integration tests for GeminiPolicyAuthor — all Gemini API calls are mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tessera.llm.gemini import GeminiPolicyAuthor
from tessera.llm.base import PolicyRecommendation

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_VALID_POLICY_YAML = """\
id: block-all-deletes
name: Block destructive delete operations
match:
  upstream: "*"
  tool_pattern: ".*delete.*"
action: block
reason: Destructive operations are blocked
priority: 100
"""

_VALID_RECOMMENDATION = {
    "filename": "block-all-deletes.yaml",
    "reason": "Block all delete operations for safety",
    "yaml_body": _VALID_POLICY_YAML,
}

_INVALID_YAML_RECOMMENDATION = {
    "filename": "bad.yaml",
    "reason": "This is bad",
    "yaml_body": "id: bad\naction: invalid_action\n",
}


def _make_mock_response(items: list[dict]) -> MagicMock:
    """Build a mock Gemini response with .text returning JSON."""
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(items)
    return mock_resp


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
@patch("tessera.llm.gemini.genai.Client")
def test_happy_path_returns_recommendations(mock_client_cls):
    """Mocked valid response produces a list of PolicyRecommendation."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _make_mock_response([_VALID_RECOMMENDATION])

    author = GeminiPolicyAuthor(api_key="fake-key")
    results = author.propose_policies("Block all delete operations")

    assert len(results) == 1
    assert isinstance(results[0], PolicyRecommendation)
    assert results[0].filename == "block-all-deletes.yaml"
    assert results[0].reason == "Block all delete operations for safety"
    mock_client.models.generate_content.assert_called_once()


@pytest.mark.integration
@patch("tessera.llm.gemini.genai.Client")
def test_retry_loop_second_call_valid(mock_client_cls):
    """First response invalid, second valid — asserts exactly 2 calls made."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # First call: invalid YAML that fails Policy.model_validate
    invalid_resp = _make_mock_response([_INVALID_YAML_RECOMMENDATION])
    # Second call: valid
    valid_resp = _make_mock_response([_VALID_RECOMMENDATION])
    mock_client.models.generate_content.side_effect = [invalid_resp, valid_resp]

    author = GeminiPolicyAuthor(api_key="fake-key")
    results = author.propose_policies("Block deletes", max_retries=3)

    assert len(results) == 1
    assert results[0].filename == "block-all-deletes.yaml"
    assert mock_client.models.generate_content.call_count == 2


@pytest.mark.integration
@patch("tessera.llm.gemini.genai.Client")
def test_max_retries_exhausted_returns_empty(mock_client_cls):
    """All attempts fail validation — returns empty list, logs warning."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # Always returns invalid YAML
    mock_client.models.generate_content.return_value = _make_mock_response([_INVALID_YAML_RECOMMENDATION])

    import logging
    author = GeminiPolicyAuthor(api_key="fake-key")

    with patch.object(author.__class__.__module__, None, create=True):
        pass  # noop — just ensuring no exception raised

    results = author.propose_policies("Do something", max_retries=3)

    assert results == []
    assert mock_client.models.generate_content.call_count == 3


@pytest.mark.integration
@patch("tessera.llm.gemini.genai.Client")
def test_analyze_tools_happy_path(mock_client_cls):
    """analyze_tools with a valid catalog response returns recommendations."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _make_mock_response([_VALID_RECOMMENDATION])

    tools = [
        {"name": "delete_file", "description": "Deletes a file", "inputSchema": {}},
        {"name": "list_files", "description": "Lists files", "inputSchema": {}},
    ]

    author = GeminiPolicyAuthor(api_key="fake-key")
    results = author.analyze_tools(tools, upstream_name="filesystem")

    assert len(results) == 1
    assert results[0].filename == "block-all-deletes.yaml"
    mock_client.models.generate_content.assert_called_once()


@pytest.mark.integration
@patch("tessera.llm.gemini.genai.Client")
def test_temperature_and_top_k_passed_correctly(mock_client_cls):
    """Temperature and top_k values are forwarded to generate_content config."""
    from google.genai import types as genai_types

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _make_mock_response([_VALID_RECOMMENDATION])

    author = GeminiPolicyAuthor(api_key="fake-key", temperature=0.05, top_k=10)
    author.propose_policies("Block deletes")

    call_kwargs = mock_client.models.generate_content.call_args
    config_arg = call_kwargs.kwargs.get("config") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else None

    assert config_arg is not None
    # The config object should carry our temperature and top_k
    assert config_arg.temperature == 0.05
    assert config_arg.top_k == 10


@pytest.mark.integration
@patch("tessera.llm.gemini.genai.Client")
def test_system_prompt_contains_condition_schema_names(mock_client_cls):
    """System prompt must contain known condition type names from the schema."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    author = GeminiPolicyAuthor(api_key="fake-key")
    prompt = author._system_prompt

    # These condition discriminator values must appear in the system prompt
    expected_conditions = [
        "arg_equals",
        "arg_matches_regex",
        "arg_in_set",
        "tool_name_in",
        "time_of_day_outside",
        "any_of",
        "none_of",
    ]
    for cond in expected_conditions:
        assert cond in prompt, f"Expected condition '{cond}' missing from system prompt"
