"""Typed env config for executors.

Replaces the ad-hoc os.getenv calls scattered across each executor's main.py.
Pydantic validates types up-front so the executor fails fast on misconfig
rather than crashing mid-job.

Usage::

    from cloudmorph_common.settings import ExecutorSettings
    settings = ExecutorSettings()  # reads from env
"""

from __future__ import annotations

from typing import Optional

try:
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:  # pragma: no cover
    # Fallback for environments without pydantic-settings: define a minimal class.
    BaseSettings = object  # type: ignore[assignment, misc]
    SettingsConfigDict = dict  # type: ignore[assignment, misc]
    Field = lambda default=None, **kwargs: default  # type: ignore[assignment]  # noqa: E731
    _HAS_PYDANTIC_SETTINGS = False


class ExecutorSettings(BaseSettings):
    """Settings for a CloudMorph executor.

    All fields read from env; some support defaults. Required fields raise
    ValidationError if missing.
    """

    if _HAS_PYDANTIC_SETTINGS:
        model_config = SettingsConfigDict(
            env_prefix="",
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )

    # --- Control Center upstream ---
    control_center_api_url: str = Field(..., description="Upstream Control Center API base URL")
    control_center_executor_token: str = Field(..., description="Long-lived install token")
    control_center_tenant_id: str = Field(..., description="Tenant identifier")
    control_center_account_id: str = Field(..., description="Cloud account identifier")
    control_center_capabilities: str = Field(default="agent.run", description="Comma-separated capability list")
    control_center_job_schema_path: Optional[str] = Field(default=None)

    # --- Executor identity / lifecycle ---
    executor_id: Optional[str] = Field(default=None, description="Defaults to hostname if unset")
    heartbeat_seconds: float = Field(default=20.0, ge=1.0, le=300.0)
    poll_base_seconds: float = Field(default=2.0, ge=0.1, le=60.0)
    poll_max_seconds: float = Field(default=15.0, ge=1.0, le=600.0)

    # --- One-shot mode (ECS task per job) ---
    job_id: Optional[str] = Field(default=None, alias="JOB_ID")
    job_token: Optional[str] = Field(default=None, alias="JOB_TOKEN")

    # --- Storage / artifacts ---
    storage_provider: str = Field(default="aws", description="aws | gcp | azure | none")
    storage_bucket: Optional[str] = Field(default=None, alias="STORAGE_BUCKET")
    storage_location: Optional[str] = Field(default=None, alias="STORAGE_LOCATION")
    storage_region: Optional[str] = Field(default=None, alias="STORAGE_REGION")
    storage_prefix: str = Field(default="", alias="STORAGE_PREFIX")
    artifact_base_prefix: Optional[str] = Field(default=None, alias="ARTIFACT_BASE_PREFIX")

    # Cloud-native fallbacks (resolved by main.py if storage_region missing)
    aws_region: Optional[str] = Field(default=None, alias="AWS_REGION")
    aws_default_region: Optional[str] = Field(default=None, alias="AWS_DEFAULT_REGION")
    gcp_location: Optional[str] = Field(default=None, alias="GCP_LOCATION")
    azure_storage_account: Optional[str] = Field(default=None, alias="AZURE_STORAGE_ACCOUNT")
    azure_storage_container: Optional[str] = Field(default=None, alias="AZURE_STORAGE_CONTAINER")
    azure_storage_connection_string: Optional[str] = Field(
        default=None, alias="AZURE_STORAGE_CONNECTION_STRING"
    )

    # --- Audit ---
    audit_sink: str = Field(default="stdout", description="stdout | s3 | s3-customer-owned | none")
    audit_buffer_path: str = Field(default="/var/lib/cloudmorph/audit-buffer")
    audit_retention_days: int = Field(default=30, ge=1, le=3650)
    audit_s3_bucket: Optional[str] = Field(default=None)
    audit_s3_region: Optional[str] = Field(default=None)
    audit_s3_role_arn: Optional[str] = Field(default=None, description="Cross-account role for customer-owned sink")
    audit_object_lock_mode: Optional[str] = Field(default=None, description="None | GOVERNANCE | COMPLIANCE")

    # --- Operational ---
    fail_mode: str = Field(default="closed", description="closed | open — on policy engine unhealthy")
    log_level: str = Field(default="info", description="debug | info | warn | error | silent")

    @property
    def capabilities_list(self) -> list[str]:
        return [c.strip() for c in self.control_center_capabilities.split(",") if c.strip()]

    @property
    def storage_bucket_resolved(self) -> Optional[str]:
        return self.storage_bucket or self.storage_location

    @property
    def storage_region_resolved(self) -> Optional[str]:
        return self.storage_region or self.aws_region or self.aws_default_region or self.gcp_location
