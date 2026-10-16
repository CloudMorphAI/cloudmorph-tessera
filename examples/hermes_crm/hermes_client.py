"""Deterministic, no-LLM driver through Hermes Agent's real MCP client stack.

This module deliberately configures one Hermes MCP server named ``tessera``.  The
URL must be the local Tessera Streamable HTTP endpoint; CRM remains behind that
proxy. Calls enter Hermes through ``model_tools.handle_function_call`` so Hermes
performs MCP tool registration, name translation, and transport dispatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

HERMES_REPOSITORY = "https://github.com/NousResearch/hermes-agent.git"
HERMES_COMMIT = "51abb25ce07f384bd60f1eba151623c769db6b3e"
HERMES_MCP_SERVER_NAME = "tessera"
LOGICAL_UPDATE_TOOL = "crm.update_contact"
LOGICAL_READ_TOOL = "crm.get_contact"


def _verify_source_pin(source: Path) -> str:
    pin_path = Path(__file__).with_name("source-pin.json")
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    if pin["commit"] != HERMES_COMMIT:
        raise RuntimeError("Client pin constant and source-pin.json do not agree")
    if (source / ".git").exists():
        git = os.environ.get("GIT_EXE") or "git"
        result = subprocess.run(  # noqa: S603 -- no shell; only a local Git revision read.
            [git, "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        revision = result.stdout.strip()
        if revision != HERMES_COMMIT:
            raise RuntimeError(f"Hermes git HEAD must be {HERMES_COMMIT}; found {revision}")
    else:
        # GitHub commit archives omit .git. Accept them only with the receipt
        # written by bootstrap.ps1 after archive and critical-file hash checks.
        receipt_path = source / ".cm093-source-receipt.json"
        if not receipt_path.is_file():
            raise RuntimeError("Source archive lacks a verified bootstrap receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if (
            receipt.get("commit") != HERMES_COMMIT
            or receipt.get("archive_sha256") != pin["archive_sha256"]
            or receipt.get("archive_url") != pin["archive_url"]
        ):
            raise RuntimeError("Pinned Hermes source receipt does not match source-pin.json")

    for relative, expected in pin["critical_files"].items():
        path = source / Path(relative)
        if not path.is_file():
            raise RuntimeError(f"Pinned Hermes source file missing: {relative}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if digest != expected:
            raise RuntimeError(f"Pinned Hermes source file hash mismatch: {relative}")
    return HERMES_COMMIT


def _write_isolated_config(profile_home: Path, tessera_url: str) -> None:
    parsed = urlparse(tessera_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--tessera-url must be an HTTP(S) MCP endpoint")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This synthetic demo accepts loopback Tessera URLs only")

    profile_home = profile_home.expanduser().resolve()
    default_home = (Path.home() / ".hermes").resolve()
    if profile_home == default_home:
        raise ValueError("Refusing to use the default Hermes profile; choose a fresh isolated directory")
    profile_home.mkdir(parents=True, exist_ok=True)
    existing = list(profile_home.iterdir())
    if existing:
        raise ValueError(f"Isolated Hermes profile must be empty; found: {existing[0].name}")

    # The MCP stack reads the same config.yaml name as normal Hermes, but HERMES_HOME
    # is set before any Hermes module is imported so no default-profile state is read.
    config = (
        "mcp_servers:\n"
        f"  {HERMES_MCP_SERVER_NAME}:\n"
        f"    url: {json.dumps(tessera_url)}\n"
        "    enabled: true\n"
        "    connect_timeout: 10\n"
        "    timeout: 10\n"
        "    tools:\n"
        "      resources: false\n"
        "      prompts: false\n"
    )
    (profile_home / "config.yaml").write_text(config, encoding="utf-8")


class HermesMCPClient:
    """Small deterministic caller using Hermes' official MCP discovery/dispatch path."""

    def __init__(self, hermes_source: Path, profile_home: Path, tessera_url: str) -> None:
        self.hermes_source = hermes_source.expanduser().resolve()
        self.profile_home = profile_home.expanduser().resolve()
        self.tessera_url = tessera_url
        parsed = urlparse(tessera_url)
        self.fixture_origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        self.registered_tools: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self._closed = False

    def connect(self) -> list[str]:
        _verify_source_pin(self.hermes_source)
        if not (self.hermes_source / "tools" / "mcp_tool_discovery.py").is_file():
            raise FileNotFoundError(f"Not a Hermes Agent source checkout: {self.hermes_source}")

        _write_isolated_config(self.profile_home, self.tessera_url)
        # Hermes resolves config, cache, state, and tokens relative to HERMES_HOME.
        os.environ["HERMES_HOME"] = str(self.profile_home)
        sys.path.insert(0, str(self.hermes_source))

        from tools.mcp_tool_discovery import discover_mcp_tools

        self.registered_tools = list(discover_mcp_tools())
        expected = {
            "crm.update_contact": self._registered_name("crm.update_contact"),
            "crm.get_contact": self._registered_name("crm.get_contact"),
        }
        missing = [logical for logical, registered in expected.items() if registered not in self.registered_tools]
        if missing:
            self.close()
            raise RuntimeError(
                "Tessera tools/list did not register expected tool(s): "
                + ", ".join(missing)
                + f"; registered by Hermes: {self.registered_tools}"
            )
        return self.registered_tools

    @staticmethod
    def _registered_name(logical_tool_name: str) -> str:
        # Hermes normalizes the original Tessera tool name for its model-facing
        # registry name. The call handler retains and forwards the original name.
        from tools.mcp_tool_schema import mcp_prefixed_tool_name

        return mcp_prefixed_tool_name(HERMES_MCP_SERVER_NAME, logical_tool_name)

    def call(self, logical_tool_name: str, arguments: dict[str, Any]) -> str:
        if logical_tool_name not in {LOGICAL_UPDATE_TOOL, LOGICAL_READ_TOOL}:
            raise ValueError(f"Unexpected Tessera tool name: {logical_tool_name}")
        registered_name = self._registered_name(logical_tool_name)
        if registered_name not in self.registered_tools:
            raise RuntimeError(f"Hermes did not register {registered_name}")

        # This is Hermes' standard synchronous registry dispatcher. It calls the
        # registered MCP handler, which sends the original MCP tool name upstream.
        from model_tools import handle_function_call

        result = handle_function_call(
            registered_name,
            arguments,
            session_id="cm093-hermes-synthetic-demo",
            turn_id=f"turn-{len(self.calls) + 1}",
        )
        self.calls.append(
            {
                "hermes_registered_name": registered_name,
                "tessera_tools_call_name": logical_tool_name,
                "arguments": arguments,
                "result": result,
                "fixture_after": self.fixture_status(),
            }
        )
        return result

    def reset_fixture(self) -> dict[str, Any]:
        request = Request(  # noqa: S310 -- connect validates the loopback HTTP(S) origin.
            self.fixture_origin + "/__demo/reset", data=b"{}", method="POST"
        )
        with urlopen(request, timeout=5) as response:  # noqa: S310 -- validated loopback fixture.
            return json.loads(response.read().decode("utf-8"))

    def fixture_status(self) -> dict[str, Any]:
        with urlopen(  # noqa: S310 -- connect validates the loopback HTTP(S) origin.
            self.fixture_origin + "/__demo/status", timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            from tools.mcp_tool_lifecycle import shutdown_mcp_servers

            shutdown_mcp_servers(names={HERMES_MCP_SERVER_NAME}, include_active=True)
        except Exception:
            # Discovery can fail during a partial Hermes import (for example,
            # on a platform that cannot initialize asyncio's socket provider).
            # Keep this cleanup best-effort so it does not replace that primary
            # startup/dispatch exception with a secondary shutdown traceback.
            pass

    def __enter__(self) -> HermesMCPClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _scenario(client: HermesMCPClient, name: str, step_delay: float = 0) -> list[dict[str, Any]]:
    record_id = "crm-demo-001"
    if name == "allowed-denied":
        steps = [
            (LOGICAL_UPDATE_TOOL, {"record_id": record_id, "fields": {"call_notes": "Initial note reviewed"}}),
            (LOGICAL_READ_TOOL, {"record_id": record_id}),
            (LOGICAL_UPDATE_TOOL, {"record_id": record_id, "fields": {"billing_status": "paid"}}),
            (LOGICAL_READ_TOOL, {"record_id": record_id}),
        ]
    elif name == "mixed-denied":
        steps = [
            (
                LOGICAL_UPDATE_TOOL,
                {
                    "record_id": record_id,
                    "fields": {"call_notes": "This mixed update must be atomic", "billing_status": "paid"},
                },
            ),
            (LOGICAL_READ_TOOL, {"record_id": record_id}),
        ]
    else:  # pragma: no cover - argparse validates this
        raise ValueError(name)

    for tool_name, arguments in steps:
        client.call(tool_name, arguments)
        if step_delay:
            time.sleep(step_delay)
    return client.calls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-source", type=Path, required=True, help="Pinned NousResearch/hermes-agent checkout")
    parser.add_argument("--profile-home", type=Path, required=True, help="New, empty isolated Hermes profile directory")
    parser.add_argument(
        "--tessera-url",
        default="http://127.0.0.1:8765/mcp/crm",
        help="Loopback Tessera per-upstream Streamable HTTP endpoint",
    )
    parser.add_argument("--scenario", choices=("allowed-denied", "mixed-denied"), default="allowed-denied")
    parser.add_argument("--step-delay", type=float, default=0, help="Pause 0-5 seconds after real calls for recording")
    args = parser.parse_args()
    if not 0 <= args.step_delay <= 5:
        parser.error("--step-delay must be between 0 and 5 seconds")

    client = HermesMCPClient(args.hermes_source, args.profile_home, args.tessera_url)
    try:
        registered = client.connect()
        client.reset_fixture()
        before = client.fixture_status()
        calls = _scenario(client, args.scenario, args.step_delay)
        after = client.fixture_status()
        print(
            json.dumps(
                {
                    "execution": "Hermes MCP connector and dispatcher; deterministic calls, no LLM planning/chat",
                    "hermes_commit": HERMES_COMMIT,
                    "hermes_profile": str(client.profile_home),
                    "server_configured": HERMES_MCP_SERVER_NAME,
                    "tessera_endpoint": client.tessera_url,
                    "registered_tools": registered,
                    "fixture_before": before,
                    "calls": calls,
                    "fixture_after": after,
                },
                indent=2,
            )
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
