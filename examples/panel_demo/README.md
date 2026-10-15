# Tessera panel demo adapter

This local adapter turns the workspace's existing `tessera.demo.run_demo()` into a browser-friendly recorded export and an optional fresh-run API. It uses the actual Tessera policy gate, response receipts, audit exporter and verifier. It does not change core Tessera code. The staged v1.0 demo work in this checkout is unreleased; these instructions assume this checkout and its existing Python environment rather than claiming availability in a public package release.

All inputs and systems are synthetic. The three attempts contain a fake token, a force-push to `main` in fictional `acme/api`, and `DROP TABLE users`. The allowed request is `list_dir`. Its response is the existing mock's canned database text; it is not live directory or database inventory. No cloud, LLM, production credential or customer system is involved.

Run from WSL Ubuntu with the already prepared environment; no dependency install is needed when that environment is intact:

```sh
cd /mnt/c/Users/found/Desktop/CloudMorph/cloudmorph-tessera
/home/sainath/tessera-demo-venv/bin/python examples/panel_demo/demo_server.py export \
  --output-dir /mnt/c/Users/found/Desktop/CloudMorph/cloudmorph-mono-repo/apps/tessera/public/demo
```

The export writes `demo-data.json` and `evidence.zip` after one real, successfully verified engine run. The recorded JSON says `mode: "recorded"`; a frontend replay does not execute policy again. The ZIP contains the unmodified engine artifacts `events.jsonl`, `manifest.json`, `summary.html` and `VERIFY.md`. A fresh throwaway signing key is created for each run, stays in child scratch storage and is deleted afterward. Never present this as a production signer or an external identity attestation.

To enable fresh local execution, serve the same static assets with the adapter:

```sh
/home/sainath/tessera-demo-venv/bin/python examples/panel_demo/demo_server.py serve \
  --web-root /mnt/c/Users/found/Desktop/CloudMorph/cloudmorph-mono-repo/apps/tessera/public/demo \
  --port 8765
```

Open `http://127.0.0.1:8765/`. The server binds only IPv4 loopback. A static host can offer recorded replay and ZIP download; a static host cannot execute Python. There is no hosted demo service in this change.

## API contract (schema version 1)

- `GET /api/health` returns `{ "service": "tessera-panel-demo", "schema_version": 1, "live": true, "offline": true }`.
- `POST /api/run` accepts no body or `{}` only, executes the four fixed calls in a fresh subprocess, and returns the JSON projection with `mode: "live"`.
- `GET /api/evidence` downloads the latest successful live run's ZIP, or returns 404 before the first successful run. A failed run does not replace the last successful ZIP.
- A second concurrent run returns 409. The default subprocess limit is 45 seconds; `--timeout` accepts 1–120 seconds. Invalid input returns 400; cross-origin or invalid-host requests return 403; unsuccessful runs return 503 with a generic message. No tracebacks or local paths are sent to the browser.

The JSON projection contains:

- `schema_version`, `run_id`, `generated_at`, `mode`, `offline`, `duration_ms`.
- `cases`: `id`, `title`, `description`, `action`, `arguments`, `decision` (`block` or `allow`), `reason`, `rule_id` (nullable), `upstream_calls`, `receipt_id`, `response_text`.
- `summary`: `blocked`, `allowed`, `upstream_calls`, `blocked_upstream_calls`, `receipts`, `audit_events`, `signatures_verified`, `chain_verified`.
- `verification`: `ok`, `signature_ok`, `integrity_ok`, `event_count_ok`, `manifest_consistent`, `events_checked`, `scope`.
- `evidence`: `url` (`./evidence.zip` for recorded data, `/api/evidence` for live data), `sha256`, `files`, `scope`.

The adapter derives policy IDs, reasons, tools, arguments and decisions from actual exported decision events linked by response receipt ID. It checks that blocked responses report errors and allowed responses do not. Upstream contact counts come from a wrapper around the existing mock transport, which still delegates to its original handler. Manifest counts are cross-checked against exported events and responses; signature and hash-chain checks come from the real verifier. The expected three blocks, one allow and zero blocked upstream contacts are acceptance checks against those measurements, not fabricated result values. The audit contains five events: one startup and four decisions.

## Evidence and trust boundary

Extract the ZIP and use this checkout's environment to verify the engine evidence offline:

```sh
/home/sainath/tessera-demo-venv/bin/tessera audit verify --dir <extracted-bundle-directory>
```

Embedded-key verification proves bundle consistency, not externally pinned signer identity. The key embedded in the manifest can verify the event-chain signature, but a party able to replace the entire bundle can generate a new key and bundle. Independently pinning an expected signer is outside this demo.

The JSON projection and mock-transport counters are presentation/instrumentation data, not separately signed claims. The ZIP's SHA256 links the displayed projection to its downloadable bytes; independently check the signed event evidence inside the ZIP. The bundle's HTML and instructions are convenience renderings; the core verifier checks the event hash chain, signature payload and manifest consistency, not signatures over every rendered file.

Each run executes in a separate Python child because core demo setup temporarily changes environment and logging. The parent forwards only a small environment allowlist, supplies temporary HOME/state/scratch, and the child denies IP socket connections and DNS resolution. Static serving rejects path escapes and directory listing, and the API accepts no caller-provided tool or scenario. This is a local presentation aid rather than a multi-user service.

Manual smoke verification covered a recorded export, a fresh HTTP run, actual ZIP contents and SHA256, static JSON retrieval, arbitrary-input rejection and cross-origin rejection. No test files were added.
