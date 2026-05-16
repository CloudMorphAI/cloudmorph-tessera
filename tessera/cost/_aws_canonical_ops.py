"""Canonical AWS operation registry — data-only, no deprecation.

Migrated out of the (now-removed) tessera.cost.aws_mapping in v0.4.0.
cli_translator and other internal consumers import from here.
"""
from __future__ import annotations

# The 10 builtin AWS ops the legacy aws_mapping.py covered.
# Canonical names — cli_translator uses these to auto-register generic handlers.
# Per-op pricing logic is gone; customers get pricing via tessera.cost.cost_for_call(),
# which routes via the price-table registry.
BUILTIN_AWS_OPS: list[str] = [
    "aws_ec2_RunInstances",
    "aws_s3_PutObject",
    "aws_s3_GetObject",
    "aws_rds_CreateDBInstance",
    "aws_lambda_InvokeFunction",
    "aws_bedrock_InvokeModel",
    "aws_eks_CreateCluster",
    "aws_ec2_CreateNatGateway",
    "aws_ebs_CreateVolume",
    "aws_cloudfront_CreateDistribution",
]
