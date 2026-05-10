"""CLI unit tests using typer.testing.CliRunner."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tessera.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_version_json() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["version"] == "0.1.0"
    assert "python" in data
    assert "platform" in data


# ---------------------------------------------------------------------------
# policy lint
# ---------------------------------------------------------------------------


def test_policy_lint_valid_dir() -> None:
    """Lint against the real policies/ dir — all reference policies should pass."""
    result = runner.invoke(app, ["policy", "lint", "--policy-dir", "policies/"])
    assert result.exit_code == 0


def test_policy_lint_invalid_yaml(tmp_path: Path) -> None:
    """A malformed YAML file should cause exit 2."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: [unclosed bracket\nname: Bad", encoding="utf-8")
    result = runner.invoke(app, ["policy", "lint", "--policy-dir", str(tmp_path)])
    assert result.exit_code == 2


def test_policy_lint_empty_dir(tmp_path: Path) -> None:
    """Empty policies directory — no files to fail; exit 0."""
    result = runner.invoke(app, ["policy", "lint", "--policy-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_policy_lint_json_output_valid(tmp_path: Path) -> None:
    """--json flag produces parseable output on success."""
    policy = textwrap.dedent("""\
        id: test-allow
        name: Test Allow
        action: allow
        priority: 0
    """)
    (tmp_path / "test-allow.yaml").write_text(policy, encoding="utf-8")
    result = runner.invoke(
        app, ["policy", "lint", "--policy-dir", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["loaded"] == 1


def test_policy_lint_json_output_invalid(tmp_path: Path) -> None:
    """--json flag produces parseable error output on failure."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "id: test\nname: Test\naction: not_a_valid_action\n", encoding="utf-8"
    )
    result = runner.invoke(
        app, ["policy", "lint", "--policy-dir", str(tmp_path), "--json"]
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# audit verify
# ---------------------------------------------------------------------------


def test_audit_verify_missing_db(tmp_path: Path) -> None:
    """Non-existent DB is treated as empty — exit 0."""
    db_path = tmp_path / "audit.db"
    result = runner.invoke(app, ["audit", "verify", "--audit-path", str(db_path)])
    assert result.exit_code == 0


def test_audit_verify_empty_db(tmp_path: Path) -> None:
    """Empty SqliteSink (created but no events) — exit 0."""
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = tmp_path / "audit.db"
    sink = SqliteSink(db_path)
    sink.close()

    result = runner.invoke(app, ["audit", "verify", "--audit-path", str(db_path)])
    assert result.exit_code == 0


def test_audit_verify_empty_db_json(tmp_path: Path) -> None:
    """Empty DB with --json returns a JSON array."""
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = tmp_path / "audit.db"
    sink = SqliteSink(db_path)
    sink.close()

    result = runner.invoke(
        app, ["audit", "verify", "--audit-path", str(db_path), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["ok"] is True


def test_audit_verify_all_flag_empty_db(tmp_path: Path) -> None:
    """--all on an empty DB exits 0."""
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = tmp_path / "audit.db"
    sink = SqliteSink(db_path)
    sink.close()

    result = runner.invoke(
        app, ["audit", "verify", "--audit-path", str(db_path), "--all"]
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_files(tmp_path: Path) -> None:
    """init --dir=tmp_path creates tessera.yaml, .env.example, and policies/."""
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0

    tessera_yaml = tmp_path / "tessera.yaml"
    env_example = tmp_path / ".env.example"
    policies_dir = tmp_path / "policies"

    assert tessera_yaml.exists(), "tessera.yaml was not created"
    assert env_example.exists(), ".env.example was not created"
    assert policies_dir.is_dir(), "policies/ directory was not created"


def test_init_mode_is_log_only(tmp_path: Path) -> None:
    """The scaffolded tessera.yaml must contain mode: log_only."""
    runner.invoke(app, ["init", "--dir", str(tmp_path)])
    content = (tmp_path / "tessera.yaml").read_text(encoding="utf-8")
    assert "mode: log_only" in content


def test_init_no_overwrite_by_default(tmp_path: Path) -> None:
    """A second init without --force should NOT overwrite existing tessera.yaml."""
    runner.invoke(app, ["init", "--dir", str(tmp_path)])
    # Mutate the file
    tessera_yaml = tmp_path / "tessera.yaml"
    tessera_yaml.write_text("# mutated content\n", encoding="utf-8")

    runner.invoke(app, ["init", "--dir", str(tmp_path)])
    # File should still be the mutated version
    assert tessera_yaml.read_text(encoding="utf-8") == "# mutated content\n"


def test_init_force_overwrites(tmp_path: Path) -> None:
    """--force causes tessera.yaml to be overwritten."""
    runner.invoke(app, ["init", "--dir", str(tmp_path)])
    tessera_yaml = tmp_path / "tessera.yaml"
    tessera_yaml.write_text("# mutated content\n", encoding="utf-8")

    runner.invoke(app, ["init", "--dir", str(tmp_path), "--force"])
    content = tessera_yaml.read_text(encoding="utf-8")
    assert "mode: log_only" in content


# ---------------------------------------------------------------------------
# policy test
# ---------------------------------------------------------------------------


def _write_policy(directory: Path, policy_id: str, action: str = "allow") -> None:
    content = textwrap.dedent(f"""\
        id: {policy_id}
        name: {policy_id}
        match:
          upstream: "*"
          tool: "test_tool"
        action: {action}
        priority: 10
    """)
    (directory / f"{policy_id}.yaml").write_text(content, encoding="utf-8")


def _write_fixture(directory: Path, name: str, expected_outcome: str) -> None:
    data = {
        "name": name,
        "input": {
            "tool_call": {"name": "test_tool", "arguments": {}},
            "runtime": {"lockdown": False},
            "upstream": "any",
        },
        "expected": {"outcome": expected_outcome},
    }
    (directory / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_policy_test_pass(tmp_path: Path) -> None:
    """Fixture that matches the policy decision exits 0."""
    policy_dir = tmp_path / "policies"
    fixture_dir = tmp_path / "fixtures"
    policy_dir.mkdir()
    fixture_dir.mkdir()

    _write_policy(policy_dir, "test-allow", action="allow")
    _write_fixture(fixture_dir, "01_pass", expected_outcome="allow")

    result = runner.invoke(
        app,
        [
            "policy",
            "test",
            "--policy-dir",
            str(policy_dir),
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_policy_test_fail(tmp_path: Path) -> None:
    """Fixture that doesn't match the policy decision exits 1."""
    policy_dir = tmp_path / "policies"
    fixture_dir = tmp_path / "fixtures"
    policy_dir.mkdir()
    fixture_dir.mkdir()

    _write_policy(policy_dir, "test-allow", action="allow")
    # Fixture expects block but policy returns allow → FAIL
    _write_fixture(fixture_dir, "01_fail", expected_outcome="block")

    result = runner.invoke(
        app,
        [
            "policy",
            "test",
            "--policy-dir",
            str(policy_dir),
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_policy_test_json_output_pass(tmp_path: Path) -> None:
    """--json on a passing run returns JSON with passed=True."""
    policy_dir = tmp_path / "policies"
    fixture_dir = tmp_path / "fixtures"
    policy_dir.mkdir()
    fixture_dir.mkdir()

    _write_policy(policy_dir, "test-allow", action="allow")
    _write_fixture(fixture_dir, "01_pass", expected_outcome="allow")

    result = runner.invoke(
        app,
        [
            "policy",
            "test",
            "--policy-dir",
            str(policy_dir),
            "--fixture-dir",
            str(fixture_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["passed"] is True


def test_policy_test_single_fixture_file(tmp_path: Path) -> None:
    """--fixture (single file) works correctly."""
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    _write_policy(policy_dir, "test-allow", action="allow")

    fixture_path = tmp_path / "single.json"
    _write_fixture(tmp_path, "single", expected_outcome="allow")

    result = runner.invoke(
        app,
        [
            "policy",
            "test",
            "--policy-dir",
            str(policy_dir),
            "--fixture",
            str(fixture_path),
        ],
    )
    assert result.exit_code == 0
