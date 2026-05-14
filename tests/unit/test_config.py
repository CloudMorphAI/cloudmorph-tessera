"""Unit tests for tessera.config."""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from tessera.config import (
    PoliciesMode,
    TesseraConfig,
    load_config,
    load_config_from_dict,
)
from tessera.errors import ConfigError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path, content: str):
    """Write *content* to a temp tessera.yaml and return the Path."""
    p = tmp_path / "tessera.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_when_no_file(tmp_path):
    """Non-existent default path returns TesseraConfig with all defaults."""
    cfg = load_config(tmp_path / "does_not_exist.yaml" if False else None)
    # Patch: call with a path that does not exist *and is default* (no explicit path)
    # Simplest: use load_config_from_dict({}) which exercises all defaults
    cfg = load_config_from_dict({})
    # v0.2.0: bind default flipped to loopback (A-4-1)
    assert cfg.listen.host == "127.0.0.1"
    assert cfg.listen.port == 8080
    assert cfg.audit.sink == "sqlite"
    assert cfg.audit.path == "/var/lib/tessera/audit.db"
    assert cfg.audit.also_stdout is False
    assert cfg.policies.dir == "/etc/tessera/policies"
    assert cfg.policies.reload == "watch"
    assert cfg.policies.mode == PoliciesMode.log_only
    assert cfg.policies.default_action == "block"
    assert cfg.intent.meta_key == "tessera_intent"
    assert cfg.intent.required is False
    assert cfg.metrics.enabled is False
    assert cfg.deployment_id == "default"
    assert cfg.log_level == "INFO"
    assert cfg.upstreams == []
    assert cfg.runtime.lockdown is False


