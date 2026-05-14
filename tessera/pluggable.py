"""Pluggable Protocol resolver via importlib."""

from __future__ import annotations

import importlib
from typing import Any

from tessera.errors import ConfigError


def resolve(env_value: str, default: str) -> type[Any]:
    """Resolve 'module.path:ClassName' string to a class.

    Returns the CLASS (not an instance). Caller instantiates with config.
    Format: "tessera.audit.sinks.sqlite:SqliteSink"
    Raises ConfigError on import failure.
    """
    spec = env_value or default
    try:
        module_path, class_name = spec.rsplit(":", 1)
    except ValueError:
        raise ConfigError(f"pluggable spec must be 'module:Class', got {spec!r}") from None
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ConfigError(f"cannot import module {module_path!r}: {e}") from e
    try:
        cls = getattr(module, class_name)
    except AttributeError:
        raise ConfigError(f"no attribute {class_name!r} in module {module_path!r}") from None
    if not isinstance(cls, type):
        raise ConfigError(
            f"pluggable spec {spec!r} resolved to non-class {type(cls).__name__}"
        )
    return cls
