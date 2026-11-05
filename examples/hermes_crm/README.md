# Hermes MCP client compatibility demo

This small driver exercises the real Hermes Agent MCP connector against one local
Tessera Streamable HTTP endpoint. Hermes connects to Tessera only; the synthetic
CRM service stays behind Tessera. The driver makes a fixed sequence of tool calls
through Hermes and does not run an LLM or send a provider request.

## Pinned Hermes source and dependencies

- Repository: `https://github.com/NousResearch/hermes-agent.git`
- Commit: `51abb25ce07f384bd60f1eba151623c769db6b3e` (15 October 2026)
- The pinned GitHub commit-addressed archive SHA256 and hashes for the client,
  MCP discovery/handler/registration/schema modules, project metadata, and lock
  file are recorded in `source-pin.json`. `bootstrap.ps1` verifies these before
  writing an archive receipt; the client checks the receipt and critical files.
- Source API: `tools.mcp_tool_discovery.discover_mcp_tools()` registers tools;
  `model_tools.handle_function_call()` dispatches each deterministic call.
- Optional feature: Hermes `[mcp]` extra. The pinned `uv.lock` resolves
  `mcp==2.0.0`, `mcp-types==2.0.0`, `httpx2==2.12.0`, and `starlette==1.3.1`.
  These are the extra's direct pins except `mcp-types`, which is transitive;
  `ruamel.yaml==0.18.16` was needed on the tested Python 3.13 environment because
  its upstream dependency marker selects Python 3.14. Use the checkout's
  own lock file and a separate environment. Upstream's metadata permits Python
  3.11–3.14 for updater compatibility, but its source states that only Python
  3.14 is supported. The actual connector runs below used Python 3.13.7; this is
  measured compatibility for this fixture, not an upstream support claim. Keep
  Hermes separate from Tessera's FastAPI environment.

Hermes maps a Tessera tool such as `crm.update_contact` to its internal registry
name `mcp__tessera__crm_update_contact`. The registered handler keeps the exact
original MCP tool name and sends `crm.update_contact` in `tools/call`. The emitted
JSON ledger records both names so the distinction can be reviewed.

## Run

From the repository root, retrieve and verify the official pinned source archive
into a new path. This does not create synthetic Git metadata:

```powershell
.\examples\hermes_crm\bootstrap.ps1 -SourceDirectory "$env:TEMP\hermes-agent-cm093-verified"
```

Install the pinned source's `[mcp]` extra in a dedicated Hermes environment using
the supported Python 3.14 runtime and the checkout's `uv.lock`. Start the local
synthetic Tessera fixture with `python .\examples\hermes_crm\server.py --host
127.0.0.1 --port 8765`. Then run from the Hermes environment:

```powershell
python .\examples\hermes_crm\hermes_client.py `
  --hermes-source $env:TEMP\hermes-agent-cm093-verified `
  --profile-home $env:TEMP\cm093-hermes-demo-profile `
  --tessera-url http://127.0.0.1:8765/mcp/crm `
  --scenario allowed-denied
```

The profile path must be a new empty directory. The script sets `HERMES_HOME`
before importing Hermes and creates only its own `config.yaml`, with a single
`tessera` server pointing at the supplied loopback per-upstream endpoint. It does
not use the default Hermes profile or read its config, `.env`, or tokens. Fixture
control endpoints are `GET /__demo/status` and `POST /__demo/reset`; they read or
reset synthetic in-memory state outside the MCP tool call ledger.

The `allowed-denied` sequence updates the synthetic note, reads the record,
attempts a paid-status update, and reads again. `mixed-denied` sends a paid status
and note change in one update so the proxy can demonstrate an atomic block. Run
the mixed case against a freshly seeded fixture. The CRM record is expected to be
`crm-demo-001`, with `call_notes: Initial note` and `billing_status: unpaid`.

The client makes exactly the listed tool calls after discovery. Hermes' startup
`initialize` and `tools/list` exchanges are not CRM `tools/call` operations.

## Verified local result and checks

On 15 October 2026, the pinned real Hermes discovery/dispatcher stack completed
both scenarios against the actual Tessera proxy, negotiating MCP `2025-11-25`:

| Scenario | Forwarded CRM tool-call counts after each step | Writes | Final record |
| --- | --- | --- | --- |
| `allowed-denied` | `1, 2, 2, 3` | 1 | Updated note; billing remains `unpaid` |
| `mixed-denied` | `0, 1` | 0 | Original note and `unpaid` billing preserved |

The paid update emits a `block` decision with reason
`paid_billing_status_is_not_allowed`. The upstream CRM has no billing guard;
Tessera prevents the disallowed update from reaching it. Each call's JSON ledger
includes its returned audit event ID and the actual fixture snapshot. The local
status endpoint exposes only selected MCP transport/version headers, excluding
authorization, cookies and session tokens.

The separate Tessera environment passed 42 focused tests, with zero skips:

```powershell
python -m pytest tests/test_hermes_crm_demo.py tests/integration/test_mcp_modern_compat.py -q
```

The two fixture tests use the actual policy engine and proxy with a mocked
upstream spy; the 40 existing tests cover modern MCP request/response regression.
Those tests are separate from the real Hermes connector runs described above.
For deterministic synthetic audit assertions, run the fixture/tests with
`TESSERA_AUDIT_SYNC=1`; this does not establish production async durability.
On Windows, preserve standard OS variables including `SystemRoot` when creating
a credential-free child environment, or asyncio/socket imports can fail before
any HTTP exchange. No provider credentials are required.

This example uses the existing parsed argument-path regex condition on the
specific scalar string `fields.billing_status == paid`. It does not implement a
general typed field ACL. The loader does not precompile this condition's regex;
the simple anchored pattern uses the existing runtime safety path. No policy
schema or evaluator change is included. Only routed MCP calls are covered; direct
REST calls, customer acceptance, autonomous LLM planning and production readiness
are outside this proof. The fixture binds to loopback only and uses synthetic
records without authentication; do not expose it as a hosted service.

For a paced live recording of the same real calls, add `--step-delay 4` (accepted
range 0–5 seconds). It only pauses after each call and does not change decisions.
