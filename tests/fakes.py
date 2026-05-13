"""Fake/stub implementations for pluggable backend tests."""

from __future__ import annotations

from typing import Any


class FakePolicyLoader:
    """Stub PolicyLoader that always returns an empty policy list.

    Used by test_pluggable.py to verify that TESSERA_POLICY_LOADER env var
    causes _lifespan to instantiate the loader via pluggable.resolve().
    """

    def __init__(self, policy_dir: str, reload_mode: str = "none") -> None:
        self.policy_dir = policy_dir
        self.reload_mode = reload_mode
        self._loaded = True

    def load_all(self, scope: str) -> list[Any]:
        return []

    def watch(self, scope: str, callback: Any) -> None:
        pass

    def stop(self) -> None:
        pass

    def state(self) -> dict[str, Any]:
        return {"loaded": 0, "errored": []}
