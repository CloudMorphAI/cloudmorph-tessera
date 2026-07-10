"""Tests for `tessera doctor` — read-only environment diagnostics.

Runs the checks against a fake HOME (no real MCP configs, no real
~/.tessera/*) to verify the fail path with actionable fix hints, then
against a populated fake HOME to verify the pass path. Confirms doctor
never writes anything.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tessera import doctor as doctor_mod


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # These two are module-level constants computed from Path.home() at
    # import time — re-point them explicitly so the fake home takes effect.
    monkeypatch.setattr(doctor_mod, "_OAUTH_TOKEN_FILE", tmp_path / ".tessera" / "oauth.json")
    monkeypatch.setattr(doctor_mod, "_POLICY_CACHE_FILE", tmp_path / ".tessera" / "policy-cache.db")
    monkeypatch.delenv("TESSERA_POLICY_LOADER", raising=False)
    monkeypatch.delenv("TESSERA_CONFIG_PATH", raising=False)
    return tmp_path


def test_doctor_all_fail_on_empty_home(fake_home: Path) -> None:
    lines: list[str] = []
    results = doctor_mod.run_doctor(bind_port=65432, echo=lines.append)

    by_name = {r.name: r for r in results}

    mcp_checks = [r for r in results if "MCP config" in r.name]
    assert len(mcp_checks) == 3
    assert all(not r.ok for r in mcp_checks)
    assert all(r.fix_hint for r in mcp_checks)

    assert by_name["Auth token (~/.tessera/oauth.json)"].ok is False
    assert by_name["Policy sync cache (~/.tessera/policy-cache.db)"].ok is False

    reachability = next(r for r in results if r.name.startswith("Serve reachability"))
    assert reachability.ok is False

    # Cloud loader unset is informational-pass, not a failure.
    assert by_name["Cloud policy loader (TESSERA_POLICY_LOADER / serve --cloud)"].ok is True

    # Nothing was created on disk by doctor itself.
    assert not (fake_home / ".tessera").exists()
    assert not (fake_home / ".cursor").exists()

    output = "\n".join(lines)
    assert "[FAIL]" in output
    assert "-> fix:" in output
    assert "checks passed" in output


def test_doctor_passes_when_configured(fake_home: Path) -> None:
    tessera_dir = fake_home / ".tessera"
    tessera_dir.mkdir(parents=True)

    # Valid, unexpired auth token.
    (tessera_dir / "oauth.json").write_text(
        json.dumps(
            {
                "access_token": "tk_fake_xxxxxxxxxxxxxxxx",  # noqa: S106
                "obtained_at": int(time.time()),
                "expires_in": 3600,
            }
        ),
        encoding="utf-8",
    )

    # Fresh policy-sync cache with one cached policy.
    cache_path = tessera_dir / "policy-cache.db"
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute("CREATE TABLE policies (policy_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO policies VALUES ('p1')")
        conn.commit()
    finally:
        conn.close()

    # Cursor global MCP config with a 'tessera' entry.
    cursor_dir = fake_home / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"tessera": {"url": "http://localhost:8080/mcp"}}}),
        encoding="utf-8",
    )

    # Local policy dir with one policy file.
    policy_dir = fake_home / "policies"
    policy_dir.mkdir()
    (policy_dir / "allow-all.yaml").write_text(
        "id: allow-all\nname: allow all\naction: allow\n", encoding="utf-8"
    )

    lines: list[str] = []
    results = doctor_mod.run_doctor(policy_dir=str(policy_dir), bind_port=65433, echo=lines.append)
    by_name = {r.name: r for r in results}

    assert by_name["Cursor MCP config"].ok is True
    assert by_name["Auth token (~/.tessera/oauth.json)"].ok is True
    assert "expires in" in by_name["Auth token (~/.tessera/oauth.json)"].detail
    assert by_name["Policy sync cache (~/.tessera/policy-cache.db)"].ok is True
    assert by_name["Policy counts"].ok is True
    assert "1 local YAML policy" in by_name["Policy counts"].detail
    assert "1 cached cloud policies" in by_name["Policy counts"].detail


def test_doctor_reports_expired_token(fake_home: Path) -> None:
    tessera_dir = fake_home / ".tessera"
    tessera_dir.mkdir(parents=True)
    (tessera_dir / "oauth.json").write_text(
        json.dumps(
            {
                "access_token": "tk_expired_xxxxxxxxxxxxxxxx",  # noqa: S106
                "obtained_at": int(time.time()) - 10_000,
                "expires_in": 100,
            }
        ),
        encoding="utf-8",
    )

    results = doctor_mod.run_doctor(bind_port=65434, echo=lambda *_: None)
    by_name = {r.name: r for r in results}
    token_result = by_name["Auth token (~/.tessera/oauth.json)"]
    assert token_result.ok is False
    assert "expired" in token_result.detail
    assert token_result.fix_hint and "tessera login" in token_result.fix_hint


def test_doctor_serve_reachability_detects_listening_socket(fake_home: Path) -> None:
    import socket
    import threading

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    stop = threading.Event()

    def _accept_loop() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                continue

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    try:
        result = doctor_mod.check_serve_reachability("127.0.0.1", port)
        assert result.ok is True
        assert "listening" in result.detail
    finally:
        stop.set()
        server.close()
        t.join(timeout=2)
