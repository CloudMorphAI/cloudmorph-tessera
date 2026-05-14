"""Happy-path tests for alpha LLM provider stubs.

Each test mocks the underlying SDK client and verifies that:
- The class instantiates without error
- propose_policies() returns a list (even if empty on parse failure)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

_VALID_POLICY_YAML = """\
id: test-block
name: Test block policy
match:
  upstream: "*"
action: block
reason: Test
"""

_VALID_ITEM = {
    "filename": "test-block.yaml",
    "reason": "Generated for test",
    "yaml_body": _VALID_POLICY_YAML,
}


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.integration
@patch("tessera.llm.anthropic.anthropic.Anthropic")
def test_anthropic_instantiates_and_proposes(mock_anthropic_cls):
    """AnthropicPolicyAuthor instantiates and propose_policies returns a list."""
    import anthropic as _anthropic_sdk

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    mock_msg = MagicMock()
    # Use spec=TextBlock so isinstance() in production code matches the mock.
    text_block = MagicMock(spec=_anthropic_sdk.types.TextBlock)
    text_block.text = json.dumps([_VALID_ITEM])
    mock_msg.content = [text_block]
    mock_client.messages.create.return_value = mock_msg

    from tessera.llm.anthropic import AnthropicPolicyAuthor
    author = AnthropicPolicyAuthor(api_key="fake-key")
    results = author.propose_policies("Block all deletes")

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].filename == "test-block.yaml"


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.integration
@patch("tessera.llm.openai.OpenAI")
def test_openai_instantiates_and_proposes(mock_openai_cls):
    """OpenAIPolicyAuthor instantiates and propose_policies returns a list."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps([_VALID_ITEM])
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_completion

    from tessera.llm.openai import OpenAIPolicyAuthor
    author = OpenAIPolicyAuthor(api_key="fake-key")
    results = author.propose_policies("Restrict access")

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].reason == "Generated for test"


# ---------------------------------------------------------------------------
# Bedrock
# ---------------------------------------------------------------------------


@pytest.mark.integration
@patch("tessera.llm.bedrock.boto3.client")
def test_bedrock_instantiates_and_proposes(mock_boto_client):
    """BedrockPolicyAuthor instantiates and propose_policies returns a list."""
    import io

    mock_client = MagicMock()
    mock_boto_client.return_value = mock_client

    response_text = json.dumps([_VALID_ITEM])
    body_bytes = json.dumps({
        "content": [{"text": response_text}],
    }).encode()

    mock_response = {
        "body": io.BytesIO(body_bytes),
    }
    mock_client.invoke_model.return_value = mock_response

    from tessera.llm.bedrock import BedrockPolicyAuthor
    author = BedrockPolicyAuthor()
    results = author.propose_policies("Limit S3 access")

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].filename == "test-block.yaml"


# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.integration
@patch("tessera.llm.azure_openai.AzureOpenAI")
def test_azure_openai_instantiates_and_proposes(mock_azure_cls):
    """AzureOpenAIPolicyAuthor instantiates and propose_policies returns a list."""
    mock_client = MagicMock()
    mock_azure_cls.return_value = mock_client

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps([_VALID_ITEM])
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_completion

    from tessera.llm.azure_openai import AzureOpenAIPolicyAuthor
    author = AzureOpenAIPolicyAuthor(api_key="fake-key", azure_endpoint="https://test.openai.azure.com")
    results = author.propose_policies("Block production deploys")

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].yaml_body == _VALID_POLICY_YAML
