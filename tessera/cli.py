"""Tessera CLI — full implementation."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import typer

from tessera.errors import ConfigError, PolicyError

app = typer.Typer(name="tessera", help="Tessera MCP firewall.", no_args_is_help=True)

# Sub-commands for audit
audit_app = typer.Typer(help="Audit management commands.")
app.add_typer(audit_app, name="audit")

# Sub-commands for policy
policy_app = typer.Typer(help="Policy management commands.")
app.add_typer(policy_app, name="policy")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    config: str = typer.Option(None, "--config", help="Path to tessera.yaml"),
    policy_dir: str = typer.Option(None, "--policy-dir"),
    bind: str = typer.Option(None, "--bind", help="host:port (e.g., 0.0.0.0:8080)"),
    log_level: str = typer.Option(None, "--log-level"),
) -> None:
    """Start the Tessera proxy server."""
    import uvicorn

    from tessera.config import load_config
    from tessera.proxy import create_app

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if policy_dir:
        cfg.policies.dir = policy_dir
    if bind:
        host, port_str = bind.rsplit(":", 1)
        cfg.listen.host = host
        try:
            cfg.listen.port = int(port_str)
        except ValueError:
            typer.echo(f"Invalid --bind port: {port_str!r}", err=True)
            raise typer.Exit(2) from None
    if log_level:
        cfg.log_level = log_level

    try:
        application = create_app(cfg)
    except (ConfigError, PolicyError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    uvicorn.run(
        application,
        host=cfg.listen.host,
        port=cfg.listen.port,
        log_level=cfg.log_level.lower(),
    )


# ---------------------------------------------------------------------------
# audit verify
# ---------------------------------------------------------------------------


@audit_app.command("verify")
def audit_verify(
    audit_path: str = typer.Option("/var/lib/tessera/audit.db", "--audit-path"),
    scope: str = typer.Option(None, "--scope"),
    all_scopes: bool = typer.Option(False, "--all"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Verify the audit hash chain integrity."""
    from tessera.audit.sinks.sqlite import SqliteSink
    from tessera.audit.verifier import verify_chain

    db_path = Path(audit_path)
    if not db_path.exists():
        # An absent DB is treated as empty — 0 events, chain ok.
        results: list[dict[str, Any]] = [
            {
                "scope": scope or "default",
                "events_checked": 0,
                "first_event_at": None,
                "last_event_at": None,
                "ok": True,
                "first_failure": None,
            }
        ]
        if json_output:
            typer.echo(json.dumps(results))
        else:
            typer.echo(f"scope={results[0]['scope']}  events=0  ok=true")
        return

    sink = SqliteSink(audit_path)

    try:
        if all_scopes:
            # Query distinct scopes from the DB
            conn = sqlite3.connect(audit_path)
            rows = conn.execute("SELECT DISTINCT scope FROM audit_events").fetchall()
            conn.close()
            scopes = [row[0] for row in rows] if rows else ["default"]
        elif scope:
            scopes = [scope]
        else:
            scopes = ["default"]

        results = [verify_chain(sink, s) for s in scopes]
    finally:
        sink.close()

    if json_output:
        typer.echo(json.dumps(results))
    else:
        for r in results:
            status = "ok" if r["ok"] else "FAILED"
            typer.echo(f"scope={r['scope']}  events={r['events_checked']}  status={status}")
            if not r["ok"] and r["first_failure"]:
                f = r["first_failure"]
                typer.echo(
                    f"  first_failure: seq={f['seq']} event_id={f['event_id']} kind={f['kind']}",
                    err=True,
                )

    any_failed = any(not r["ok"] for r in results)
    if any_failed:
        raise typer.Exit(3)


# ---------------------------------------------------------------------------
# policy test
# ---------------------------------------------------------------------------


