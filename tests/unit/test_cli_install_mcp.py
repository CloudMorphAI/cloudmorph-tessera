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
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
            "--token", "tk_test_xxxxxxxxxxxx",
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
            "--upstream-name", "github",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads((cursor_dir / "mcp.json").read_text())
    assert "other-server" in config["mcpServers"]
    assert "github" in config["mcpServers"]


def test_install_cursor_refuses_overwrite_without_upgrade(tmp_path: Path) -> None:
    runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
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
            "--upstream-name", "github",
            "--token", "old_token",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-cursor",
            "--project-dir", str(tmp_path),
            "--tessera-url", "http://localhost:9090",
            "--upstream-name", "github",
            "--token", "new_token",
            "--upgrade",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert config["mcpServers"]["github"]["url"] == "http://localhost:9090/mcp/github"
    assert config["mcpServers"]["github"]["headers"]["Authorization"] == "Bearer new_token"


# ---------------------------------------------------------------------------
# install-claude-desktop
# ---------------------------------------------------------------------------


def test_install_claude_desktop_writes_config(tmp_path: Path) -> None:
    config_file = tmp_path / "claude_desktop_config.json"
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
            "--token", "tk_test_xxxxxxxxxxxx",
        ],
    )
    assert result.exit_code == 0, result.output
    assert config_file.exists()
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
            "--upstream-name", "github",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads(config_file.read_text())
    assert "other-server" in config["mcpServers"]
    assert "github" in config["mcpServers"]


def test_install_claude_desktop_refuses_overwrite_without_upgrade(tmp_path: Path) -> None:
    config_file = tmp_path / "claude_desktop_config.json"
    runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
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
            "--upstream-name", "github",
            "--token", "old_token",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install-claude-desktop",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:9090",
            "--upstream-name", "github",
            "--token", "new_token",
            "--upgrade",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads(config_file.read_text())
    assert config["mcpServers"]["github"]["url"] == "http://localhost:9090/mcp/github"
    assert config["mcpServers"]["github"]["headers"]["Authorization"] == "Bearer new_token"
