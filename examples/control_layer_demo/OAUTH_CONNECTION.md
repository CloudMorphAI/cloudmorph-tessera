# Reviewed local OAuth connection workflow

Maintained under existing CM-104. The diagnostic packet received exact native approval and
passed one live scripted connection test. That approval is now consumed; no replay.
Attempt1 remains consumed; its root cause is undetermined. A ten-second HTTPX
component timeout and22second cooperative overall deadline supported this run;
this does not retrospectively prove the prior failure cause.

This workflow proves only consent/pair verification, strict policy snapshot and
one labelled test-audit transport when those checks actually pass. It does not
start a model/proxy, execute business tools, activate runtime policy, or prove
independent-agent or full YC acceptance.

## Maintained files

- `ephemeral_oauth_receiver.py`: RAM-only exact loopback/state callback and fixed
  no-query result page; owns its socket and closes it.
- `ephemeral_oauth_acceptance_use.py`: at most one policy GET and one labelled
  `SCRIPTED CONNECTION TEST` audit POST, strict snapshot/exact canonical ACK,
  private tmpfs files only, no retries or redirects.
- `ephemeral_oauth_connection.py`: pinned inputs and exact human approval before
  any SDK/cloud action, fresh account/binding/table/TTL/JWKS checks, verified JWT
  pair, durable FLOW/EXCHANGE claims, exact conditional refresh revoke/readback.
- `run_ephemeral_oauth_connection.py`: offline preparation check or explicit
  approved operator launch under an owned-process watchdog; finite JSON output.
- `verify_ephemeral_oauth_diagnostics.py`, `verify_ephemeral_oauth_operator.py`,
  `verify_ephemeral_oauth_runner.py`: repeatable offline checks with network/SDK
  tripwires, fake HTTP and owned synthetic children only.

Public configuration is `https://auth.tessera.cloudmorph.ai`, `tessera-cli`,
`tessera:policies:read tessera:audit:write`, AWS237509402889/us-east-1 and exact
`tessera-oauth-refresh-tokens-prod` target. Non-secret separate run inputs:
`workspace-support/audit/2026-10-18-cm095-resume/connection-attempt2-inputs.json`.
SHA256 `a3f5a3d9a850ad484da1bf796c33b4d1731f479d89339711182184665569b732`. UUID `6f3271c1-3b72-47cf-be9c-4b99cbf3e4d2`.
Actual operator SHA256 `14f1214a6a48d584bea07b582f867186a92f825870b94e49563e5040ea6fcf48`.
Source hashes and review are in `oauth-diagnostic-run-v3-preparation.json` and
`oauth-diagnostic-run-v3-independent-review.json` beside the inputs.

## Repeatable offline commands

The separately reviewed Nova-only workflow now has maintained fresh-read and
packet preparation commands. Run in WSL using the existing demo environment:

```sh
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/prepare_workload_preflight.py --output /mnt/c/Users/found/Desktop/CloudMorph/workspace-support/audit/2026-10-18-cm095-resume/NEW-readonly-preflight.json
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/prepare_workload_packet.py --preflight /mnt/c/Users/found/Desktop/CloudMorph/workspace-support/audit/2026-10-18-cm095-resume/NEW-readonly-preflight.json
```

Choose a new preflight receipt name. The first command reads only the fixed AWS
account, UI84/Labs60 deployment metadata, Nova Micro metadata and matching complete
on-demand prices. It performs no model/OAuth/cloud write; its worker has a75second
watchdog and bounded terminate/kill waits. Failures produce finite receipts.

The second command admits exact reviewed runtime main `29af3519`, operator and
watchdog bytes, a PASS preflight no older than one hour, actual source/dependency
hashes and the fixed model budget. It preserves prior read-only evidence and
creates exclusive nonsecret manifest/input/budget/pending-request files. Existing
workload inputs, approval or claims stop preparation. It creates no approval,
claim, listener or SDK operation. A preparation failure may leave partial files;
preserve and inspect them rather than rerunning or deleting markers.

The actual native human decision must separately bind that packet before the
reviewed launcher can run. The request covers only the exact hosted workload
policy, one grant, one bounded Nova session and exact generated-row cleanup;
Hermes is excluded. Previous connection approvals do not authorize it. These
helpers do not renew consumed permission or publish/deploy the runtime.

