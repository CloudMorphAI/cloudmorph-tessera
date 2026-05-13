"""AWS blast-radius estimator — counts principals affected by an IAM/S3/KMS policy change."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_WILDCARD_PRINCIPAL = 999_999
_CACHE_TTL = 300.0  # seconds


class BlastRadiusBackend:
    """Sync principal-count estimator backed by boto3 IAM reads.

    Counts the number of principals (users/roles/accounts) that would be
    granted or affected by the supplied IAM/S3/KMS policy document.
    """

    def __init__(self, boto_session: Any = None, cache_ttl_seconds: int = 300) -> None:
        self._session = boto_session
        self._cache_ttl = float(cache_ttl_seconds)
        # Cache: (account_id_or_scope, region, principal_pattern) → (count, expires_at)
        self._cache: dict[tuple[str, str, str], tuple[int, float]] = {}
        self._lock = threading.RLock()

    def _get_session(self) -> Any:
        if self._session is None:
            import boto3
            self._session = boto3.Session()
        return self._session

    def _iam_client(self, region: str = "us-east-1") -> Any:
        return self._get_session().client("iam", region_name=region)

    def _s3_client(self, region: str = "us-east-1") -> Any:
        return self._get_session().client("s3", region_name=region)

    def _kms_client(self, region: str = "us-east-1") -> Any:
        return self._get_session().client("kms", region_name=region)

    def _cache_get(self, key: tuple[str, str, str]) -> int | None:
        with self._lock:
            entry = self._cache.get(key)
        if entry is not None and entry[1] > time.monotonic():
            return entry[0]
        return None

    def _cache_put(self, key: tuple[str, str, str], count: int) -> None:
        with self._lock:
            self._cache[key] = (count, time.monotonic() + self._cache_ttl)

    def _count_from_principal(self, principal_entry: Any, region: str, iam_client: Any) -> int:
        """Return an integer principal count for a single Principal entry."""
        if principal_entry == "*":
            return _WILDCARD_PRINCIPAL

        if isinstance(principal_entry, str):
            if principal_entry == "*":
                return _WILDCARD_PRINCIPAL
            # Specific ARN → 1
            if ":root" in principal_entry:
                # Account root → count users + roles for that account
                return self._count_account_principals(iam_client)
            return 1

        if isinstance(principal_entry, dict):
            total = 0
            for kind, val in principal_entry.items():
                if kind == "AWS":
                    if val == "*":
                        return _WILDCARD_PRINCIPAL
                    if isinstance(val, list):
                        for arn in val:
                            if arn == "*":
                                return _WILDCARD_PRINCIPAL
                            if ":root" in str(arn):
                                total += self._count_account_principals(iam_client)
                            else:
                                total += 1
                    else:
                        if ":root" in str(val):
                            total += self._count_account_principals(iam_client)
                        else:
                            total += 1
                elif kind == "Service":
                    # AWS service principals — treat each as 1
                    if isinstance(val, list):
                        total += len(val)
                    else:
                        total += 1
                elif kind == "Federated":
                    if isinstance(val, list):
                        total += len(val)
                    else:
                        total += 1
            return total

        return 1

    def _count_account_principals(self, iam_client: Any) -> int:
        """Estimate principals in the current account by listing users + roles."""
        try:
            users = iam_client.list_users(MaxItems=1000).get("Users", [])
            roles = iam_client.list_roles(MaxItems=1000).get("Roles", [])
            return len(users) + len(roles)
        except Exception:  # noqa: BLE001
            return 100  # conservative fallback

    def _parse_policy_doc(self, doc_raw: Any) -> list[dict[str, Any]]:
        """Parse a policy document (string or dict) into a list of Statement dicts."""
        if isinstance(doc_raw, str):
            try:
                doc = json.loads(doc_raw)
            except (json.JSONDecodeError, ValueError):
                return []
        elif isinstance(doc_raw, dict):
            doc = doc_raw
        else:
            return []
        stmts = doc.get("Statement", [])
        if isinstance(stmts, dict):
            stmts = [stmts]
        return stmts if isinstance(stmts, list) else []

    def _count_policy_principals(self, policy_doc_raw: Any, region: str, iam_client: Any) -> int:
        """Count principals across all statements in a policy document."""
        stmts = self._parse_policy_doc(policy_doc_raw)
        total = 0
        for stmt in stmts:
            principal = stmt.get("Principal")
            if principal is None:
                continue
            count = self._count_from_principal(principal, region, iam_client)
            if count >= _WILDCARD_PRINCIPAL:
                return _WILDCARD_PRINCIPAL
            total += count
        return total

    def compute(self, tool_name: str, args: dict[str, Any]) -> int:
        """Return the estimated principal count for the given tool call.

        Dispatches based on tool_name. On boto3 exceptions returns 1 (conservative
        but not wildcard — let the condition operator decide).
        """
        region = str(args.get("region") or args.get("Region") or "us-east-1")

        if tool_name in ("iam:PutRolePolicy", "aws_iam_PutRolePolicy"):
            return self._compute_iam_role_policy(args, region)

        if tool_name in ("iam:AttachRolePolicy", "aws_iam_AttachRolePolicy"):
            return self._compute_iam_attach_role_policy(args, region)

        if tool_name in ("s3:PutBucketPolicy", "aws_s3_PutBucketPolicy"):
            return self._compute_s3_bucket_policy(args, region)

        if tool_name in ("kms:PutKeyPolicy", "aws_kms_PutKeyPolicy"):
            return self._compute_kms_key_policy(args, region)

        # Unknown tool — return 0 so the condition can decide
        return 0

    def _compute_iam_role_policy(self, args: dict[str, Any], region: str) -> int:
        role_name = args.get("RoleName", args.get("roleName", ""))
        cache_key = (role_name, region, "role_policy")
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        iam = self._iam_client(region)
        try:
            role_resp = iam.get_role(RoleName=role_name)
            assume_doc = role_resp.get("Role", {}).get("AssumeRolePolicyDocument", {})
        except Exception:  # noqa: BLE001
            return 1

        count = self._count_policy_principals(assume_doc, region, iam)
        self._cache_put(cache_key, count)
        return count

    def _compute_iam_attach_role_policy(self, args: dict[str, Any], region: str) -> int:
        role_name = args.get("RoleName", args.get("roleName", ""))
        cache_key = (role_name, region, "attach_role_policy")
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        iam = self._iam_client(region)
        try:
            role_resp = iam.get_role(RoleName=role_name)
            assume_doc = role_resp.get("Role", {}).get("AssumeRolePolicyDocument", {})
        except Exception:  # noqa: BLE001
            return 1

        count = self._count_policy_principals(assume_doc, region, iam)
        self._cache_put(cache_key, count)
        return count

    def _compute_s3_bucket_policy(self, args: dict[str, Any], region: str) -> int:
        bucket = args.get("Bucket", args.get("bucket", ""))
        policy_doc = args.get("Policy", args.get("policy", "{}"))
        cache_key = (bucket, region, "s3_bucket_policy")
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        iam = self._iam_client(region)
        count = self._count_policy_principals(policy_doc, region, iam)
        self._cache_put(cache_key, count)
        return count

    def _compute_kms_key_policy(self, args: dict[str, Any], region: str) -> int:
        key_id = args.get("KeyId", args.get("keyId", ""))
        policy_doc = args.get("Policy", args.get("policy", "{}"))
        cache_key = (key_id, region, "kms_key_policy")
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        iam = self._iam_client(region)
        count = self._count_policy_principals(policy_doc, region, iam)
        self._cache_put(cache_key, count)
        return count
