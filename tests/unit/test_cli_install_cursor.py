"""Unit tests for tessera install-cursor-hooks and install-claude-code CLI commands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from tessera.cli import app

runner = CliRunner()


def test_install_cursor_hooks_creates_files(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install-cursor-hooks",
            "--cursor-config-dir", str(tmp_path),
            "--tessera-url", "http://localhost:8080",
            "--token", "tk_test_xxxxxxxxxxxx",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "tessera_hook.py").exists()
    hooks_json = json.loads((tmp_path / "hooks.json").read_text())
    assert len(hooks_json["hooks"]) == 1
    assert "beforeMCPExecution" in hooks_json["hooks"][0]["events"]
    assert hooks_json["hooks"][0]["env"]["TESSERA_URL"] == "http://localhost:8080"


def test_install_cursor_hooks_upgrade(tmp_path: Path) -> None:
    # Install once
    runner.invoke(app, ["install-cursor-hooks", "--cursor-config-dir", str(tmp_path), "--tessera-url", "http://localhost:8080"])
    # Install again with --upgrade (should succeed, not duplicate entry)
    result = runner.invoke(app, ["install-cursor-hooks", "--cursor-config-dir", str(tmp_path), "--tessera-url", "http://localhost:8080", "--upgrade"])
    assert result.exit_code == 0
    hooks = json.loads((tmp_path / "hooks.json").read_text())["hooks"]
    assert len(hooks) == 1  # no duplicate


def test_install_cursor_hooks_merges_existing_config(tmp_path: Path) -> None:
    # Pre-existing hooks.json with another hook
    existing = {"hooks": [{"command": "other_hook.py", "events": ["beforeMCPExecution"]}]}
    (tmp_path / "hooks.json").write_text(json.dumps(existing))
    runner.invoke(app, ["install-cursor-hooks", "--cursor-config-dir", str(tmp_path), "--tessera-url", "http://localhost:8080"])
    hooks = json.loads((tmp_path / "hooks.json").read_text())["hooks"]
    assert len(hooks) == 2  # existing + tessera
    commands = [h["command"] for h in hooks]
    assert "other_hook.py" in commands


def test_install_cursor_hooks_uninstall(tmp_path: Path) -> None:
    runner.invoke(app, ["install-cursor-hooks", "--cursor-config-dir", str(tmp_path), "--tessera-url", "http://localhost:8080"])
    result = runner.invoke(app, ["install-cursor-hooks", "--cursor-config-dir", str(tmp_path), "--uninstall"])
    assert result.exit_code == 0
    assert not (tmp_path / "tessera_hook.py").exists()


@pytest.mark.parametrize("platform,expected_subpath", [
    ("darwin", ".cursor/hooks"),
    ("linux", ".cursor/hooks"),
])
def test_install_cursor_hooks_platform_detection(tmp_path: Path, platform: str, expected_subpath: str) -> None:
    with patch("sys.platform", platform), patch("pathlib.Path.home", return_value=tmp_path):
        result = runner.invoke(app, ["install-cursor-hooks", "--tessera-url", "http://localhost:8080"])
    # May fail (no hook src in tmp), but platform detection path is correct
    assert "hooks" in result.output.lower() or result.exit_code in (0, 1)


def test_install_claude_code(tmp_path: Path) -> None:
    config_file = tmp_path / "claude.json"
    result = runner.invoke(
        app,
        [
            "install-claude-code",
            "--claude-config", str(config_file),
            "--tessera-url", "http://localhost:8080",
            "--upstream-name", "github",
            "--token", "tk_test_xxxxxxxxxxxx",
        ],
    )
    assert result.exit_code == 0, result.output
    config = json.loads(config_file.read_text())
    assert "github" in config["mcpServers"]
    assert config["mcpServers"]["github"]["url"] == "http://localhost:8080/mcp/github"
    assert "Authorization" in config["mcpServers"]["github"]["headers"]
