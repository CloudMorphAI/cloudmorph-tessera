# External client attachment

This separate example accepts real OpenCode and Codex MCP clients at the same
`/mcp/control` endpoint. It does not launch models or create cloud infrastructure.

The attachment preserves each actual client-selected tool and arguments. It
adds trusted run/request/tool-use correlation metadata, authenticates twice
through canonical Tessera bearer auth, and uses canonical policy/proxy/audit.
Principal names are `opencode` and `codex`, sharing exactly one tenant policy and
audit chain. Those labels identify the authenticated credentials; independent
client session/provider receipts must establish actual model identity/execution.

Server input stores only SHA256 digests of distinct randomly generated 256-bit
URL-safe client tokens, plus a finite expiry. Raw tokens must remain in the
operator/client RAM and protected HTTPS request memory; never put them in ECS
definitions, environment, source, files, logs or prompts. The digest authenticator
hashes the incoming bearer then delegates constant-time matching and principal/
scope mapping to the canonical authenticator. Empty auth never becomes dev mode.

## Local preparation

From a checkout with Tessera's runtime dependencies installed, run on Linux:

```
python -B examples/external_client_demo/serve_external_clients.py --check
python -B examples/external_client_demo/serve_external_clients.py --check --plan /approved/plan.json --plan-sha256 ACTUAL_SHA256
```

The first command reports exact source hashes and missing actual bindings. It
does not generate client tokens or assert remote readiness. The second also
validates the supplied nonsecret plan and shared startup guard, including two
loopback closed-port probes. Both perform zero SDK/external-network/listener
operations. The maintained image bootstrap removes only its three known path
variables (`TESSERA_AUDIT_PATH`, `TESSERA_CONFIG_PATH`, `TESSERA_POLICY_DIR`);
all remaining `TESSERA_*` overrides are rejected in check and serve modes.
Installed-image source hashes must be regenerated and independently
reviewed in that exact image; checkout checks do not prove image compatibility.

## Portable credential-free checks

The focused tests use the real canonical auth, policy, proxy and audit components
with an in-process synthetic upstream. They verify distinct principals, expiry,
identity spoof rejection, fixed argument bounds, call budgets, and an actual
audited default block with zero forwarded requests. No AWS credentials, Docker,
private workspace files or approval records are required:

```
python -m pytest -q tests/test_external_client_demo.py
```

The separate startup regression requires Linux Docker and an **already present**
image containing Tessera, boto3 and uvicorn. Supply its local image reference and
exact OCI repository digest explicitly; the command never pulls or builds:

```
python -B examples/external_client_demo/startup_regression.py \
  --image EXISTING_LOCAL_IMAGE \
  --image-digest sha256:EXACT_64_HEX_REPOSITORY_DIGEST \
  --receipt /tmp/external-client-startup-NEW.json
```

Use `--docker /path/to/docker` if Docker is outside PATH. The receipt must be a
new path; an existing receipt is never replaced. An unpublished locally built
image may have no repository digest. In that case use `--image-id sha256:EXACT_CONFIG_ID`
instead of `--image-digest`; the two options are mutually exclusive. The actual
Docker image `Id` must match exactly. The receipt labels this `local_config_id`,
distinct from `repository_manifest_digest`; digest mode still requires an exact
matching `RepoDigests` entry and never falls back to a local ID.
The verifier discovers the
canonical13-file hashes inside that image and binds the current five example
files into a new synthetic plan. This discovery is local evidence and grants no
live source admission or access. No historical audit directory, deployment plan,
task UUID, OAuth token or cloud account credential is read. Dummy verifier hashes
have no delivered client credential.

`task_payload.build_payload(plan)` requires the complete18-file manifest and
exact local source hashes. `task_payload.bootstrap(payload, mode)` is the shared
actual launcher for both `check` and `serve`; there is no parallel test launcher.
It verifies each embedded file, uses a private0700 tmpfs tree, executes the server
in the same Python PID, and removes its exact owned tree in `finally`. The two
helper hashes are recorded separately in the regression receipt.

All regression containers use `--pull=never --network=none --read-only`, UID10001,
no privileges/capabilities, no host mounts, finite limits and exact-name cleanup.
They run normal `--check`, rejection of an unknown auth override before provider
construction, and actual `--serve` with an explicit synthetic in-process
STS/Amplify provider. Only loopback health is probed; health200 is followed by
SIGTERM, actual server/payload cleanup receipts, closed-port checks and owned RAM
removal. Container absence requires the precise missing-container response and
a healthy Docker daemon. The receipt distinguishes loopback probes from zero
external-network/real-SDK calls. A timeout or unproved cleanup fails the check.
CLI output is drained while running, with a64KiB cap on each of stdout/stderr.
Overflow or timeout kills and reaps only the owned CLI process, then the existing
exact-name container cleanup runs. No unbounded captured output is retained.
This is startup evidence only: no client tool call, model execution, real AWS
read, hosted ACK, deployment or live two-client acceptance is claimed.

