"""Unit tests for tessera install-cursor and install-claude-desktop CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tessera.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# install-cursor
# ---------------------------------------------------------------------------


def test_install_cursor_writes_project_config(tmp_path: Path) -> None:
    """v0.8 default: unified entry at /mcp with key 'tessera'."""
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--token", "tk_test_xxxxxxxxxxxx",
        ],
    )
    assert result.exit_code == 0, result.output
    config_file = tmp_path / ".cursor" / "mcp.json"
    assert config_file.exists()
    config = json.loads(config_file.read_text())
    assert "tessera" in config["mcpServers"]
    assert config["mcpServers"]["tessera"]["url"] == "http://localhost:8080/mcp"
    assert config["mcpServers"]["tessera"]["headers"]["Authorization"] == "Bearer tk_test_xxxxxxxxxxxx"


def test_install_cursor_legacy_per_upstream(tmp_path: Path) -> None:
    """--legacy-per-upstream preserves v0.7.x behavior (per-upstream entry)."""
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
            "--token", "tk_test_xxxxxxxxxxxx",
            "--legacy-per-upstream",
        ],
    )
    assert result.exit_code == 0, result.output
    config_file = tmp_path / ".cursor" / "mcp.json"
    assert config_file.exists()
    config = json.loads(config_file.read_text())
    assert "github" in config["mcpServers"]
    assert config["mcpServers"]["github"]["url"] == "http://localhost:8080/mcp/github"
    assert config["mcpServers"]["github"]["headers"]["Authorization"] == "Bearer tk_test_xxxxxxxxxxxx"


def test_install_cursor_merges_existing(tmp_path: Path) -> None:
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    existing = {"mcpServers": {"other-server": {"url": "http://other:9000"}}}
    (cursor_dir / "mcp.json").write_text(json.dumps(existing))

    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads((cursor_dir / "mcp.json").read_text())
    assert "other-server" in config["mcpServers"]
    assert "tessera" in config["mcpServers"]


def test_install_cursor_refuses_overwrite_without_upgrade(tmp_path: Path) -> None:
    # First install writes unified entry
    runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
        ],
    )
    # Second install without --upgrade should fail
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
        ],
    )
    assert result.exit_code == 1
    assert "upgrade" in result.output.lower() or "upgrade" in (result.stderr or "").lower()


def test_install_cursor_upgrade_replaces_entry(tmp_path: Path) -> None:
    runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--token", "old_token",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:9090",
            "--token", "new_token",
            "--upgrade",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert config["mcpServers"]["tessera"]["url"] == "http://localhost:9090/mcp"
    assert config["mcpServers"]["tessera"]["headers"]["Authorization"] == "Bearer new_token"


def test_install_cursor_upgrade_migrates_legacy_entries(tmp_path: Path) -> None:
    """--upgrade removes old per-upstream entries and writes unified entry."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    legacy = {
        "mcpServers": {
            "aws": {"url": "http://localhost:8080/mcp/aws"},
            "github": {"url": "http://localhost:8080/mcp/github"},
        }
    }
    (cursor_dir / "mcp.json").write_text(json.dumps(legacy))

    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--upgrade",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads((cursor_dir / "mcp.json").read_text())
    # Unified entry written
    assert "tessera" in config["mcpServers"]
    # Legacy per-upstream entries removed
    assert "aws" not in config["mcpServers"]
    assert "github" not in config["mcpServers"]


# ---------------------------------------------------------------------------
# install-claude-desktop
# ---------------------------------------------------------------------------


def test_install_claude_desktop_writes_config(tmp_path: Path) -> None:
    """v0.8 default: unified entry at /mcp with key 'tessera'."""
    config_file = tmp_path / "claude_desktop_config.json"
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--token", "tk_test_xxxxxxxxxxxx",
        ],
    )
    assert result.exit_code == 0, result.output
    assert config_file.exists()
    config = json.loads(config_file.read_text())
    assert "tessera" in config["mcpServers"]
    assert config["mcpServers"]["tessera"]["url"] == "http://localhost:8080/mcp"
    assert config["mcpServers"]["tessera"]["headers"]["Authorization"] == "Bearer tk_test_xxxxxxxxxxxx"


def test_install_claude_desktop_legacy_per_upstream(tmp_path: Path) -> None:
    """--legacy-per-upstream preserves v0.7.x behavior."""
    config_file = tmp_path / "claude_desktop_config.json"
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
            "--token", "tk_test_xxxxxxxxxxxx",
            "--legacy-per-upstream",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads(config_file.read_text())
    assert "github" in config["mcpServers"]
    assert config["mcpServers"]["github"]["url"] == "http://localhost:8080/mcp/github"
    assert config["mcpServers"]["github"]["headers"]["Authorization"] == "Bearer tk_test_xxxxxxxxxxxx"


def test_install_claude_desktop_merges_existing(tmp_path: Path) -> None:
    config_file = tmp_path / "claude_desktop_config.json"
    existing = {"mcpServers": {"other-server": {"url": "http://other:9000"}}}
    config_file.write_text(json.dumps(existing))

    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads(config_file.read_text())
    assert "other-server" in config["mcpServers"]
    assert "tessera" in config["mcpServers"]


def test_install_claude_desktop_refuses_overwrite_without_upgrade(tmp_path: Path) -> None:
    config_file = tmp_path / "claude_desktop_config.json"
    runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
        ],
    )
    assert result.exit_code == 1
    assert "upgrade" in result.output.lower() or "upgrade" in (result.stderr or "").lower()


def test_install_claude_desktop_upgrade_replaces_entry(tmp_path: Path) -> None:
    config_file = tmp_path / "claude_desktop_config.json"
    runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--token", "old_token",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:9090",
            "--token", "new_token",
            "--upgrade",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads(config_file.read_text())
    assert config["mcpServers"]["tessera"]["url"] == "http://localhost:9090/mcp"
    assert config["mcpServers"]["tessera"]["headers"]["Authorization"] == "Bearer new_token"
