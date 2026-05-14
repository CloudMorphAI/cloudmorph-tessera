"""AWS MCP upstream integration for Tessera.

Both AWSMcpUpstream and BlastRadiusBackend are lazily importable here.
AWSMcpUpstream requires the [aws] optional dep group (mcp_proxy_for_aws, boto3).
BlastRadiusBackend requires boto3 at runtime only.
"""

__all__ = ["AWSMcpUpstream", "BlastRadiusBackend"]


def __getattr__(name: str) -> object:
    if name == "AWSMcpUpstream":
        from tessera.integrations.aws.upstream import AWSMcpUpstream  # noqa: PLC0415
        return AWSMcpUpstream
    if name == "BlastRadiusBackend":
        from tessera.integrations.aws.blast_radius import BlastRadiusBackend  # noqa: PLC0415
        return BlastRadiusBackend
    # Fall through for submodule attribute access (e.g., `tessera.integrations.aws.upstream`
    # used by tests via unittest.mock.patch). importlib resolves this lazily — we
    # just need to import the named submodule and return it. AttributeError remains
    # the right signal if the name is neither a known class nor a real submodule.
    import importlib
    try:
        return importlib.import_module(f"tessera.integrations.aws.{name}")
    except ImportError as exc:
        raise AttributeError(f"module 'tessera.integrations.aws' has no attribute {name!r}") from exc
