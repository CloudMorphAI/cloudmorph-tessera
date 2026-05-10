"""Tessera runtime configuration loader."""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from tessera.errors import ConfigError

_DEFAULT_CONFIG_PATH = "/etc/tessera/tessera.yaml"
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ListenConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8080


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = "bearer"


class AuditConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sink: str = "sqlite"
    path: str = "/var/lib/tessera/audit.db"
    also_stdout: bool = False


class PoliciesMode(str, Enum):
    enforcement = "enforcement"
    log_only = "log_only"
    observation = "observation"


class PoliciesConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dir: str = "/etc/tessera/policies"
    reload: str = "watch"  # watch | sighup | none
    mode: PoliciesMode = PoliciesMode.log_only
    default_action: str = "block"


class IntentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta_key: str = "tessera_intent"
    required: bool = False


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    bearer_token_env: str = "TESSERA_METRICS_TOKEN"  # noqa: S105


class CredentialsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    header: str = "Authorization"
    value: str  # may contain ${VAR} — resolved at load time


class UpstreamConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    url: str
    timeout_seconds: int = 30
    credentials: CredentialsConfig | None = None


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lockdown: bool = False


class TesseraConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    listen: ListenConfig = ListenConfig()
    auth: AuthConfig = AuthConfig()
    audit: AuditConfig = AuditConfig()
    policies: PoliciesConfig = PoliciesConfig()
    intent: IntentConfig = IntentConfig()
    metrics: MetricsConfig = MetricsConfig()
    deployment_id: str = "default"
    log_level: str = "INFO"
    upstreams: list[UpstreamConfig] = []
    runtime: RuntimeConfig = RuntimeConfig()


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


def _interpolate_credentials(upstream_data: dict[str, Any]) -> dict[str, Any]:
    """Resolve ${VAR} in credentials.value for a single upstream dict."""
    creds = upstream_data.get("credentials")
    if not creds or not isinstance(creds, dict):
        return upstream_data

    raw_value: str | None = creds.get("value")
    if not raw_value or not isinstance(raw_value, str):
        return upstream_data

    name = upstream_data.get("name", "<unknown>")

    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ConfigError(
                f"env var {var_name!r} referenced in upstreams[{name}].credentials.value is not set"
            )
        return val

    resolved = _VAR_RE.sub(_replace, raw_value)
    return {
        **upstream_data,
        "credentials": {**creds, "value": resolved},
    }


# ---------------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------------


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Mutate *data* in-place with env-var overrides; return it."""

    def _setdeep(d: dict[str, Any], section: str, key: str, value: Any) -> None:
        if section not in d or not isinstance(d[section], dict):
            d[section] = {}
        d[section][key] = value

    if (v := os.environ.get("TESSERA_POLICY_DIR")) is not None:
        _setdeep(data, "policies", "dir", v)

    if (v := os.environ.get("TESSERA_AUDIT_PATH")) is not None:
        _setdeep(data, "audit", "path", v)

    if (v := os.environ.get("TESSERA_LOG_LEVEL")) is not None:
        data["log_level"] = v

    if (v := os.environ.get("TESSERA_DEPLOYMENT_ID")) is not None:
        data["deployment_id"] = v

    if (v := os.environ.get("TESSERA_BIND_HOST")) is not None:
        _setdeep(data, "listen", "host", v)

    if (v := os.environ.get("TESSERA_BIND_PORT")) is not None:
        try:
            data.setdefault("listen", {})
            if not isinstance(data["listen"], dict):
                data["listen"] = {}
            data["listen"]["port"] = int(v)
        except ValueError:
            raise ConfigError(f"TESSERA_BIND_PORT must be an integer, got {v!r}") from None

    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config_from_dict(data: dict[str, Any]) -> TesseraConfig:
    """Construct TesseraConfig from a plain dict (for tests and internal use).

    Env-var overrides and ${VAR} interpolation are NOT applied here — callers
    supply the fully-resolved dict.
    """
    return TesseraConfig(**data)


def load_config(path: str | Path | None = None) -> TesseraConfig:
    """Load TesseraConfig from *path* (YAML file).

    Resolution order:
    1. *path* argument if given
    2. ``TESSERA_CONFIG_PATH`` env var
    3. ``/etc/tessera/tessera.yaml`` (built-in default)

    If no path is given and the default file is absent, ``TesseraConfig()``
    with all defaults is returned.  If an explicit path is given and it does
    not exist, ``ConfigError`` is raised.
    """
    explicit = path is not None
    if path is None:
        env_path = os.environ.get("TESSERA_CONFIG_PATH")
        resolved: Path = Path(env_path) if env_path else Path(_DEFAULT_CONFIG_PATH)
    else:
        resolved = Path(path)

    if not resolved.exists():
        if explicit:
            raise ConfigError(f"Config file not found: {resolved}")
        # Default path missing → return all-defaults config (with env overrides)
        data: dict[str, Any] = {}
        _apply_env_overrides(data)
        return TesseraConfig(**data)

    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {resolved} must be a YAML mapping, got {type(raw).__name__}")

    # Interpolate ${VAR} in upstreams[].credentials.value
    upstreams_raw = raw.get("upstreams") or []
    if isinstance(upstreams_raw, list):
        raw["upstreams"] = [_interpolate_credentials(u) if isinstance(u, dict) else u for u in upstreams_raw]

    _apply_env_overrides(raw)

    return TesseraConfig(**raw)
