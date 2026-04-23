"""Tests for ControlCenterClient — wire protocol, error handling."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from cloudmorph_common.client import ControlCenterClient, ControlCenterError


class TestInit:
    def test_strips_trailing_slash(self):
        c = ControlCenterClient("https://api.example.com/", "tok_test")
        assert c.base_url == "https://api.example.com"

    def test_default_timeout(self):
        c = ControlCenterClient("https://api.example.com", "tok_test")
        assert c.timeout_seconds == 30


class TestClaimJob:
    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_claim_returns_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"jobId": "job_x", "jobToken": "tok_y"}).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = ControlCenterClient("https://api.example.com", "install_tok")
        result = c.claim_job("tnt_a", "acc_b", ["agent.run"])
        assert result == {"jobId": "job_x", "jobToken": "tok_y"}

    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_claim_204_returns_none(self, mock_urlopen):
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.example.com/controlcenter/executor/claim",
            204,
            "no content",
            {},
            io.BytesIO(b""),
        )
        c = ControlCenterClient("https://api.example.com", "install_tok")
        assert c.claim_job("tnt_a", "acc_b", ["agent.run"]) is None

    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_claim_409_returns_none(self, mock_urlopen):
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.example.com/controlcenter/executor/claim",
            409,
            "conflict",
            {},
            io.BytesIO(b""),
        )
        c = ControlCenterClient("https://api.example.com", "install_tok")
        assert c.claim_job("tnt_a", "acc_b", ["agent.run"]) is None

    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_claim_500_raises(self, mock_urlopen):
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.example.com/controlcenter/executor/claim",
            500,
            "internal",
            {},
            io.BytesIO(b'{"error": "server"}'),
        )
        c = ControlCenterClient("https://api.example.com", "install_tok")
        with pytest.raises(ControlCenterError) as exc:
            c.claim_job("tnt_a", "acc_b", ["agent.run"])
        assert exc.value.status == 500
        assert exc.value.payload == {"error": "server"}


class TestPostStatus:
    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_post_status_with_logs_and_reason(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = ControlCenterClient("https://api.example.com", "install_tok")
        result = c.post_status("job_x", "tok_y", "running", logs="started", reason="manual")
        assert result == {"ok": True}


class TestPostComplete:
    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_post_complete_with_artifacts(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        c = ControlCenterClient("https://api.example.com", "install_tok")
        result = c.post_complete(
            "job_x",
            "tok_y",
            "completed",
            artifacts=[{"kind": "summary", "uri": "s3://bucket/key"}],
            logs="done",
            summary="OK",
            result={"count": 5},
        )
        assert result == {"ok": True}


class TestNetworkErrors:
    @patch("cloudmorph_common.client.urllib.request.urlopen")
    def test_url_error_becomes_status_zero(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("connection refused")
        c = ControlCenterClient("https://api.example.com", "install_tok")
        with pytest.raises(ControlCenterError) as exc:
            c.fetch_job("job_x", "tok_y")
        assert exc.value.status == 0
        assert "connection refused" in exc.value.payload.get("message", "")
