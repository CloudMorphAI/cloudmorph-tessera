"""Bundled reference policies — accessible at runtime via importlib.resources.

This package ships the reference policies in the wheel, including the
firewall defaults used by `tessera demo`:
- secret-leak-block, github-mcp-protection, postgres-mcp-protection
- other generic guards (pii-block, prompt-injection-heuristic, read-only-mode,
  write-action-approval, cost-cap, and the rest of this directory)
- AWS illustrative denies and EXAMPLE cost files

The top-level `policies/` directory in the repo is the same content for
dev / test workflow convenience. Wheel consumers access via:

    from importlib.resources import files
    bundled_dir = files("tessera.policies_default")
    for yaml_path in bundled_dir.iterdir():
        if yaml_path.suffix == ".yaml":
            ...

`tessera init` copies these into the user's working directory.
`tessera policy lint --policy-dir <path>` works against any directory; if
the path resolves to the bundled location, that works too.
"""

from __future__ import annotations

from importlib.resources import files


def bundled_policy_dir() -> str:
    """Filesystem path string of the bundled policies directory.

    Backed by importlib.resources for compatibility with zipped wheels.
    """
    return str(files("tessera.policies_default"))
