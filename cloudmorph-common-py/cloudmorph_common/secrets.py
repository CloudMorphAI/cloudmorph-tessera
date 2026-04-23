"""Secrets resolver layer.

Replaces raw env reads for sensitive values. Backends:
- env (default): read from os.environ
- aws-secrets-manager: AWS Secrets Manager
- gcp-secret-manager: GCP Secret Manager (post-MVP)
- azure-key-vault: Azure Key Vault (post-MVP)

Selected via `CLOUDMORPH_SECRETS_BACKEND` env var.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from cloudmorph_common.errors import ConfigError


class SecretResolver(ABC):
    """Resolve a secret by key. Backend-specific."""

    @abstractmethod
    def get(self, key: str) -> str:
        """Get a secret value. Raises ConfigError if not found."""
        ...


class EnvSecretResolver(SecretResolver):
    """Read secrets from environment variables."""

    def get(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise ConfigError(f"Secret '{key}' not in environment")
        return value


class AwsSecretsManagerResolver(SecretResolver):
    """Read secrets from AWS Secrets Manager.

    Args:
        region: AWS region. Defaults to AWS_REGION / AWS_DEFAULT_REGION.
        client: Optional pre-built boto3 client (for tests / DI).
        cache_ttl_seconds: Cache resolved values for this many seconds (default 60).
    """

    def __init__(
        self,
        region: str | None = None,
        client: object | None = None,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self.region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        self._client = client
        self._cache: dict[str, tuple[float, str]] = {}
        self._ttl = cache_ttl_seconds

    def _client_lazy(self) -> object:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConfigError("boto3 not installed; pip install cloudmorph-common[aws]") from exc
        self._client = boto3.client("secretsmanager", region_name=self.region)
        return self._client

    def get(self, key: str) -> str:
        import time

        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0] < self._ttl):
            return cached[1]
        client = self._client_lazy()
        try:
            resp = client.get_secret_value(SecretId=key)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"AWS Secrets Manager get_secret_value('{key}') failed: {exc}") from exc
        value = resp.get("SecretString") or resp.get("SecretBinary", b"").decode("utf-8")
        if not value:
            raise ConfigError(f"Secret '{key}' returned empty value from AWS Secrets Manager")
        self._cache[key] = (now, value)
        return value


def get_resolver() -> SecretResolver:
    """Return the configured resolver based on CLOUDMORPH_SECRETS_BACKEND env var."""
    backend = (os.getenv("CLOUDMORPH_SECRETS_BACKEND") or "env").lower().strip()
    if backend == "env":
        return EnvSecretResolver()
    if backend in {"aws", "aws-secrets-manager"}:
        return AwsSecretsManagerResolver()
    raise ConfigError(f"Unknown CLOUDMORPH_SECRETS_BACKEND: {backend!r}")