The separate original-client checks use the already admitted Hermes source,
fixed patch, authenticated setup receipts and isolated dependency environment.
They install nothing and create no new credential or model session:

```sh
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_original_flow.py
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_original_loopback.py --check
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_original_loopback.py
```

The first two commands check the maintained original-flow contracts and finite
probe/signal/guard-receipt classification without external actions. The third
is a bounded local fixture with actual Hermes SDK, canonical
proxy and loopback HTTP; hosted policy/ACK and AWS read results are synthetic.
It authenticates source before copying to a fresh private RAM tree and retains
finite diagnostics only. Its100second operation limit has a20second cleanup
reserve; missing or malformed guard evidence remains a failure. Capture each
result in a new receipt and preserve prior failures; do not automatically retry.

The scripted child receives private RAM `TMPDIR/TMP/TEMP` before Hermes imports.
Hermes may choose `/tmp` for later Unix sockets when paths exceed50characters;
outside-RAM writes remain blocked. The fixture separately identifies at most one
exact blocked urllib3 IPv6 loopback capability probe in each of the parent and
canonical proxy, with pinned source, role and call site; it never allows the bind.
Adapter/proxy shutdown requires validated guard startup plus either normal EXIT
or a recorded owned SIGTERM and observed SIGTERM exit; other incomplete receipts
fail. These checks establish offline compatibility only, not hosted policy
activation, real AWS/business outcomes or live-run authority.

Run from `/mnt/c/Users/found/Desktop/CloudMorph/cloudmorph-tessera` in WSL Ubuntu:

```sh
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_prepared_flow.py
```

This one-command source check runs nine maintained offline verifiers, stopping
on the first failure. Its JSON binds each verifier's actual SHA256 and separates
synthetic checks from live acceptance. It includes startup, Nova feedback, exact
workload policy, the new session operator and watchdog. Each child has a45second
limit and owns its temporary fixtures. No dependency installation, approval,
OAuth/model call or live run is performed. The separate Hermes connector needs
its own admitted source and isolated environment; it is not covered by this check.

The three original diagnostic commands remain available individually:

```sh
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_ephemeral_oauth_diagnostics.py
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_ephemeral_oauth_operator.py
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/verify_ephemeral_oauth_runner.py
```

Each emits finite PASS/FAIL JSON. Latest results:22diagnostic cases/21onceguards,
82operator guard checks,8runner checks; root also verified42actual fake-helper
receipts against the operator,8actual module origins and9core hashes. Repository
Ruff passes all7maintained Python files. No live OAuth/AWS/model call was used.
Full receipt: `oauth-diagnostic-run-v3-offline-checks.json`.

One-command source/run preparation check (no cloud calls or permission):

```sh
/home/sainath/tessera-demo-venv/bin/python -B examples/control_layer_demo/run_ephemeral_oauth_connection.py --check --run-inputs /mnt/c/Users/found/Desktop/CloudMorph/workspace-support/audit/2026-10-18-cm095-resume/connection-attempt2-inputs.json --run-inputs-sha256 a3f5a3d9a850ad484da1bf796c33b4d1731f479d89339711182184665569b732 --operator-sha256 14f1214a6a48d584bea07b582f867186a92f825870b94e49563e5040ea6fcf48
```

`PASS_PREPARATION_APPROVAL_REQUIRED` means local source checks passed. It does
not mean fresh cloud checks, consent, acceptance or launch authorization passed.
Broader CM-104 release preflight consolidation remains open.

## Diagnostics and privacy

Consumer schema v2 retains12top-level keys including at most2ordered diagnostic
records. Each has method, fixed phase, logical started/headers/body flags,
HTTP/body/JSON elapsed milliseconds clamped0..30000, status or null, exact
allowlisted exception class or null, and SHA256only of bounded values from
apigw-requestid/x-amzn-requestid/x-amz-apigw-id. Duplicate/invalid/overlong IDs are
omitted. Capture status and hashes before status/JSON/validator assertions;
finalize elapsed on timeout too. Later snapshot/cache failures update phase/class
without extending the HTTP elapsed measurement. Started does not prove server
contact. API response IDs do not automatically correlate Lambda invocation IDs.
No raw header/ID/body/URL/token/YAML/private identity/error text is projected.
UNKNOWN remains required after any attempted audit POST failure.

## Approval, one-use operation and cleanup

