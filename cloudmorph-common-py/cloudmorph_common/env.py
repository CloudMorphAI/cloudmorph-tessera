"""Env var helpers used by executor settings."""

from __future__ import annotations

import os

from cloudmorph_common.errors import ConfigError


def require_env(name: str) -> str:
    """Get a required env var; raise ConfigError if missing or empty."""
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required env var: {name}")
    return value


def float_env(name: str, default: float) -> float:
    """Read a float env var, falling back to default on missing/invalid."""
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to default on missing/invalid."""
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def bool_env(name: str, default: bool = False) -> bool:
    """Read a bool env var. Truthy: 1/true/yes/y. Falsy: 0/false/no/n. Else default."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return default


def list_env(name: str, default: list[str] | None = None, separator: str = ",") -> list[str]:
    """Read a separator-delimited env var as a list of trimmed non-empty strings."""
    value = os.getenv(name, "")
    if not value:
        return default if default is not None else []
    return [item.strip() for item in value.split(separator) if item.strip()]
