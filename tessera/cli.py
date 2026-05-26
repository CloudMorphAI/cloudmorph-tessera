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

# Sub-commands for pricing
pricing_app = typer.Typer(help="Pricing backend management.")
app.add_typer(pricing_app, name="pricing")

# Sub-commands for config (v0.7.0 Item D §7.5 — `tessera config sync`)
config_app = typer.Typer(help="Configuration + cloud-sync commands.")
app.add_typer(config_app, name="config")


# ── v0.7.0 Item D §7.5 — OAuth login + token storage helpers ────────────────


_OAUTH_TOKEN_DIR = Path.home() / ".tessera"
_OAUTH_TOKEN_FILE = _OAUTH_TOKEN_DIR / "oauth.json"
# v0.7.2: auth.tessera.cloudmorph.ai is the ApiMapping for the OAuth Lambda
# on the tessera-api-prod HttpApi. Older versions defaulted to
# https://tessera.cloudmorph.ai which routes to the ECS ALB (a different
# product surface) — that default never worked for OAuth.
_OAUTH_DEFAULT_ISSUER = "https://auth.tessera.cloudmorph.ai"
_OAUTH_DEFAULT_CLIENT_ID = "tessera-cli"
_OAUTH_DEFAULT_SCOPE = "tessera:policies:read tessera:audit:write"


def _save_oauth_tokens(payload: dict) -> Path:
    """Persist OAuth tokens to ~/.tessera/oauth.json with mode 0600."""
    _OAUTH_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    # Write to a tmpfile + rename to avoid leaving a half-written file on crash.
    tmp = _OAUTH_TOKEN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        # Windows ignores chmod; non-fatal.
        pass
    tmp.replace(_OAUTH_TOKEN_FILE)
    return _OAUTH_TOKEN_FILE


