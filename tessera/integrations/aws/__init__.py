"""AWS MCP upstream integration for Tessera.

Both AWSMcpUpstream and BlastRadiusBackend are lazily importable here.
AWSMcpUpstream requires the [aws] optional dep group (mcp_proxy_for_aws, boto3).
BlastRadiusBackend requires boto3 at runtime only.
"""

__all__ = ["AWSMcpUpstream", "BlastRadiusBackend"]


def __getattr__(name: str):
    if name == "AWSMcpUpstream":
        from tessera.integrations.aws.upstream import AWSMcpUpstream  # noqa: PLC0415
        return AWSMcpUpstream
    if name == "BlastRadiusBackend":
        from tessera.integrations.aws.blast_radius import BlastRadiusBackend  # noqa: PLC0415
        return BlastRadiusBackend
    raise AttributeError(f"module 'tessera.integrations.aws' has no attribute {name!r}")
