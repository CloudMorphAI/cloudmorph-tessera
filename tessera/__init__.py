"""Tessera — the open-source MCP firewall for AI agents."""

from __future__ import annotations

# BEFORE TAGGING A NEW RELEASE — bump the version in ALL of these places:
#   1. This line (__version__ below)
#   2. pyproject.toml ([project] version)
#   3. README.md — search & replace all `tessera:<old>` → `tessera:<new>` AND `tessera <old>` → `tessera <new>`
#   4. docs/INSTALL.md — same search & replace pattern
#   5. CHANGELOG.md — add a new section
# A stale version in README/INSTALL renders on GitHub and PyPI project page —
# install commands customers copy-paste pull the wrong image tag.
__version__ = "0.2.0"
__all__ = ["__version__"]