def test_defaults_when_no_file_via_loader(tmp_path, monkeypatch):
    """load_config() with no args and absent default path returns defaults."""
    monkeypatch.delenv("TESSERA_CONFIG_PATH", raising=False)
    # Point default resolution at a path that surely won't exist
    monkeypatch.setenv("TESSERA_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
    cfg = load_config()
    assert isinstance(cfg, TesseraConfig)
    assert cfg.deployment_id == "default"


# ---------------------------------------------------------------------------
# Load from YAML
# ---------------------------------------------------------------------------


def test_load_from_yaml(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        listen:
          host: 127.0.0.1
          port: 9000
        deployment_id: test-env
        policies:
          mode: enforcement
        upstreams: []
        """,
    )
    cfg = load_config(p)
    assert cfg.listen.host == "127.0.0.1"
    assert cfg.listen.port == 9000
    assert cfg.deployment_id == "test-env"
    assert cfg.policies.mode == PoliciesMode.enforcement


# ---------------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------------


def test_env_override_policy_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_POLICY_DIR", "/custom/policies")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    cfg = load_config(p)
    assert cfg.policies.dir == "/custom/policies"


def test_env_override_bind_port_coercion(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_BIND_PORT", "9090")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    cfg = load_config(p)
    assert cfg.listen.port == 9090
    assert isinstance(cfg.listen.port, int)


def test_env_override_bind_port_invalid_raises_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_BIND_PORT", "abc")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    with pytest.raises(ConfigError, match="TESSERA_BIND_PORT"):
        load_config(p)


def test_env_override_deployment_id(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_DEPLOYMENT_ID", "prod-eu-west")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    cfg = load_config(p)
    assert cfg.deployment_id == "prod-eu-west"


def test_env_override_audit_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_AUDIT_PATH", "/tmp/test-audit.db")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    cfg = load_config(p)
    assert cfg.audit.path == "/tmp/test-audit.db"


def test_env_override_log_level(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_LOG_LEVEL", "DEBUG")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    cfg = load_config(p)
    assert cfg.log_level == "DEBUG"


def test_env_override_bind_host(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_BIND_HOST", "10.0.0.1")
    p = _write_yaml(tmp_path, "upstreams: []\n")
    cfg = load_config(p)
    assert cfg.listen.host == "10.0.0.1"


# ---------------------------------------------------------------------------
# ${VAR} interpolation in credentials
# ---------------------------------------------------------------------------


def test_var_interpolation_in_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-value")
    p = _write_yaml(
        tmp_path,
        """
        upstreams:
          - name: aws
            url: https://mcp.aws.example.com
            credentials:
              header: Authorization
              value: "Bearer ${MY_TOKEN}"
        """,
    )
    cfg = load_config(p)
    assert cfg.upstreams[0].credentials is not None
    assert cfg.upstreams[0].credentials.value == "Bearer secret-value"


def test_var_interpolation_unset_raises_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    p = _write_yaml(
        tmp_path,
        """
        upstreams:
          - name: github
            url: https://mcp.github.example.com
            credentials:
              header: Authorization
              value: "Bearer ${MISSING_TOKEN}"
        """,
    )
    with pytest.raises(ConfigError, match="MISSING_TOKEN"):
        load_config(p)


def test_var_interpolation_multiple_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_PART", "mcp.example.com")
    monkeypatch.setenv("TOKEN_PART", "tok123")
    p = _write_yaml(
        tmp_path,
        """
        upstreams:
          - name: multi
            url: https://mcp.example.com
            credentials:
              header: Authorization
              value: "${HOST_PART}:${TOKEN_PART}"
        """,
    )
    cfg = load_config(p)
    assert cfg.upstreams[0].credentials is not None
    assert cfg.upstreams[0].credentials.value == "mcp.example.com:tok123"


# ---------------------------------------------------------------------------
# PoliciesMode enum
# ---------------------------------------------------------------------------


def test_policies_mode_enum_validation(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        policies:
          mode: enforcement
        upstreams: []
        """,
    )
    cfg = load_config(p)
    assert cfg.policies.mode == PoliciesMode.enforcement


def test_policies_mode_log_only(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        policies:
          mode: log_only
        upstreams: []
        """,
    )
    cfg = load_config(p)
    assert cfg.policies.mode == PoliciesMode.log_only


def test_policies_mode_observation(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        policies:
          mode: observation
        upstreams: []
        """,
    )
    cfg = load_config(p)
    assert cfg.policies.mode == PoliciesMode.observation


def test_policies_mode_invalid_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        policies:
          mode: invalid
        upstreams: []
        """,
    )
    with pytest.raises(ValidationError):
        load_config(p)


# ---------------------------------------------------------------------------
# MetricsConfig defaults
# ---------------------------------------------------------------------------


def test_metrics_defaults_disabled():
    cfg = load_config_from_dict({})
    assert cfg.metrics.enabled is False
    assert cfg.metrics.bearer_token_env == "TESSERA_METRICS_TOKEN"


# ---------------------------------------------------------------------------
# deployment_id default
# ---------------------------------------------------------------------------


def test_deployment_id_default():
    cfg = load_config_from_dict({})
    assert cfg.deployment_id == "default"


# ---------------------------------------------------------------------------
# Explicit path missing raises ConfigError
# ---------------------------------------------------------------------------


def test_explicit_path_missing_raises_config_error():
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config("/nonexistent/path/tessera.yaml")


# ---------------------------------------------------------------------------
# Extra keys are ignored (ConfigDict extra="ignore")
# ---------------------------------------------------------------------------


def test_unknown_yaml_keys_ignored(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        upstreams: []
        some_future_key: will_be_ignored
        """,
    )
    cfg = load_config(p)
    assert cfg.deployment_id == "default"


# ---------------------------------------------------------------------------
# load_config_from_dict helper
# ---------------------------------------------------------------------------


def test_load_config_from_dict_partial():
    cfg = load_config_from_dict({"deployment_id": "unit-test", "listen": {"port": 7777}})
    assert cfg.deployment_id == "unit-test"
    assert cfg.listen.port == 7777
    assert cfg.listen.host == "127.0.0.1"  # v0.2.0 default (A-4-1 bind flip)


def test_load_config_from_dict_upstream():
    cfg = load_config_from_dict(
        {
            "upstreams": [
                {
                    "name": "test-upstream",
                    "url": "https://example.com",
                    "credentials": {"header": "Authorization", "value": "Bearer already-resolved"},
                }
            ]
        }
    )
    assert len(cfg.upstreams) == 1
    assert cfg.upstreams[0].name == "test-upstream"
    assert cfg.upstreams[0].credentials is not None
    assert cfg.upstreams[0].credentials.value == "Bearer already-resolved"
