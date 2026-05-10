"""Tests for StdoutSink."""

from __future__ import annotations

import io
import json

import pytest

from tessera.audit.sinks.stdout import StdoutSink


def test_emit_writes_json_line() -> None:
    stream = io.StringIO()
    sink = StdoutSink(stream=stream)
    event = {"action": "login", "user": "alice", "status": "ok"}
    sink.emit(event)
    output = stream.getvalue().strip()
    parsed = json.loads(output)
    assert parsed["action"] == "login"
    assert parsed["user"] == "alice"
    assert parsed["status"] == "ok"


def test_emit_non_ascii_preserved() -> None:
    stream = io.StringIO()
    sink = StdoutSink(stream=stream)
    event = {"message": "héllo wörld — 日本語"}
    sink.emit(event)
    output = stream.getvalue().strip()
    # Non-ASCII characters must appear literally, not as \uXXXX escapes
    assert "héllo wörld" in output
    assert "日本語" in output


def test_close_is_noop() -> None:
    stream = io.StringIO()
    sink = StdoutSink(stream=stream)
    # Should not raise and stream should still be usable
    sink.close()
    sink.emit({"after": "close"})
    assert "after" in stream.getvalue()


def test_head_hash_returns_empty_string() -> None:
    sink = StdoutSink(stream=io.StringIO())
    assert sink.head_hash("any-scope") == ""


def test_iter_events_raises_not_implemented() -> None:
    sink = StdoutSink(stream=io.StringIO())
    with pytest.raises(NotImplementedError, match="stdout is write-only"):
        next(sink.iter_events())


def test_custom_stream() -> None:
    stream = io.StringIO()
    sink = StdoutSink(stream=stream)
    sink.emit({"key": "value"})
    output = stream.getvalue()
    assert output.endswith("\n")
    parsed = json.loads(output.strip())
    assert parsed["key"] == "value"


def test_name_attribute_is_stdout() -> None:
    sink = StdoutSink(stream=io.StringIO())
    assert sink.name == "stdout"