@policy_app.command("test")
def policy_test(
    policy_dir: str = typer.Option("policies/", "--policy-dir"),
    fixture_dir: str = typer.Option(None, "--fixture-dir"),
    fixture: str = typer.Option(None, "--fixture"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run fixture decisions against loaded policies."""
    from tessera.policy.engine import PolicyEngine
    from tessera.policy.loader import FilesystemPolicyLoader
    from tessera.policy.schema import Action

    # Load policies
    try:
        loader = FilesystemPolicyLoader(policy_dir)
        policies = loader.load_all("default")
    except PolicyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    engine = PolicyEngine(policies, default_action=Action.allow)

    # Gather fixture files
    fixture_files: list[Path] = []
    if fixture:
        p = Path(fixture)
        if not p.exists():
            typer.echo(f"Fixture file not found: {fixture}", err=True)
            raise typer.Exit(2)
        fixture_files.append(p)
    elif fixture_dir:
        d = Path(fixture_dir)
        if not d.is_dir():
            typer.echo(f"Fixture directory not found: {fixture_dir}", err=True)
            raise typer.Exit(2)
        fixture_files = sorted(d.glob("*.json"))
    else:
        typer.echo("Provide --fixture or --fixture-dir.", err=True)
        raise typer.Exit(2)

    if not fixture_files:
        if json_output:
            typer.echo(json.dumps([]))
        else:
            typer.echo("No fixture files found.")
        return

    results: list[dict[str, Any]] = []
    any_fail = False

    for fpath in fixture_files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as exc:
            typer.echo(f"Failed to parse fixture {fpath.name}: {exc}", err=True)
            raise typer.Exit(2) from exc

        context: dict[str, Any] = data.get("input", {})
        expected_outcome: str = data.get("expected", {}).get("outcome", "allow")

        decision = engine.evaluate(context)
        actual = decision.action.value
        passed = actual == expected_outcome

        if not passed:
            any_fail = True

        results.append(
            {
                "fixture": fpath.name,
                "expected": expected_outcome,
                "actual": actual,
                "passed": passed,
            }
        )

    if json_output:
        typer.echo(json.dumps(results))
    else:
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            typer.echo(f"[{mark}] {r['fixture']}  expected={r['expected']}  actual={r['actual']}")

    if any_fail:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# policy lint
# ---------------------------------------------------------------------------


@policy_app.command("lint")
def policy_lint(
    policy_dir: str = typer.Option("policies/", "--policy-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Validate YAML policies and run regex safety checks."""
    from tessera.policy.loader import FilesystemPolicyLoader

    loader = FilesystemPolicyLoader(policy_dir)

    try:
        loader.load_all("default")
    except PolicyError as exc:
        if json_output:
            typer.echo(json.dumps({"ok": False, "errors": [str(exc)]}))
        else:
            typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(2) from exc

    state = loader.state()
    errors = state.get("errored", [])

    if errors:
        if json_output:
            typer.echo(json.dumps({"ok": False, "loaded": state["loaded"], "errors": errors}))
        else:
            for e in errors:
                typer.echo(f"ERROR: {e['path']}: {e['error']}", err=True)
        raise typer.Exit(2)

    if json_output:
        typer.echo(json.dumps({"ok": True, "loaded": state["loaded"], "errors": []}))
    else:
        typer.echo(f"OK — {state['loaded']} policy file(s) loaded, 0 errors.")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command()
def version(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show version information."""
    import platform

    from tessera import __version__

    info = {
        "version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
    }
    if json_output:
        typer.echo(json.dumps(info))
    else:
        typer.echo(
            f"tessera {__version__} "
            f"(Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro})"
        )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

_TESSERA_YAML_TEMPLATE = """\
# Tessera configuration — generated by `tessera init`
listen:
  host: 0.0.0.0
  port: 8080

auth:
  type: bearer

audit:
  sink: sqlite
  path: /var/lib/tessera/audit.db
  also_stdout: false

policies:
  dir: policies/
  reload: watch
  # mode: log_only — calls are forwarded regardless of policy decisions;
  # decisions are logged. Change to `enforcement` when you are ready to block.
  mode: log_only
  default_action: block

intent:
  meta_key: tessera_intent
  required: false

metrics:
  enabled: false

deployment_id: default

upstreams: []

runtime:
  lockdown: false
"""

_ENV_EXAMPLE = """\
# Copy to .env and fill in values.
# TESSERA_BEARER_TOKENS=alice:tk_change_me
# TESSERA_CONFIG_PATH=/etc/tessera/tessera.yaml
# TESSERA_LOG_LEVEL=INFO
"""


@app.command()
def init(
    target_dir: str = typer.Option(".", "--dir"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Scaffold starter tessera.yaml, policies/, and .env.example."""
    base = Path(target_dir)
    base.mkdir(parents=True, exist_ok=True)

    tessera_yaml = base / "tessera.yaml"
    env_example = base / ".env.example"
    policies_dir = base / "policies"

    skipped: list[str] = []

    def _write_if_allowed(path: Path, content: str) -> None:
        if path.exists() and not force:
            skipped.append(str(path))
            typer.echo(f"Skipping existing file: {path} (use --force to overwrite)")
            return
        path.write_text(content, encoding="utf-8")
        typer.echo(f"Created: {path}")

    _write_if_allowed(tessera_yaml, _TESSERA_YAML_TEMPLATE)
    _write_if_allowed(env_example, _ENV_EXAMPLE)

    if not policies_dir.exists():
        policies_dir.mkdir(parents=True, exist_ok=True)
        typer.echo(f"Created: {policies_dir}/")
    elif not force:
        typer.echo(f"Skipping existing directory: {policies_dir}/")

    if skipped:
        typer.echo(f"\n{len(skipped)} file(s) skipped. Re-run with --force to overwrite.")


# ---------------------------------------------------------------------------
# install-cursor-hooks
# ---------------------------------------------------------------------------


@app.command("install-cursor-hooks")
def install_cursor_hooks(
    cursor_config_dir: str = typer.Option(
        None,
        "--cursor-config-dir",
        help="Override autodetect of Cursor config directory.",
    ),
    tessera_url: str = typer.Option(
        "http://localhost:8080",
        "--tessera-url",
        help="Tessera proxy URL.",
    ),
    token: str = typer.Option(
        "",
        "--token",
        envvar="TESSERA_BEARER_TOKEN",
        help="Bearer token for Tessera. Reads TESSERA_BEARER_TOKEN if not provided.",
    ),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove Tessera hooks."),
    upgrade: bool = typer.Option(False, "--upgrade", help="Overwrite existing hook file."),
) -> None:
    """Install (or uninstall) Tessera's Cursor Hooks integration."""
    import shutil

    # Detect config dir
    if cursor_config_dir:
        config_dir = Path(cursor_config_dir)
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        config_dir = Path(appdata) / "Cursor" / "hooks"
    else:
        config_dir = Path.home() / ".cursor" / "hooks"

    config_dir.mkdir(parents=True, exist_ok=True)

    hook_dest = config_dir / "tessera_hook.py"
    hooks_json_path = config_dir / "hooks.json"

    # Uninstall path
    if uninstall:
        if hook_dest.exists():
            hook_dest.unlink()
            typer.echo(f"Removed {hook_dest}")
        if hooks_json_path.exists():
            data = json.loads(hooks_json_path.read_text(encoding="utf-8"))
            hooks = data.get("hooks", [])
            hooks = [h for h in hooks if h.get("command") != str(hook_dest)]
            data["hooks"] = hooks
            hooks_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            typer.echo(f"Removed tessera entry from {hooks_json_path}")
        typer.echo("Tessera Cursor Hooks uninstalled.")
        return

    # Copy hook script
    hook_src = Path(__file__).parent / "integrations" / "cursor_hooks.py"
    if not hook_src.exists():
        typer.echo(f"Hook script not found: {hook_src}", err=True)
        raise typer.Exit(1)

    if hook_dest.exists() and not upgrade:
        typer.echo(f"Hook already exists at {hook_dest}. Use --upgrade to overwrite.")
    else:
        shutil.copy2(hook_src, hook_dest)
        typer.echo(f"Installed hook script at {hook_dest}")

    # Write/merge hooks.json
    env: dict[str, str] = {"TESSERA_URL": tessera_url}
    if token:
        env["TESSERA_BEARER_TOKEN"] = token

    tessera_hook_entry = {
        "command": str(hook_dest),
        "events": ["beforeMCPExecution", "afterMCPExecution"],
        "env": env,
    }

    if hooks_json_path.exists():
        existing = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        hooks_list = existing.get("hooks", [])
        # Remove existing tessera entry to avoid duplicates
        hooks_list = [h for h in hooks_list if h.get("command") != str(hook_dest)]
        hooks_list.append(tessera_hook_entry)
        existing["hooks"] = hooks_list
        hooks_json_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    else:
        hooks_json_path.write_text(
            json.dumps({"hooks": [tessera_hook_entry]}, indent=2),
            encoding="utf-8",
        )

    typer.echo(f"Updated {hooks_json_path}")
    typer.echo(f"Tessera Cursor Hooks installed. URL: {tessera_url}")


# ---------------------------------------------------------------------------
# install-claude-code
# ---------------------------------------------------------------------------


@app.command("install-claude-code")
def install_claude_code(
    tessera_url: str = typer.Option(
        "http://localhost:8080",
        "--tessera-url",
        help="Tessera proxy URL.",
    ),
    token: str = typer.Option(
        "",
        "--token",
        envvar="TESSERA_BEARER_TOKEN",
        help="Bearer token for Tessera.",
    ),
    upstream_name: str = typer.Option(
        "github",
        "--upstream-name",
        help="MCP upstream name to configure in Claude Code.",
    ),
    claude_config_path: str = typer.Option(
        None,
        "--claude-config",
        help="Override path to ~/.claude.json",
    ),
    upgrade: bool = typer.Option(
        False,
        "--upgrade",
        help="Replace existing entry for this upstream. Without --upgrade, refuses to overwrite.",
    ),
) -> None:
    """Configure Claude Code to use Tessera as MCP proxy via ~/.claude.json."""
    # Detect config file
    if claude_config_path:
        config_file = Path(claude_config_path)
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        config_file = Path(appdata) / "Claude" / "claude.json"
    else:
        config_file = Path.home() / ".claude.json"

    # Load or create config
    if config_file.exists():
        config: dict[str, Any] = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        config = {}

    mcp_servers: dict[str, Any] = config.setdefault("mcpServers", {})

    # Per A-4-8: refuse to overwrite an existing entry unless --upgrade is passed.
    if upstream_name in mcp_servers and not upgrade:
        typer.echo(
            f"ERROR: ~/.claude.json already has an mcpServers entry for '{upstream_name}'. "
            "Pass --upgrade to replace it.",
            err=True,
        )
        raise typer.Exit(code=1)

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    mcp_servers[upstream_name] = {
        "url": f"{tessera_url}/mcp/{upstream_name}",
        **({"headers": headers} if headers else {}),
    }

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

    typer.echo(f"Updated {config_file}")
    typer.echo(f"Claude Code → Tessera proxy configured for upstream '{upstream_name}'")
    typer.echo(f"URL: {tessera_url}/mcp/{upstream_name}")


if __name__ == "__main__":
    app()
