"""AWS tool → Infracost query mapping shim.

Built-in OSS mappings cover 10 high-value operations.
Extended/premium mappings can be loaded at runtime from a YAML cache directory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

logger = logging.getLogger(__name__)


@dataclass
class InfracostQuery:
    """Parameters for a single Infracost SKU query."""

    service: str
    region: str
    attributes: dict[str, str]
    confidence_band: Literal["high", "medium", "ceiling"] = "high"
    args_used: list[str] = field(default_factory=list)


# Extended mappings populated at runtime via load_extended_mappings().
# Keys are tool_name strings; values are callables (tool_name, args) -> InfracostQuery | None.
_extended_mappings: dict[str, Callable[[str, dict[str, Any]], InfracostQuery | None]] = {}


def _get_region(args: dict[str, Any], default: str = "us-east-1") -> str:
    return str(args.get("region") or args.get("Region") or default)


def _map_ec2_run_instances(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    attrs: dict[str, str] = {
        "instanceType": str(args.get("InstanceType", args.get("instanceType", "t3.micro"))),
        "tenancy": str(args.get("Placement", {}).get("Tenancy", "Shared") if isinstance(args.get("Placement"), dict) else "Shared"),
        "operatingSystem": "Linux",
        "preInstalledSw": "NA",
        "licenseModel": "No License required",
        "capacitystatus": "Used",
    }
    return InfracostQuery(
        service="Compute Instance",
        region=region,
        attributes=attrs,
        confidence_band="high",
        args_used=["InstanceType", "region", "Placement"],
    )


def _map_s3_put_object(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    storage_class = str(args.get("StorageClass", args.get("storageClass", "STANDARD")))
    attrs: dict[str, str] = {
        "storageClass": storage_class,
        "volumeType": "Standard",
    }
    return InfracostQuery(
        service="AWS S3",
        region=region,
        attributes=attrs,
        confidence_band="medium",
        args_used=["StorageClass", "region"],
    )


def _map_s3_get_object(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    attrs: dict[str, str] = {"requestType": "Tier1"}
    return InfracostQuery(
        service="AWS S3",
        region=region,
        attributes=attrs,
        confidence_band="medium",
        args_used=["region"],
    )


def _map_rds_create_db_instance(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    db_class = str(args.get("DBInstanceClass", args.get("dbInstanceClass", "db.t3.micro")))
    engine = str(args.get("Engine", args.get("engine", "mysql")))
    multi_az = args.get("MultiAZ", args.get("multiAZ", False))
    attrs: dict[str, str] = {
        "instanceType": db_class,
        "databaseEngine": engine,
        "multiAZ": "Yes" if multi_az else "No",
        "deploymentOption": "Multi-AZ" if multi_az else "Single-AZ",
    }
    return InfracostQuery(
        service="Database Instance",
        region=region,
        attributes=attrs,
        confidence_band="high",
        args_used=["DBInstanceClass", "Engine", "MultiAZ", "region"],
    )


def _map_lambda_invoke(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    memory_size = str(args.get("MemorySize", args.get("memorySize", 128)))
    architecture = str(args.get("Architectures", ["x86_64"])[0] if isinstance(args.get("Architectures"), list) else args.get("architecture", "x86_64"))
    attrs: dict[str, str] = {
        "group": "AWS-Lambda-Duration",
        "memorySize": memory_size,
        "architecture": architecture,
    }
    return InfracostQuery(
        service="AWS Lambda",
        region=region,
        attributes=attrs,
        confidence_band="ceiling",
        args_used=["MemorySize", "Architectures", "region"],
    )


def _map_bedrock_invoke_model(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    model_id = str(args.get("modelId", args.get("ModelId", "anthropic.claude-3-sonnet-20240229-v1:0")))
    attrs: dict[str, str] = {
        "modelId": model_id,
        "type": "output",
    }
    return InfracostQuery(
        service="Amazon Bedrock",
        region=region,
        attributes=attrs,
        confidence_band="ceiling",
        args_used=["modelId", "region"],
    )


def _map_eks_create_cluster(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    attrs: dict[str, str] = {"clusterType": "EKS"}
    return InfracostQuery(
        service="Amazon Elastic Kubernetes Service",
        region=region,
        attributes=attrs,
        confidence_band="high",
        args_used=["region"],
    )


def _map_ec2_create_nat_gateway(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    attrs: dict[str, str] = {"group": "AmazonVPC-NatGateway-Hours"}
    return InfracostQuery(
        service="AmazonVPC",
        region=region,
        attributes=attrs,
        confidence_band="high",
        args_used=["region"],
    )


def _map_ebs_create_volume(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args)
    volume_type = str(args.get("VolumeType", args.get("volumeType", "gp3")))
    size = str(args.get("Size", args.get("size", 20)))
    iops = str(args.get("Iops", args.get("iops", 3000)))
    attrs: dict[str, str] = {
        "volumeApiName": volume_type,
        "storageMedia": "SSD-backed",
    }
    return InfracostQuery(
        service="Amazon Elastic Compute Cloud",
        region=region,
        attributes=attrs,
        confidence_band="high",
        args_used=["VolumeType", "Size", "Iops", "region"],
    )


def _map_cloudfront_create_distribution(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    region = _get_region(args, default="us-east-1")
    price_class = str(
        args.get("DistributionConfig", {}).get("PriceClass", "PriceClass_All")
        if isinstance(args.get("DistributionConfig"), dict)
        else "PriceClass_All"
    )
    attrs: dict[str, str] = {
        "priceClass": price_class,
        "transferType": "CloudFront to Internet",
    }
    return InfracostQuery(
        service="AmazonCloudFront",
        region=region,
        attributes=attrs,
        confidence_band="medium",
        args_used=["DistributionConfig.PriceClass", "region"],
    )


# Built-in 10 operation mapping table.
_BUILTIN_MAPPING: dict[str, Callable[[str, dict[str, Any]], InfracostQuery | None]] = {
    "aws_ec2_RunInstances": _map_ec2_run_instances,
    "aws_s3_PutObject": _map_s3_put_object,
    "aws_s3_GetObject": _map_s3_get_object,
    "aws_rds_CreateDBInstance": _map_rds_create_db_instance,
    "aws_lambda_InvokeFunction": _map_lambda_invoke,
    "aws_bedrock_InvokeModel": _map_bedrock_invoke_model,
    "aws_eks_CreateCluster": _map_eks_create_cluster,
    "aws_ec2_CreateNatGateway": _map_ec2_create_nat_gateway,
    "aws_ebs_CreateVolume": _map_ebs_create_volume,
    "aws_cloudfront_CreateDistribution": _map_cloudfront_create_distribution,
}


def map_request(tool_name: str, args: dict[str, Any]) -> InfracostQuery | None:
    """Map an MCP tool call to an Infracost query.

    Extended mappings (loaded from premium pack) take precedence over builtins.
    Returns None if tool_name is not mapped — caller should fail-closed (don't block).
    """
    # Extended wins over builtin
    fn = _extended_mappings.get(tool_name) or _BUILTIN_MAPPING.get(tool_name)
    if fn is None:
        return None
    return fn(tool_name, args)


def load_extended_mappings(cache_dir: Path) -> int:
    """Scan cache_dir/*.yaml and register each as an extended mapping.

    Each YAML file must contain a list of mapping entries:
      - tool_name: aws_foo_Bar
        service: "AmazonFoo"
        attributes:
          key: value
        confidence_band: high

    Returns the count of entries successfully loaded.
    """
    loaded = 0
    if not cache_dir.is_dir():
        return 0

    for yaml_path in sorted(cache_dir.glob("*.yaml")):
        try:
            with yaml_path.open(encoding="utf-8") as fh:
                entries = yaml.safe_load(fh)
            if not isinstance(entries, list):
                logger.warning("extended_mapping_skip path=%s reason=not_a_list", yaml_path)
                continue
            for entry in entries:
                tool_name = entry.get("tool_name")
                if not tool_name:
                    continue
                # Capture entry in closure
                _service = str(entry.get("service", ""))
                _attrs: dict[str, str] = {str(k): str(v) for k, v in (entry.get("attributes") or {}).items()}
                _band: Literal["high", "medium", "ceiling"] = entry.get("confidence_band", "high")
                _args_used: list[str] = list(entry.get("args_used", []))

                def _make_fn(
                    svc: str,
                    ats: dict[str, str],
                    bd: Literal["high", "medium", "ceiling"],
                    au: list[str],
                ) -> Callable[[str, dict[str, Any]], InfracostQuery | None]:
                    def _fn(tn: str, args: dict[str, Any]) -> InfracostQuery | None:
                        region = _get_region(args)
                        return InfracostQuery(
                            service=svc,
                            region=region,
                            attributes=ats,
                            confidence_band=bd,
                            args_used=au,
                        )
                    return _fn

                _extended_mappings[str(tool_name)] = _make_fn(_service, _attrs, _band, _args_used)
                loaded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("extended_mapping_load_failed path=%s error=%s", yaml_path, exc)

    return loaded


# Public alias used in __init__.py
aws_mapping = _BUILTIN_MAPPING
