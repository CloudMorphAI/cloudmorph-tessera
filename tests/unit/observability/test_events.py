"""Unit tests for tessera.observability.events."""

from __future__ import annotations

import asyncio
import logging

import pytest

from tessera.observability import events


@pytest.fixture(autouse=True)
def reset_hooks() -> object:
    """Clear hook registry before and after every test."""
    events.clear_hooks()
    yield
    events.clear_hooks()


class TestRegisterAndClear:
    def test_register_on_decision(self) -> None:
        calls: list[object] = []

        async def hook(decision: object, context: dict) -> None:  # type: ignore[type-arg]
            calls.append(decision)

        events.register_on_decision(hook)
        asyncio.get_event_loop().run_until_complete(
            events.fire_on_decision("d1", {})
        )
        assert calls == ["d1"]

    def test_register_on_audit_emit(self) -> None:
        seen: list[dict] = []  # type: ignore[type-arg]

        async def hook(event: dict) -> None:  # type: ignore[type-arg]
            seen.append(event)

        events.register_on_audit_emit(hook)
        asyncio.get_event_loop().run_until_complete(
            events.fire_on_audit_emit({"eventId": "evt_abc"})
        )
        assert seen == [{"eventId": "evt_abc"}]

    def test_clear_hooks(self) -> None:
        async def hook(d: object, c: dict) -> None:  # type: ignore[type-arg]
            pass

        events.register_on_decision(hook)
        events.clear_hooks()
        calls: list[object] = []

        async def recording_hook(d: object, c: dict) -> None:  # type: ignore[type-arg]
            calls.append(d)

        # After clear, registering a new hook should still work
        events.register_on_decision(recording_hook)
        asyncio.get_event_loop().run_until_complete(
            events.fire_on_decision("x", {})
        )
        assert calls == ["x"]


class TestFireOnDecision:
    def test_multiple_hooks_all_called(self) -> None:
        results: list[int] = []

        async def h1(d: object, c: dict) -> None:  # type: ignore[type-arg]
            results.append(1)

        async def h2(d: object, c: dict) -> None:  # type: ignore[type-arg]
            results.append(2)

        events.register_on_decision(h1)
        events.register_on_decision(h2)
        asyncio.get_event_loop().run_until_complete(events.fire_on_decision("d", {}))
        assert results == [1, 2]

    def test_failing_hook_does_not_stop_others(self, caplog: pytest.LogCaptureFixture) -> None:
        results: list[int] = []

        async def bad_hook(d: object, c: dict) -> None:  # type: ignore[type-arg]
            raise RuntimeError("boom")

        async def good_hook(d: object, c: dict) -> None:  # type: ignore[type-arg]
            results.append(42)

        events.register_on_decision(bad_hook)
        events.register_on_decision(good_hook)

        with caplog.at_level(logging.WARNING, logger="tessera.observability.events"):
            asyncio.get_event_loop().run_until_complete(events.fire_on_decision("d", {}))

        # good_hook still ran
        assert results == [42]
        # warning was logged
        assert any("on_decision_hook_failed" in r.message for r in caplog.records)

    def test_failing_audit_hook_does_not_stop_others(self, caplog: pytest.LogCaptureFixture) -> None:
        results: list[str] = []

        async def bad(e: dict) -> None:  # type: ignore[type-arg]
            raise ValueError("bad audit")

        async def good(e: dict) -> None:  # type: ignore[type-arg]
            results.append(e.get("eventId", ""))

        events.register_on_audit_emit(bad)
        events.register_on_audit_emit(good)

        with caplog.at_level(logging.WARNING, logger="tessera.observability.events"):
            asyncio.get_event_loop().run_until_complete(
                events.fire_on_audit_emit({"eventId": "evt_test"})
            )

        assert results == ["evt_test"]
        assert any("on_audit_hook_failed" in r.message for r in caplog.records)