The native approval request is `connection-attempt2-approval-request.json`.
This is a request, not an approval receipt. Only an actual human Approve one test
decision may create the separate pinned14-field approval receipt. It must bind
input hash, run UUID, exact operator hash/client/scope/plan and all risk flags.
No such receipt or attempt2 claim exists at preparation completion.

After that decision, the same launcher takes `--approved-single-connection`
instead of `--check` and the actual `--approval-sha256`. The flag itself is not
permission. All source/input hashes must still match. The operator's selected
Chrome profile and built-in autofill are used through normal browser UI;
never extract credentials/cookies/token storage. Hand off unlock/MFA when needed.
Browser and loopback receiver must be on the same verified device.

The launcher adds an exclusive durable LAUNCH claim; operator FLOW is claimed
before READY and EXCHANGE before its sole token POST, using O_EXCL+fsync. Preserve
all claims on any outcome. Attempt1 is forbidden and unchanged. Never delete or
rename claims to retry, reuse the old approval, remint, refresh or scan. A new
run identity needs separate reviewed inputs and explicit human authority.

The operator has330/355second main/cleanup interrupts and360second budget.
Launcher control loop is390seconds plus at most5seconds TERM and5seconds KILL
wait. It signals only its owned process group. Early control failure/interruption
can truncate cleanup; process termination proves no refresh revocation. SIGKILL,
unknown exchange or unknown update may leave cleanup unproved or a30day refresh
row. No automatic replay/scan/remint follows. Save finite UNKNOWN where needed.

Exact cleanup uses only the signature-verified generated refresh row, full
immutable conditions and one UpdateItem; read back only the same key, no write
retry/DeleteItem/account cutoff/binding change. Preserve expiry/TTL and normal
code lifecycle. Refresh revocation does not revoke the one-hour access JWT.
Discard RAM references, close the owned socket/process and private tmpfs files;
immutable-string zeroization is not proven. No ordinary CLI token cache.

Capture only the public initial authorization URL transiently for browser
navigation, never persist its state/challenge or the signed consent request,
code/verifier/tokens. Save the operator's finite result and watchdog JSON. Actual
receipt status distinguishes pair/cleanup/consumer acceptance; exit1 can be a
truthful safe partial result. No AWS logging/settings/resource/model changes
are part of this workflow.

## Preserved actual history

Attempt1 used receiver48f0d1d0/helper55f612a3/operator11a8751d, archived as
`oauth-tooling-executed-attempt1-source.zip` (SHA41888227...dda9b). Hosted consent,
callback and signed pair verified; exact refresh revocation confirmed once at
09:59:56UTC, access expiry10:59:49UTC. Consumer BLOCKED/CONNECTION_USE_FAILED
before snapshot assignment or audit POST. Drona accepted that limited result.
No full acceptance. Prior maintained v2source is separately archived b718f91c...5797.

Complete read-only diagnosis is in `CONNECTION-READONLY-DIAGNOSIS.md` and
`connection-attempt1-final-diagnosis.json`: actual cause/helper-to-API attribution
unproven, no per-request API access log, server timings only circumstantially
support the timeout hypothesis. Current instrumentation tests that hypothesis;
it cannot retrospectively prove the prior exception or establish an underlying
backend logic fix. No server logging change is authorized.

## Actual approved attempt2 outcome

Native decision Approve one test was recorded and bound to the reviewed inputs/
operator. The actual run passed: strict policy GET200/6872ms,1policy/digestcd68...a3e;
canonical labelled audit POST200/2068ms,1write. The authenticated Audit page shows
the same canonical-verified stream/head/digest and zero model/tool/business actions.
Refresh exact-row conditional revocation/readback confirmed10:41:13UTC; access
expires11:41:03UTC. Watchdog180.006s/exit0, process/port stopped and RAM directories0.
Drona independently accepted scripted-connection scope only. No proxy activation
or independent-agent acceptance. Attempt1 cause remains undetermined.

Attempt2 approval and LAUNCH/FLOW/EXCHANGE claims are consumed and immutable;
preparation/execute commands now fail closed. Keep them for evidence, never
remove them to rerun. Both source archives and actual receipts are preserved;
see CONNECTION-PASS-1041.json, connection-attempt2-result.json,
connection-attempt2-browser-audit-readback.json and connection-attempt2-independent-review.json.
A future separately approved run needs newly reviewed inputs/guards.
