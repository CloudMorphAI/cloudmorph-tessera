"""Unit tests for the CloudMorph Python SDK."""

import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk-python"))

from cloudmorph import CloudMorph, CloudMorphError, RateLimitError


class TestCloudMorphInit(unittest.TestCase):
    def test_requires_token(self):
        with self.assertRaises(ValueError):
            CloudMorph(token="")

    def test_default_base_url(self):
        cm = CloudMorph(token="cm_test")
        self.assertEqual(cm.base_url, "https://mcp.cloudmorph.io")

    def test_custom_base_url(self):
        cm = CloudMorph(token="cm_test", base_url="http://localhost:8080/")
        self.assertEqual(cm.base_url, "http://localhost:8080")


class TestIsTerminal(unittest.TestCase):
    def test_terminal_statuses(self):
        for status in ["completed", "failed", "cancelled", "canceled", "blocked"]:
            self.assertTrue(CloudMorph._is_terminal(status))

    def test_non_terminal_statuses(self):
        for status in ["running", "pending", "queued", ""]:
            self.assertFalse(CloudMorph._is_terminal(status))


class TestRequest(unittest.TestCase):
    @patch("cloudmorph.client.urllib.request.urlopen")
    def test_request_success(self, mock_urlopen):
        response_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": json.dumps({
                    "requestId": "req_123",
                    "decision": "allow",
                    "status": "completed",
                    "output": {"buckets": [], "count": 0},
                })}],
                "isError": False,
            },
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cm = CloudMorph(token="cm_test", base_url="http://localhost:8080")
        result = cm.request("aws.s3.list_buckets")

        self.assertEqual(result["requestId"], "req_123")
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["status"], "completed")

    @patch("cloudmorph.client.urllib.request.urlopen")
    def test_request_with_targets(self, mock_urlopen):
        response_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": json.dumps({
                    "requestId": "req_456",
                    "decision": "allow",
                    "status": "pending",
                })}],
            },
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cm = CloudMorph(token="cm_test", base_url="http://localhost:8080")
        result = cm.request(
            "aws.ec2.list_instances",
            targets=["acc_123"],
            payload={"region": "us-east-1"},
            wait=True,
        )

        self.assertEqual(result["requestId"], "req_456")


class TestErrorHandling(unittest.TestCase):
    @patch("cloudmorph.client.urllib.request.urlopen")
    def test_rpc_error(self, mock_urlopen):
        response_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "tool_not_found"},
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cm = CloudMorph(token="cm_test", base_url="http://localhost:8080")
        with self.assertRaises(CloudMorphError) as ctx:
            cm.request("nonexistent.action")

        self.assertIn("tool_not_found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
