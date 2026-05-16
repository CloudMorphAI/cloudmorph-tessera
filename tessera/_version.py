"""Version source-of-truth: reads from installed package metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cloudmorph-tessera")
except PackageNotFoundError:
    # Development mode (not pip-installed) — fall back to literal.
    __version__ = "0.4.0"  # KEEP IN SYNC with pyproject.toml; will be auto-bumped by scripts/bump_version.py
