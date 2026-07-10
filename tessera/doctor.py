"""`tessera doctor` — read-only, offline diagnostics for the local environment.

Checks MCP client configuration (Cursor, Claude Desktop, Claude Code), the
saved OAuth token, the local policy-sync cache, whether cloud-loader mode is
active, whether a `tessera serve` is reachable, and local/cloud policy
counts. Every check is a read: a file existence/mtime/parse check or a local
TCP connect probe. Nothing is ever written, deleted, or mutated — this
command never changes config, tokens, or policies.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

__all__ = ["CheckResult", "run_doctor"]

_CLOUD_LOADER_SPEC = "tessera.policy.cloud_loader:CloudBackedPolicyLoader"
_SYNC_CACHE_STALE_HOURS = 24.0

# Module-level so tests can monkeypatch them directly (they mirror the
# ~/.tessera/* paths used across the CLI — see cli.py's _OAUTH_TOKEN_FILE and
# cloud_sync.py's _DEFAULT_CACHE_PATH).
_OAUTH_TOKEN_FILE = Path.home() / ".tessera" / "oauth.json"
_POLICY_CACHE_FILE = Path.home() / ".tessera" / "policy-cache.db"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix_hint: str | None = None

    def render(self) -> str:
        if self.ok:
            return f"[PASS] {self.name} — {self.detail}"
        hint = f" -> fix: {self.fix_hint}" if self.fix_hint else ""
        return f"[FAIL] {self.name} — {self.detail}{hint}"


# ── MCP client config paths (mirrors tessera/cli.py install-* commands) ─────


def _cursor_mcp_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _claude_code_config_path() -> Path:
    # Mirrors install_claude_code's resolution exactly (see tessera/cli.py).
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude.json"
    return Path.home() / ".claude.json"


def _check_mcp_entry(client_name: str, config_path: Path) -> CheckResult:
    name = f"{client_name} MCP config"
    if not config_path.exists():
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{config_path} not found",
            fix_hint=f"run `tessera install-{client_name.lower().replace(' ', '-')}` to configure {client_name}",
        )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{config_path} exists but could not be parsed: {exc}",
            fix_hint=f"inspect {config_path} for malformed JSON, or re-run the install command with --upgrade",
        )
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict) and "tessera" in servers:
        return CheckResult(name=name, ok=True, detail=f"'tessera' entry present in {config_path}")
    return CheckResult(
        name=name,
        ok=False,
        detail=f"no 'tessera' entry in {config_path}",
        fix_hint=f"run `tessera install-{client_name.lower().replace(' ', '-')}` to add it",
    )


def check_cursor() -> CheckResult:
    return _check_mcp_entry("Cursor", _cursor_mcp_path())


def check_claude_desktop() -> CheckResult:
    return _check_mcp_entry("Claude Desktop", _claude_desktop_config_path())


def check_claude_code() -> CheckResult:
    return _check_mcp_entry("Claude Code", _claude_code_config_path())


# ── Auth token ────────────────────────────────────────────────────────────


def _decode_jwt_exp(token: str) -> float | None:
    """Best-effort, unverified decode of a JWT's `exp` claim — for display only.

    Never validates the signature; this is purely a local, offline read of
    the token's own claimed expiry so `doctor` can warn before it lapses.
    """
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes)
    except Exception:  # noqa: BLE001 — decode failure just means "no exp available"
        return None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    return float(exp) if isinstance(exp, (int, float)) else None


def check_auth_token() -> CheckResult:
    name = "Auth token (~/.tessera/oauth.json)"
    if not _OAUTH_TOKEN_FILE.exists():
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{_OAUTH_TOKEN_FILE} not found",
            fix_hint="run `tessera login` to authenticate with Tessera Cloud",
        )
    try:
        data = json.loads(_OAUTH_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{_OAUTH_TOKEN_FILE} could not be parsed: {exc}",
            fix_hint="run `tessera login` again to regenerate the token file",
        )

    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not access_token:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{_OAUTH_TOKEN_FILE} has no access_token",
            fix_hint="run `tessera login` to authenticate",
        )

    now = time.time()
    expires_at: float | None = None
    obtained_at = data.get("obtained_at")
    expires_in = data.get("expires_in")
    if isinstance(obtained_at, (int, float)) and isinstance(expires_in, (int, float)):
        expires_at = float(obtained_at) + float(expires_in)
    if expires_at is None:
        expires_at = _decode_jwt_exp(access_token)

    if expires_at is not None and expires_at <= now:
        expired_at_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires_at))
        return CheckResult(
            name=name,
            ok=False,
            detail=f"access_token expired at {expired_at_str}",
            fix_hint="run `tessera login` to refresh the token",
        )
    if expires_at is not None:
        remaining_h = (expires_at - now) / 3600
        return CheckResult(name=name, ok=True, detail=f"access_token present, expires in {remaining_h:.1f}h")
    return CheckResult(name=name, ok=True, detail="access_token present (no expiry claim found)")


# ── Sync cache ────────────────────────────────────────────────────────────


def check_sync_cache() -> CheckResult:
    name = "Policy sync cache (~/.tessera/policy-cache.db)"
    if not _POLICY_CACHE_FILE.exists():
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{_POLICY_CACHE_FILE} not found",
            fix_hint="run `tessera login` then `tessera config sync` to populate the cloud policy cache",
        )
    age_h = (time.time() - _POLICY_CACHE_FILE.stat().st_mtime) / 3600
    if age_h > _SYNC_CACHE_STALE_HOURS:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"cache is {age_h:.1f}h old (stale — last refreshed {_POLICY_CACHE_FILE})",
            fix_hint="run `tessera config sync` to refresh",
        )
    return CheckResult(name=name, ok=True, detail=f"cache present, last refreshed {age_h:.1f}h ago")


# ── Cloud loader ──────────────────────────────────────────────────────────


def check_cloud_loader() -> CheckResult:
    name = "Cloud policy loader (TESSERA_POLICY_LOADER / serve --cloud)"
    value = os.environ.get("TESSERA_POLICY_LOADER", "")
    if value == _CLOUD_LOADER_SPEC:
        return CheckResult(
            name=name, ok=True, detail="TESSERA_POLICY_LOADER is set — `serve` will enforce cloud policies too"
        )
    if value:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"TESSERA_POLICY_LOADER is set to a non-default loader: {value!r}",
            fix_hint=f"unset TESSERA_POLICY_LOADER, or set it to {_CLOUD_LOADER_SPEC!r} for cloud enforcement",
        )
    return CheckResult(
        name=name,
        ok=True,
        detail="not set — local-YAML-only mode (pass `tessera serve --cloud` to also enforce cloud policies)",
    )


# ── Serve reachability ────────────────────────────────────────────────────


def check_serve_reachability(host: str = "127.0.0.1", port: int = 8080, timeout: float = 1.0) -> CheckResult:
    name = f"Serve reachability ({host}:{port})"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"nothing listening on {host}:{port}",
            fix_hint=f"start the proxy with `tessera serve --bind {host}:{port}`",
        )
    return CheckResult(name=name, ok=True, detail=f"a server is listening on {host}:{port}")


# ── Policy counts ─────────────────────────────────────────────────────────


def _cloud_policy_count() -> int:
    if not _POLICY_CACHE_FILE.exists():
        return 0
    try:
        conn = sqlite3.connect(str(_POLICY_CACHE_FILE))
        try:
            row = conn.execute("SELECT COUNT(*) FROM policies").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def check_policy_counts(policy_dir: str | None = None) -> CheckResult:
    name = "Policy counts"
    resolved_dir: Path
    if policy_dir:
        resolved_dir = Path(policy_dir)
    else:
        try:
            from tessera.config import load_config

            resolved_dir = Path(load_config().policies.dir)
        except Exception:  # noqa: BLE001 — a broken/missing config must not crash doctor
            resolved_dir = Path("policies")

    local_count = 0
    if resolved_dir.is_dir():
        local_count = len(set(resolved_dir.glob("*.yaml")) | set(resolved_dir.glob("*.yml")))

    cloud_count = _cloud_policy_count()

    ok = local_count > 0 or cloud_count > 0
    unit = "policy" if local_count == 1 else "policies"
    detail = f"{local_count} local YAML {unit} in {resolved_dir}, {cloud_count} cached cloud policies"
    fix_hint = None
    if not ok:
        fix_hint = f"add policy YAML files to {resolved_dir}, or run `tessera config sync` for cloud policies"
    return CheckResult(name=name, ok=ok, detail=detail, fix_hint=fix_hint)


# ── Orchestration ─────────────────────────────────────────────────────────


def run_doctor(
    *,
    bind_host: str = "127.0.0.1",
    bind_port: int = 8080,
    policy_dir: str | None = None,
    echo: Callable[..., None] = print,
) -> list[CheckResult]:
    """Run every check and print a pass/fail report via `echo`. Returns the results."""
    checks = [
        check_cursor(),
        check_claude_desktop(),
        check_claude_code(),
        check_auth_token(),
        check_sync_cache(),
        check_cloud_loader(),
        check_serve_reachability(bind_host, bind_port),
        check_policy_counts(policy_dir),
    ]

    echo("Tessera doctor — environment diagnostics (read-only, no changes made)")
    echo("=" * 72)
    for c in checks:
        echo(c.render())
    echo("=" * 72)

    passed = sum(1 for c in checks if c.ok)
    echo(f"{passed}/{len(checks)} checks passed")

    return checks