The exact plan schema is `tessera.external_client_plan.v1`, with only these keys:

| Key | Actual binding |
| --- | --- |
| `runId` | New `external2-` plus canonical UUID4 |
| `tenant` | Approved tenant scope, `[a-z0-9_-]{1,64}` |
| `publicOrigin` | Approved HTTPS origin, no credentials/path/query/fragment |
| `listenHost` | `127.0.0.1` or explicitly approved private ECS `0.0.0.0` |
| `listenPort`, `adapterPort` | Distinct ports 1024–65535 |
| `maxSeconds` | Session lifetime 60–900 seconds |
| `expiresAtEpoch` | Actual credential expiry: covers session, at most 1800 seconds away |
| `clientTokenSha256` | Exactly `opencode` and `codex`, distinct lowercase SHA256 strings |
| `expectedCallerArnPrefix` | Exact approved `arn:aws:sts::237509402889:assumed-role/ROLE_NAME/` |
| `sourceSha256` | Exact complete logical path/hash map emitted by `--check` in the execution image |

Missing actual task role, tenant, HTTPS ingress, secret delivery, or reviewed image
bindings block server execution. No role or endpoint is invented. The server
verifies actual STS account and assumed-role ARN prefix before any deployment
read. Cloud permission creation and HTTPS routing belong to the parent scope.
No model/provider privileges belong in the server task role.

## Separately authorized serving

```
python -B examples/external_client_demo/serve_external_clients.py --serve --plan /approved/plan.json --plan-sha256 ACTUAL_SHA256
```

Use only after the parent approves the exact infrastructure/access/run packet.
The root must enforce an independent task teardown deadline. Uvicorn graceful
shutdown allows 10 seconds, adapter completion up to another 45 seconds; use at
least 60 seconds of cleanup reserve after `maxSeconds`, with a finite hard task
deadline. `SERVER_STARTING` is not readiness: verify private target health and
authenticated MCP initialization on the exact approved HTTPS endpoint. The private
`/healthz` route is not publicly mapped. No public admin/evidence route is added.
Existing model clients supply `Authorization: Bearer …`; neither a
custom MCP metadata shim nor scripted fake client tool outputs are required.

Each client has at most 24 HTTP requests and 2 tool calls. The real model should
choose `read_workload_status` with these exact arguments, then report the observed
metadata/denial without retry or bypass:

| Field | Permitted deployment | Out-of-scope deployment |
| --- | --- | --- |
| `operation` | `amplify.GetJob` | `amplify.GetJob` |
| `app_id` | `d2nxbpvv53qeqc` | `d3s9n6ffim4722` |
| `branch` | `main` | `main` |
| `job_id` | `84` | `60` |

Policy is the exact local read allow SHA256
`c201005df8d1d0985099515a7419e4b3ada539f3704b2a4eb79ccca240b44500`,
with enforcement/default block. It is not claimed to be a hosted policy snapshot.
The adapter independently accepts UI84 only, allows one actual AWS SDK GetJob per
principal, uses SDK total attempts=1, and refuses everything else before AWS.
Results are labelled `AWS_SDK`, not native AWS MCP. STS and deployment calls may
have service charges; no independent zero-cost claim is made.

## Evidence and cleanup

The runner uses a new owned mode0700 tmpfs tree only. On shutdown, it drains
canonical audit, verifies the exact tenant chain, and joins actual authenticated
principal → request/tool-use ID → returned canonical audit header/event → adapter
contact. Each principal needs one successful exact UI84 allow and one exact Labs60
block with zero contacts. Model session IDs/tool selection/output belong in the
separate actual client receipts; server identity alone does not prove model use.

The final stdout receipt contains finite nonsecret correlations, chain head,
cleanup status, adapter contacts and outcome. It also exports original unchanged
canonical events only after strict fixed-scope content validation; it never
scrubs or restamps events. Unexpected content marks export `UNPROVED` and fails
the run. Client tool metadata accepts only bounded standard progress tokens;
caller intent/identity extras are rejected before canonical dispatch. The actual
audit identity comes from the returned canonical header or body metadata.
Private audit DB, arbitrary arguments and logs are not copied. Owned RAM is
removed only after listeners/workers stop. Failures or
missing evidence produce an honest failed/unproved receipt, never synthetic
success or automatic retry. Capture only this bounded output in the existing
owned CloudWatch stream. Hosted upload is deliberately absent and
`hosted_upload_acknowledged=false`; the parent may perform a separately authorized
canonical upload from real evidence, without inventing an ACK.