def _load_oauth_tokens() -> dict | None:
    if not _OAUTH_TOKEN_FILE.exists():
        return None
    try:
        return json.loads(_OAUTH_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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
# audit tail
# ---------------------------------------------------------------------------


@audit_app.command("tail")
def audit_tail(
    audit_path: str = typer.Option("/var/lib/tessera/audit.db", "--audit-path"),
    scope: str = typer.Option(None, "--scope"),
    follow: bool = typer.Option(False, "--follow", help="Poll for new events."),
    n: int = typer.Option(20, "-n", "--limit", help="Number of recent events to show."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Print recent audit events; --follow polls for new ones."""
    from tessera.audit.inspect import tail_events
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = Path(audit_path)
    if not db_path.exists():
        typer.echo("No audit database found at: " + audit_path, err=True)
        raise typer.Exit(1)

    sink = SqliteSink(audit_path)
    try:
        for event in tail_events(sink, scope=scope, limit=n, follow=follow):
            if json_output:
                typer.echo(json.dumps(event))
            else:
                occurred_at = event.get("occurredAt", "")
                event_id = event.get("eventId", "")
                event_type = event.get("eventType", "")
                tenant = event.get("tenantId", "")
                payload = event.get("payload", {})
                detail = ""
                if event_type == "decision":
                    detail = (
                        f"policy={payload.get('policy_id', '-')} "
                        f"reason={payload.get('reason', '-')}"
                    )
                elif event_type == "passthrough":
                    detail = f"method={payload.get('method', '-')}"
                elif event_type == "intent_derivation":
                    detail = f"principal={payload.get('principal_id', '-')}"
                typer.echo(
                    f"[{occurred_at}] {event_id} scope={tenant}  {event_type:<30} {detail}"
                )
    except KeyboardInterrupt:
        pass
    finally:
        sink.close()


# ---------------------------------------------------------------------------
# audit verify-chain
# ---------------------------------------------------------------------------


@audit_app.command("verify-chain")
def audit_verify_chain(
    audit_path: str = typer.Option("/var/lib/tessera/audit.db", "--audit-path"),
    scope: str = typer.Option(None, "--scope"),
    all_scopes: bool = typer.Option(False, "--all"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Walk the audit hash chain and print first broken link or OK."""
    from tessera.audit.sinks.sqlite import SqliteSink
    from tessera.audit.verifier import verify_chain

    db_path = Path(audit_path)
    if not db_path.exists():
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
            scopes = list(sink.iter_scopes()) or ["default"]
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
            typer.echo(
                f"scope={r['scope']}  events={r['events_checked']}  status={status}"
            )
            if r.get("first_event_at"):
                typer.echo(f"  first event: {r['first_event_at']}")
            if r.get("last_event_at"):
                typer.echo(f"  last event:  {r['last_event_at']}")
            if not r["ok"] and r.get("first_failure"):
                f = r["first_failure"]
                typer.echo(
                    f"  FAILED at seq={f['seq']} event_id={f['event_id']}", err=True
                )
                typer.echo(
                    f"  kind: {f['kind']}", err=True
                )
                typer.echo(
                    f"  expected: {f.get('expected_event_hash', '')}", err=True
                )
                typer.echo(
                    f"  computed: {f.get('computed_event_hash', '')}", err=True
                )

    any_failed = any(not r["ok"] for r in results)
    if any_failed:
        raise typer.Exit(3)


# ---------------------------------------------------------------------------
# audit export
# ---------------------------------------------------------------------------


@audit_app.command("export")
def audit_export(
    audit_path: str = typer.Option("/var/lib/tessera/audit.db", "--audit-path"),
    scope: str = typer.Option(None, "--scope"),
    fmt: str = typer.Option("jsonl", "--format", help="jsonl or csv"),
    output: str = typer.Option("-", "--output", help="Output path or - for stdout"),
) -> None:
    """Bulk export the audit log in JSONL or CSV format."""
    from tessera.audit.inspect import export_csv, export_jsonl
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = Path(audit_path)
    if not db_path.exists():
        typer.echo("No audit database found at: " + audit_path, err=True)
        raise typer.Exit(1)

    if fmt not in ("jsonl", "csv"):
        typer.echo(f"Unknown format: {fmt!r}. Choose jsonl or csv.", err=True)
        raise typer.Exit(2)

    sink = SqliteSink(audit_path)
    try:
        rows = export_jsonl(sink, scope=scope) if fmt == "jsonl" else export_csv(sink, scope=scope)
        if output == "-":
            for row in rows:
                typer.echo(row, nl=fmt == "jsonl")
        else:
            with open(output, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(row)
                    if fmt == "jsonl":
                        fh.write("\n")
            typer.echo(f"Exported to {output}")
    finally:
        sink.close()


# ---------------------------------------------------------------------------
# audit inspect
# ---------------------------------------------------------------------------


@audit_app.command("inspect")
def audit_inspect(
    event_id: str = typer.Argument(..., help="Event ID to inspect (evt_...)"),
    audit_path: str = typer.Option("/var/lib/tessera/audit.db", "--audit-path"),
) -> None:
    """Fetch and pretty-print a single audit event by ID."""
    from tessera.audit.inspect import fetch_event_by_id
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = Path(audit_path)
    if not db_path.exists():
        typer.echo("No audit database found at: " + audit_path, err=True)
        raise typer.Exit(1)

    sink = SqliteSink(audit_path)
    try:
        event = fetch_event_by_id(sink, event_id)
    finally:
        sink.close()

    if event is None:
        typer.echo(f"Event not found: {event_id}", err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps(event, indent=2))


# ---------------------------------------------------------------------------
# policy test
# ---------------------------------------------------------------------------


@policy_app.command("test")
def policy_test(
    policy_dir: str = typer.Option("policies/", "--policy-dir"),
    fixture_dir: str | None = typer.Option(None, "--fixture-dir"),
    fixture: str | None = typer.Option(None, "--fixture"),
    json_output: bool = typer.Option(False, "--json"),
    default_action: str | None = typer.Option(
        None,
        "--default-action",
        help="Default action when no policy matches: allow|block|log_only|require_approval. "
        "Defaults to 'allow' for policy test; production server typically defaults 'block'.",
    ),
) -> None:
    """Run fixture decisions against loaded policies."""
    from tessera.policy.engine import PolicyEngine
    from tessera.policy.loader import FilesystemPolicyLoader
    from tessera.policy.schema import Action

    # Warn if --default-action not explicitly set (production usually defaults block).
    # Suppress in --json mode so the stdout stream stays parseable.
    if default_action is None:
        if not json_output:
            typer.echo(
                'WARN: --default-action defaults to "allow"; production server typically defaults "block". '
                'Pass --default-action block to match production behavior.',
                err=True,
            )
        _resolved_default_action = Action.allow
    else:
        valid_actions = {"allow", "block", "log_only", "require_approval"}
        if default_action not in valid_actions:
            typer.echo(
                f"Invalid --default-action {default_action!r}. Choose from: {', '.join(sorted(valid_actions))}",
                err=True,
            )
            raise typer.Exit(2)
        _resolved_default_action = Action(default_action)

    # Load policies
    try:
        loader = FilesystemPolicyLoader(policy_dir)
        policies = loader.load_all("default")
    except PolicyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    engine = PolicyEngine(policies, default_action=_resolved_default_action)

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
    token_name: str = typer.Option(
        None,
        "--token-name",
        help="Named token to select from TESSERA_BEARER_TOKENS or TESSERA_BEARER_TOKENS_FILE. "
        "Injected as TESSERA_CURSOR_TOKEN_NAME in hook env.",
    ),
    fail_closed: bool = typer.Option(
        False,
        "--fail-closed",
        help="When Tessera is unreachable, deny the MCP call instead of failing open.",
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

    # Build hook env dict — propagate multi-token env vars from caller's shell.
    env: dict[str, str] = {"TESSERA_URL": tessera_url}

    # Multi-token propagation: pass through whichever token source is active in
    # the caller's environment so the hook resolves tokens the same way.
    if os.environ.get("TESSERA_BEARER_TOKENS"):
        env["TESSERA_BEARER_TOKENS"] = os.environ["TESSERA_BEARER_TOKENS"]
    elif os.environ.get("TESSERA_BEARER_TOKENS_FILE"):
        env["TESSERA_BEARER_TOKENS_FILE"] = os.environ["TESSERA_BEARER_TOKENS_FILE"]
    elif token:
        env["TESSERA_BEARER_TOKEN"] = token

    if token_name:
        env["TESSERA_CURSOR_TOKEN_NAME"] = token_name

    if fail_closed:
        env["TESSERA_CURSOR_FAIL_CLOSED"] = "true"

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


# ---------------------------------------------------------------------------
# install-cursor
# ---------------------------------------------------------------------------


@app.command("install-cursor")
def install_cursor(
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
        help="MCP upstream name to configure in Cursor.",
    ),
    project_dir: str = typer.Option(
        None,
        "--project-dir",
        help="Project directory to write .cursor/mcp.json into (default: current working directory).",
    ),
    upgrade: bool = typer.Option(
        False,
        "--upgrade",
        help="Replace existing entry for this upstream. Without --upgrade, refuses to overwrite.",
    ),
) -> None:
    """Configure Cursor to use Tessera as MCP server via .cursor/mcp.json in the project directory."""
    base_dir = Path(project_dir) if project_dir else Path.cwd()
    cursor_dir = base_dir / ".cursor"
    config_file = cursor_dir / "mcp.json"

    if config_file.exists():
        config: dict[str, Any] = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        config = {}

    mcp_servers: dict[str, Any] = config.setdefault("mcpServers", {})

    if upstream_name in mcp_servers and not upgrade:
        typer.echo(
            f"ERROR: {config_file} already has an mcpServers entry for '{upstream_name}'. "
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

    cursor_dir.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

    typer.echo(f"Updated {config_file}")
    typer.echo(f"Cursor → Tessera proxy configured for upstream '{upstream_name}'")
    typer.echo(f"URL: {tessera_url}/mcp/{upstream_name}")


# ---------------------------------------------------------------------------
# install-claude-desktop
# ---------------------------------------------------------------------------


@app.command("install-claude-desktop")
def install_claude_desktop(
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
        help="MCP upstream name to configure in Claude Desktop.",
    ),
    claude_config_path: str = typer.Option(
        None,
        "--claude-config",
        help="Override path to claude_desktop_config.json.",
    ),
    upgrade: bool = typer.Option(
        False,
        "--upgrade",
        help="Replace existing entry for this upstream. Without --upgrade, refuses to overwrite.",
    ),
) -> None:
    """Configure Claude Desktop to use Tessera as MCP server via claude_desktop_config.json."""
    if claude_config_path:
        config_file = Path(claude_config_path)
    elif sys.platform == "darwin":
        config_file = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        config_file = Path(appdata) / "Claude" / "claude_desktop_config.json"
    else:
        config_file = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

    if config_file.exists():
        config: dict[str, Any] = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        config = {}

    mcp_servers: dict[str, Any] = config.setdefault("mcpServers", {})

    if upstream_name in mcp_servers and not upgrade:
        typer.echo(
            f"ERROR: {config_file} already has an mcpServers entry for '{upstream_name}'. "
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
    typer.echo(f"Claude Desktop → Tessera proxy configured for upstream '{upstream_name}'")
    typer.echo(f"URL: {tessera_url}/mcp/{upstream_name}")


# ---------------------------------------------------------------------------
# policy author
# ---------------------------------------------------------------------------


@policy_app.command("author")
def policy_author(
    intent: str = typer.Option(..., "--intent", help="Free-text customer intent"),
    model: str = typer.Option("gemini", "--model", help="LLM provider: gemini|anthropic|openai|bedrock|azure-openai|mistral|cohere"),
    output: str = typer.Option("-", "--output", help="- for stdout, or directory path to write files"),
) -> None:
    """Generate draft policies from natural-language intent. Human review required."""
    provider = _resolve_llm_provider(model)

    try:
        recommendations = provider.propose_policies(intent)
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not recommendations:
        typer.echo("# Generated draft — review before deploying.", err=True)
        typer.echo("No policies generated.", err=True)
        return

    if output == "-":
        typer.echo("# Generated draft — review before deploying.")
        for rec in recommendations:
            typer.echo(f"\n# --- {rec.filename} ---")
            typer.echo(f"# Reason: {rec.reason}")
            typer.echo(rec.yaml_body)
    else:
        out_dir = Path(output)
        out_dir.mkdir(parents=True, exist_ok=True)
        typer.echo("# Generated draft — review before deploying.")
        for rec in recommendations:
            dest = out_dir / rec.filename
            dest.write_text(rec.yaml_body, encoding="utf-8")
            typer.echo(f"Wrote {dest}  ({rec.reason})")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@app.command("analyze")
def analyze(
    mcp_url: str = typer.Option(..., "--mcp", help="MCP server URL to introspect"),
    model: str = typer.Option("gemini", "--model", help="LLM provider: gemini|anthropic|openai|bedrock|azure-openai|mistral|cohere"),
    output: str = typer.Option("-", "--output", help="- for stdout JSON, or file path"),
    token: str | None = typer.Option(None, "--token", envvar="TESSERA_BEARER_TOKEN", help="Bearer token"),
) -> None:
    """Analyze an MCP server's tool catalog and recommend policies."""
    import httpx

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    rpc_body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(mcp_url, json=rpc_body, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        typer.echo(f"ERROR fetching tool catalog: {exc}", err=True)
        raise typer.Exit(1) from exc

    result = data.get("result", {})
    tools: list[dict[str, Any]] = result.get("tools", []) if isinstance(result, dict) else []

    if not tools:
        typer.echo("No tools found in catalog.", err=True)
        raise typer.Exit(1)

    # Derive upstream name from URL for context
    from urllib.parse import urlparse
    parsed_url = urlparse(mcp_url)
    upstream_name = parsed_url.netloc or mcp_url

    provider = _resolve_llm_provider(model)

    try:
        recommendations = provider.analyze_tools(tools, upstream_name=upstream_name)
    except Exception as exc:
        typer.echo(f"ERROR generating recommendations: {exc}", err=True)
        raise typer.Exit(1) from exc

    output_data = [
        {"filename": r.filename, "reason": r.reason, "yaml_body": r.yaml_body}
        for r in recommendations
    ]
    json_str = json.dumps(output_data, indent=2)

    if output == "-":
        typer.echo(json_str)
    else:
        out_path = Path(output)
        out_path.write_text(json_str, encoding="utf-8")
        typer.echo(f"Wrote {out_path}")


# ---------------------------------------------------------------------------
# LLM provider resolver (shared by policy author + analyze)
# ---------------------------------------------------------------------------


def _resolve_llm_provider(model: str) -> Any:
    """Instantiate the correct LLM provider from a short name."""
    model_lower = model.lower()
    if model_lower == "gemini":
        from tessera.llm.gemini import GeminiPolicyAuthor
        return GeminiPolicyAuthor()
    if model_lower == "anthropic":
        from tessera.llm.anthropic import AnthropicPolicyAuthor
        return AnthropicPolicyAuthor()
    if model_lower == "openai":
        from tessera.llm.openai import OpenAIPolicyAuthor
        return OpenAIPolicyAuthor()
    if model_lower == "bedrock":
        from tessera.llm.bedrock import BedrockPolicyAuthor
        return BedrockPolicyAuthor()
    if model_lower in ("azure-openai", "azure_openai", "azure"):
        from tessera.llm.azure_openai import AzureOpenAIPolicyAuthor
        return AzureOpenAIPolicyAuthor()
    if model_lower == "mistral":
        from tessera.llm.mistral import MistralPolicyAuthor
        return MistralPolicyAuthor()
    if model_lower == "cohere":
        from tessera.llm.cohere import CoherePolicyAuthor
        return CoherePolicyAuthor()
    typer.echo(f"Unknown model provider: {model!r}. Choose from: gemini, anthropic, openai, bedrock, azure-openai, mistral, cohere", err=True)
    raise typer.Exit(2)


# ---------------------------------------------------------------------------
# pricing serve
# ---------------------------------------------------------------------------


@pricing_app.command("serve")
def pricing_serve(
    port: int = typer.Option(4000, "--port", help="Host port to expose the pricing API on."),
    detach: bool = typer.Option(False, "--detach", help="Run container in background (-d)."),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="INFRACOST_API_KEY",
        help="Infracost API key (or set INFRACOST_API_KEY env var).",
    ),
) -> None:
    """Run the Infracost Cloud Pricing API container locally."""
    import subprocess

    cmd = ["docker", "run", "--rm"]
    if detach:
        cmd.append("-d")
    cmd.extend(["-p", f"{port}:4000"])
    if api_key:
        cmd.extend(["-e", f"INFRACOST_API_KEY={api_key}"])
    cmd.append("infracost/cloud-pricing-api:latest")
    # Inputs come from the local CLI invoker (port + their own API key); cmd is
    # a list (no shell expansion). S603 false positive in this context.
    sys.exit(subprocess.call(cmd))  # noqa: S603


# ---------------------------------------------------------------------------
# v0.7.0 Item D §7.5 (extension) — `tessera audit emit` + `audit upload`
# ---------------------------------------------------------------------------
#
# `emit` writes one synthetic event to a JSONL outbox at
# ~/.tessera/audit-outbox.jsonl (a small standalone queue file so emit + upload
# work as two separate process invocations during smoke-tests + cron-style
# pushes). `upload --once` drains the outbox by enqueueing every line into
# AuditCloudUploader and calling flush_once(). Successfully-uploaded events
# are removed from the outbox; failures leave the file intact for retry.
#
# This is intentionally minimal — production wrappers should use
# AuditEmitter + a SqliteSink + the in-process background uploader, NOT this
# JSONL outbox path. The outbox is for ad-hoc CLI usage + smoke validation.


_AUDIT_OUTBOX_PATH = Path.home() / ".tessera" / "audit-outbox.jsonl"


def _audit_outbox_append(event: dict) -> None:
    _AUDIT_OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _AUDIT_OUTBOX_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def _audit_outbox_read() -> list[dict]:
    if not _AUDIT_OUTBOX_PATH.exists():
        return []
    events: list[dict] = []
    with _AUDIT_OUTBOX_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _audit_outbox_clear() -> None:
    if _AUDIT_OUTBOX_PATH.exists():
        _AUDIT_OUTBOX_PATH.unlink()


@audit_app.command("emit")
def audit_emit(
    tool: str = typer.Option(..., "--tool", help="Tool name (e.g. demo.tool, aws.s3.PutObject)."),
    decision: str = typer.Option(
        "allow",
        "--decision",
        help="Decision outcome: allow | deny | observed.",
    ),
    scope: str = typer.Option(
        "default",
        "--scope",
        help="Tenant/scope identifier; mirrors the wrapper's local scope.",
    ),
    policy_id: str | None = typer.Option(None, "--policy-id", help="Optional policy_id."),
    reason: str | None = typer.Option(None, "--reason", help="Optional human-readable reason."),
) -> None:
    """Write one synthetic audit event to ~/.tessera/audit-outbox.jsonl.

    Use together with `tessera audit upload --once` to validate cloud
    ingest end-to-end. The event uses a deterministic-but-unique seq based
    on the current count in the outbox so the cloud sees monotonic seqs
    across repeated emits in the same outbox.
    """
    import datetime as _dt
    import uuid as _uuid

    outbox = _audit_outbox_read()
    next_seq = len(outbox) + 1
    event = {
        "seq":         next_seq,
        "event_id":    _uuid.uuid4().hex,
        "occurred_at": _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head_hash":   "",
        "payload": {
            "tool":      tool,
            "decision":  decision,
            "scope":     scope,
            "policy_id": policy_id or "",
            "reason":    reason or "",
            "source":    "cli.audit.emit",
        },
    }
    _audit_outbox_append(event)
    typer.echo(
        f"Queued audit event seq={next_seq} tool={tool} decision={decision} "
        f"(outbox depth: {next_seq})"
    )


@audit_app.command("upload")
def audit_upload(
    once: bool = typer.Option(
        False,
        "--once",
        help="Drain the outbox in one shot then exit. Required for v0.7.0.",
    ),
    endpoint: str | None = typer.Option(
        None,
        "--endpoint",
        help="Cloud endpoint. Defaults to the issuer the CLI logged into.",
    ),
    upload_scope: str = typer.Option(
        "",
        "--upload-scope",
        help="Opaque scope id sent in the request body (mirrors the wrapper's local scope).",
    ),
) -> None:
    """Drain ~/.tessera/audit-outbox.jsonl to /api/tessera/audit/ingest.

    Reads the saved OAuth token from ~/.tessera/oauth.json (must have scope
    tessera:audit:write) and POSTs every queued event in batches. On full
    success the outbox is truncated; on any failure the outbox is left
    intact so the next invocation can retry.

    Background-loop mode (no `--once`) is intentionally not implemented in
    v0.7.0 — wrappers integrate the uploader directly via
    `AuditCloudUploader.background_flush_loop()`.
    """
    import asyncio

    if not once:
        typer.echo(
            "tessera audit upload requires --once in v0.7.0; "
            "background-loop mode is owned by the wrapper, not the CLI.",
            err=True,
        )
        raise typer.Exit(2)

    events = _audit_outbox_read()
    if not events:
        typer.echo("Outbox is empty — nothing to upload.")
        return

    tokens = _load_oauth_tokens()
    if not tokens or not tokens.get("access_token"):
        typer.echo(
            "No saved OAuth token at ~/.tessera/oauth.json — run `tessera login` first.",
            err=True,
        )
        raise typer.Exit(2)

    from tessera.audit.cloud_uploader import AuditCloudUploader

    chosen_endpoint = endpoint or tokens.get("issuer") or _OAUTH_DEFAULT_ISSUER
    uploader = AuditCloudUploader(
        endpoint=chosen_endpoint,
        oauth_token=tokens["access_token"],
        upload_scope=upload_scope,
    )
    for evt in events:
        uploader.enqueue(evt)

    try:
        sent = asyncio.run(uploader.flush_once())
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Upload failed: {exc}", err=True)
        typer.echo(
            f"Outbox retained at {_AUDIT_OUTBOX_PATH} for retry.",
            err=True,
        )
        raise typer.Exit(2) from exc

    _audit_outbox_clear()
    typer.echo(f"{sent} event{'s' if sent != 1 else ''} uploaded.")


# ---------------------------------------------------------------------------
# v0.7.0 Item D §7.5 — `tessera login` (OAuth 2.1 PKCE browser flow)
# ---------------------------------------------------------------------------


@app.command("login")
def login(
    issuer: str = typer.Option(
        _OAUTH_DEFAULT_ISSUER,
        "--issuer",
        help="Base URL of the OAuth authorization server.",
    ),
    client_id: str = typer.Option(
        _OAUTH_DEFAULT_CLIENT_ID,
        "--client-id",
        help="Pre-registered public client ID (default: tessera-cli).",
    ),
    scope: str = typer.Option(
        _OAUTH_DEFAULT_SCOPE,
        "--scope",
        help="Space-separated OAuth scopes to request.",
    ),
    port: int = typer.Option(
        0,
        "--port",
        help="Localhost port to receive the callback on (0 = random).",
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        help="Seconds to wait for the browser callback before giving up.",
    ),
) -> None:
    """Authenticate with tessera.cloudmorph.ai via OAuth 2.1 + PKCE.

    Opens a browser to the authorization server's /oauth/authorize endpoint,
    waits for the redirect to a one-shot local listener, exchanges the code
    for an access + refresh token, and stores them at ~/.tessera/oauth.json.
    """
    import base64
    import hashlib
    import http.server
    import secrets
    import socket
    import threading
    import time
    import urllib.parse
    import webbrowser

    import httpx

    issuer = issuer.rstrip("/")

    # ── PKCE ────────────────────────────────────────────────────────────────
    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    state = secrets.token_urlsafe(24)

    # ── Local callback listener ─────────────────────────────────────────────
    captured: dict[str, str] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server contract
            parsed = urllib.parse.urlparse(self.path)
            qs = dict(urllib.parse.parse_qsl(parsed.query))
            captured.update(qs)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in qs and qs.get("state") == state:
                body = (
                    "<html><body style='font-family:system-ui;"
                    "max-width:560px;margin:80px auto;padding:0 20px'>"
                    "<h1 style='font-weight:300'>Login successful</h1>"
                    "<p>You can close this tab and return to your terminal.</p>"
                    "</body></html>"
                )
            else:
                body = (
                    "<html><body style='font-family:system-ui;"
                    "max-width:560px;margin:80px auto;padding:0 20px'>"
                    "<h1 style='font-weight:300'>Login failed</h1>"
                    f"<p>{qs.get('error_description', qs.get('error', 'unknown'))}</p>"
                    "</body></html>"
                )
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *_args: Any) -> None:  # noqa: N802
            # Suppress default stderr access-log spam.
            return

    # Bind to localhost on the requested (or random) port. SO_REUSEADDR so
    # repeated logins don't TIME_WAIT on the previous port.
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    server.timeout = 1.0  # poll handle_request every second so we can check timeouts
    redirect_uri = f"http://localhost:{port}/callback"

    # ── Build /authorize URL ────────────────────────────────────────────────
    authorize_url = (
        f"{issuer}/oauth/authorize?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    typer.echo(f"Opening {authorize_url} ...")
    try:
        webbrowser.open(authorize_url)
    except Exception:  # noqa: BLE001
        typer.echo("(Could not auto-open the browser — paste the URL above into your browser.)")

    # ── Wait for callback ───────────────────────────────────────────────────
    typer.echo(f"Listening on http://localhost:{port} for the OAuth callback ...")
    started = time.monotonic()
    while not captured.get("code") and not captured.get("error"):
        if time.monotonic() - started > timeout:
            typer.echo("Login timed out — no callback received.", err=True)
            raise typer.Exit(2)
        # handle_request honors server.timeout (1.0s) so we can re-check the budget.
        server.handle_request()
    server.server_close()

    if "error" in captured:
        typer.echo(
            f"Login failed: {captured.get('error_description') or captured.get('error')}",
            err=True,
        )
        raise typer.Exit(2)

    code = captured["code"]
    if captured.get("state") != state:
        typer.echo("Login failed: state mismatch (possible CSRF) — aborting.", err=True)
        raise typer.Exit(2)

    # ── Exchange code for tokens ────────────────────────────────────────────
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{issuer}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                    "client_id": client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            tokens = resp.json()
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Token exchange failed: {exc}", err=True)
        raise typer.Exit(2) from exc

    # Save tokens
    payload = {
        "issuer": issuer,
        "client_id": client_id,
        "scope": tokens.get("scope") or scope,
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "obtained_at": int(time.time()),
    }
    saved_at = _save_oauth_tokens(payload)
    typer.echo(f"Login successful — tokens saved to {saved_at}")


# ---------------------------------------------------------------------------
# v0.7.0 Item D §7.5 — `tessera config sync` (manual policy refresh)
# ---------------------------------------------------------------------------


@config_app.command("sync")
def config_sync(
    endpoint: str | None = typer.Option(
        None,
        "--endpoint",
        help="Cloud endpoint. Defaults to the issuer the CLI logged into.",
    ),
    cache_path: str | None = typer.Option(
        None,
        "--cache-path",
        help="Override the local SQLite cache location (default ~/.tessera/policy-cache.db).",
    ),
) -> None:
    """Refresh local policy cache from tessera.cloudmorph.ai immediately.

    Reads the OAuth token from ~/.tessera/oauth.json (created by `tessera login`),
    fetches /api/cli/policies, and writes the response to the local SQLite cache
    used by `tessera serve`. Prints a count of policies cached on success.
    """
    import asyncio

    tokens = _load_oauth_tokens()
    if not tokens or not tokens.get("access_token"):
        typer.echo(
            "No saved OAuth token at ~/.tessera/oauth.json — run `tessera login` first.",
            err=True,
        )
        raise typer.Exit(2)

    from tessera.cloud_sync import CloudPolicySync

    chosen_endpoint = endpoint or tokens.get("issuer") or _OAUTH_DEFAULT_ISSUER
    sync = CloudPolicySync(
        endpoint=chosen_endpoint,
        oauth_token=tokens["access_token"],
        cache_path=cache_path,
    )

    try:
        items = asyncio.run(sync.fetch_and_cache())
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Sync failed: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(f"Cached {len(items)} policies from {chosen_endpoint}.")


if __name__ == "__main__":
    app()
