# Post-call demo verification and capture

This folder contains the HTTP-level verifier for the local fictional Acme post-call demo. It starts the actual local `DemoService` and HTTP handler in-process on an ephemeral `127.0.0.1` port with Nova disabled. The scripted request runs the actual Tessera policy gateway, local SQLite audit chain, and isolated synthetic Node adapters. No provider call, account credential, vendor system, or external customer data is used.

## Run the verifier

From the `cloudmorph-tessera` repository root, in the existing WSL Python environment with the runtime dependencies installed and Node.js available on `PATH`:

```bash
python examples/post_call_demo/verification/verify_http.py
```

The verifier checks the complete eight-tool scripted response (seven allows and one 30% discount block), audit correlation and chain verification, zero adapter contacts for the blocked update, unchanged 0% discount, pending internal review task, and unsent draft. It also posts the same idempotency key again and asserts the exact same receipt with no second gate execution. Negative HTTP checks cover malformed/duplicate JSON, non-finite values, unknown request fields, invalid or missing idempotency headers, missing/cross Origin, invalid Host, unsupported policy-write paths, and Nova disabled. It prints only a small filtered result and does not persist a run receipt.

The backend fixture has separate unit tests for strict tool argument validation, mixed-payload atomic rejection, malformed discount values, and idempotent adapter writes. This verifier intentionally does not duplicate those direct fixture tests; it exercises the served HTTP contract and actual policy gate. The API accepts a fixed scenario envelope, not arbitrary caller-supplied tool arguments, so malformed tool arguments are rejected by the fixture/gateway tests rather than exposed as a public HTTP tool API.

## Nova run and raw recording

Nova mode is intentionally excluded from the verifier. A single real-provider attempt requires the explicit `--enable-nova` opt-in to the local server and must be performed separately after the scripted checks and independent review. The server allows only one attempt per process; a failed attempt is not automatically retried. Use a new empty output directory for the run receipt.

For a raw UI recording, start the local server without adding vendor integrations, open the loopback UI, and preserve one continuous, unspliced master showing the selected run mode, actual backend response, policy decision, adapter count, readback, audit verification, and model/usage only when returned by the actual provider call. Do not reuse a prior AP recording or splice its events into this scenario. Keep the raw master and its hash unchanged; editorial cuts can be made separately. There is no fixed raw-recording duration requirement.

The Playwright recorder requires an explicit mode and a fresh output directory. With the local server already running, an example WSL invocation using the existing Playwright install is:

```bash
node examples/post_call_demo/verification/capture_ui.mjs \
  --mode scripted \
  --output-dir /tmp/cm099-raw-scripted-<fresh-id> \
  --playwright-module /tmp/arjun-onboarding-linux-jkwwhvb_/node_modules/playwright \
  --browser-cache /tmp/arjun-onboarding-browsers-20261016
```

It records the actual `/api/run` response bytes, a continuous unmodified WebM, timestamps/status, browser errors, a filtered DOM summary, and screenshots. It blocks non-loopback browser requests, clicks once, and waits for the actual UI completion label. A successful response must render the requested mode, 0% deal discount, eight event cards, one blocked card, zero blocked-call contacts, and no browser page errors. The walkthrough centers and holds the actual notes allow, drafts, blocked 30% card, separate 30% review task, final deal readback, audit chain, and audit records for about five seconds each. It verifies each selected target is fully visible before capture. This is real browsing time, not simulated execution or streaming. `--mode nova` is a separate explicit choice and does not enable or start the server's Nova mode; no mode fallback or automatic provider call occurs.

This fixture demonstrates only synthetic local behavior. `policy_admin_auth_verified` is explicitly false; the read-only policy endpoint is not evidence of authenticated admin policy writes, and loopback Origin/Host checks are not authentication. The demo does not prove a real CRM connection, customer acceptance, an operator role boundary, or production readiness.
