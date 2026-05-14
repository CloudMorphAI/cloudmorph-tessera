"""Bundled reference policies — accessible at runtime via importlib.resources.

This package ships 12 example policies in the wheel:
- 7 generic: cost-cap, data-residency-eu, pii-block, prod-protection,
  read-only-mode, secret-leak-block, write-action-approval
- 5 AWS-illustrative: aws-{ec2-cost-cap,iam-blast-radius,region-allowlist,
  cost-runaway-stop,bedrock-cost-ceiling}-EXAMPLE

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
