"""Unit tests for tessera.observability.tracing."""

from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import MagicMock, patch


def _reload_tracing(otel_enabled: str = "") -> object:
    """Reload tracing module with a specific TESSERA_OTEL_ENABLED value."""
    for key in list(sys.modules):
        if "tessera.observability" in key:
            del sys.modules[key]
    with patch.dict("os.environ", {"TESSERA_OTEL_ENABLED": otel_enabled}, clear=False):
        mod = importlib.import_module("tessera.observability.tracing")
    return mod


class TestIsEnabled:
    def test_disabled_by_default(self) -> None:
        mod = _reload_tracing("")
        assert mod.is_enabled() is False  # type: ignore[attr-defined]

    def test_enabled_with_1(self) -> None:
        mod = _reload_tracing("1")
        assert mod.is_enabled() is True  # type: ignore[attr-defined]

    def test_enabled_with_true(self) -> None:
        mod = _reload_tracing("true")
        assert mod.is_enabled() is True  # type: ignore[attr-defined]

    def test_enabled_with_yes(self) -> None:
        mod = _reload_tracing("yes")
        assert mod.is_enabled() is True  # type: ignore[attr-defined]

    def test_disabled_with_0(self) -> None:
        mod = _reload_tracing("0")
        assert mod.is_enabled() is False  # type: ignore[attr-defined]


class TestTraceDecoratorDisabled:
    """When disabled, @trace is a pure no-op passthrough."""

    def test_sync_function_runs(self) -> None:
        mod = _reload_tracing("")

        @mod.trace("test.span")  # type: ignore[attr-defined]
        def my_fn(x: int) -> int:
            return x * 2

        assert my_fn(5) == 10

    def test_async_function_runs(self) -> None:
        mod = _reload_tracing("")

        @mod.trace("test.span")  # type: ignore[attr-defined]
        async def my_async_fn(x: int) -> int:
            return x + 1

        result = asyncio.get_event_loop().run_until_complete(my_async_fn(3))
        assert result == 4

    def test_sync_funcname_preserved(self) -> None:
        mod = _reload_tracing("")

        @mod.trace("test.span")  # type: ignore[attr-defined]
        def named_fn() -> None:
            pass

        assert named_fn.__name__ == "named_fn"

    def test_async_funcname_preserved(self) -> None:
        mod = _reload_tracing("")

        @mod.trace("test.span")  # type: ignore[attr-defined]
        async def async_named_fn() -> None:
            pass

        assert async_named_fn.__name__ == "async_named_fn"


class TestTraceDecoratorEnabled:
    """When _tracer is mocked in, spans are opened."""

    def test_sync_span_created(self) -> None:
        mod = _reload_tracing("1")
        # Inject a mock tracer
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        mod._tracer = mock_tracer  # type: ignore[attr-defined]

        @mod.trace("myspan")  # type: ignore[attr-defined]
        def do_work() -> str:
            return "done"

        result = do_work()
        assert result == "done"
        mock_tracer.start_as_current_span.assert_called_once_with("myspan")

    def test_async_span_created(self) -> None:
        mod = _reload_tracing("1")
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        mod._tracer = mock_tracer  # type: ignore[attr-defined]

        @mod.trace("async_span")  # type: ignore[attr-defined]
        async def do_async() -> str:
            return "async_done"

        result = asyncio.get_event_loop().run_until_complete(do_async())
        assert result == "async_done"
        mock_tracer.start_as_current_span.assert_called_once_with("async_span")
