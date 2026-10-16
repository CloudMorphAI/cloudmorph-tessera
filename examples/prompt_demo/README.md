# Prompt → maker → Tessera checker → synthetic system

This backend accepts natural-language input, calls Amazon Bedrock Nova Lite through Converse, and sends every valid proposed tool call through the actual Tessera `create_app`/`TestClient` `/mcp/demo` gate. The model decides what to propose; it can also answer without using a tool. No scripted proposal replaces a model refusal or unavailable provider.

All executors are fixed synthetic Python fixtures behind `httpx.MockTransport`. They cannot make payments, access GitHub, execute SQL, read files, run commands or contact cloud systems. `read_file` checks supplied synthetic text; it does not read a file. `list_dir` returns canned fictional project names. The real external calls in the deployed application are the admission counter and the named model provider. Use synthetic input: prompts are sent to Bedrock, and tool arguments can occur in the temporary local audit database.

## Runtime and packaging

Python 3.12; Lambda entry point `handler.lambda_handler` when `handler.py` and `runtime.py` are placed at the archive root beside `policies/` and the `tessera/` package. Include the three bundled `tessera/policies_default` YAML files and these two local YAML files. The existing Tessera runtime dependencies from `pyproject.toml` are needed, plus `cryptography` (audit verifier/signing imports), `boto3`/`botocore` (deployed transports). `fastapi.testclient` requires `httpx`. This example adds no dependency installation or infrastructure rollout.

Environment:

- `BUDGET_TABLE`: DynamoDB table with String partition key `budget_key`; Number TTL attribute `expires_at`. Role needs only `dynamodb:UpdateItem` on this table.
- `ALLOWED_ORIGINS`: exact comma-separated HTTPS site origins. Localhost is accepted only when its exact origin/port is explicitly included. No wildcard and no missing Origin on POST.
- `AWS_REGION`: model region, default `us-east-1`. Fixed model/inference profile: `us.amazon.nova-lite-v1:0`. The role needs scoped model/profile inference permissions selected by deployment.

Configure API routes `GET /health`, `POST /chat` and exact-origin CORS in the dedicated deployment. Health is configuration information, not proof of model access, and consumes neither budget nor inference. No sessions, conversations, audit files or global “latest evidence” are shared between requests. The browser submits `{ "message": "Pay vendor_acme 200000 rupees" }` with JSON Content-Type and its Origin. Only that field is accepted. Responses are `Cache-Control: no-store`; raw prompts, arguments, outputs and exception traces are not logged by this example.

The backend reserves one request with a single atomic conditional DynamoDB increment, maximum 100 per UTC day globally. A missing/unavailable table fails closed; exhausted budget returns 429. Failed inference still consumes its reservation, bounding admitted attempts. API throttling/concurrency are additional deployment controls. Origin validation is a browser boundary, not authentication or an abuse-proof spend control: nonbrowser clients can forge Origin and consume the shared allowance.

Maximum request body is 8,192 decoded bytes; prompt is 1,400 characters. Each run has at most two Converse calls, 512 output tokens each and four proposed tool calls total. An oversized tool batch is not dispatched. A 24-second run deadline includes admission, and model time is checked before another turn; SDK connect timeout is two seconds and read timeout eight seconds with `total_max_attempts=1` (initial attempt only, no retry). A second model call starts only with at least 10.5 seconds remaining. These transport/deadline guards accompany the deployment's hard Lambda timeout. Character/output limits are not a precise token or dollar cap. History exists only within this run; no continuing conversation endpoint is provided.

## Decisions and evidence

Every request gets fresh temp policies, fixture state, deployment scope and SQLite audit. Three actual bundled policies block synthetic secret patterns, protected-branch force pushes and destructive SQL on critical tables. The unchanged local `arg_greater_than` payment policy blocks amounts above INR 150,000; INR 150,000 itself is allowed. The added fixed AP fixtures allow invoice lookup, a fake-ledger payment for a separate approved INR 25,000 invoice, and a review-only draft/readback for the INR 500,000 exception invoice. A strict validator rejects unknown tools, unknown arguments/payees, bool/fractional/negative amounts and out-of-fixture targets before forwarding any member of that batch. Invalid proposals fail the HTTP run, rather than fabricate a Tessera policy decision. Validated tools then encounter the real engine with `default_action: block` and the explicit synthetic tool allowlist, after the payment-cap rule. `create_payment_draft` accepts only the fixed invoice ID and has no amount argument; it stores a request-scoped draft at the original INR 500,000 amount with `awaiting_human_approval`, `payment_authorized: false`, and `automatic_payment: false`.

The AP fixture run uses two independent requests so it stays within the existing four-tool-per-request limit and the request-local draft scope. The first reads `INV-DEMO-025` and records its INR 25,000 payment only in an in-memory fake ledger. The second reads `INV-DEMO-500`, proposes the full INR 500,000 payment (blocked before the adapter), then makes a new Tessera call to create and read back the human-approval draft. The fixture never retries, lowers, or splits the blocked payment. The policy remains a **per-action** cap and does not provide an aggregate anti-splitting control for arbitrary caller proposals.

Success response fields are `request_id`, `model`, `mode`, `assistant_text`, ordered `events`, `usage`, `summary`, and `audit`. The mode distinguishes the live model handler, injected provider tests, and the scripted offline connector flow; `model` is null unless the live handler selects its Bedrock Converse client. Valid proposals have `type: maker` (tool, arguments, tool_use_id), `type: checker` (tool, tool_use_id, actual decision/reason/policy_id/receipt_id), and `type: system` (tool, tool_use_id, measured contacted/call_count and synthetic result or null). Successful responses use only those three event types. Invalid/oversized proposals return HTTP 502 without a successful partial transcript. Time/turn limits append an explicitly labeled `Demo limit:` notice. UI must treat checker/system cards as authoritative independently of model narrative. A live model may have zero proposals, and a second-turn proposal may end at the two-turn limit without a third narrative call.

Checker fields are resolved from actual audit rows by the receipt ID returned by the gate after its lifespan drains. The chain verifier recomputes the stored event hashes and links; `event_count` includes startup. Contact counts come from the counted synthetic transport; any allowed/contact mismatch or blocked contact fails the run. These measurements and model text are not signed audit evidence. The API does not return a ZIP, assert an Ed25519 signature or prove externally pinned signer identity. `audit.chain_verified` reports only the actual local hash-chain check/count completeness. Scratch data is removed on completion; Lambda process termination may leave a scratch directory until that worker is discarded, and it is never exposed through an endpoint.

The existing demo environment/logging contexts restore global process state. The runtime rejects simultaneous local calls; Lambda handles one invocation at a time per execution environment. Use one worker per invocation and do not embed this example in a concurrent threaded HTTP handler. No core Tessera behavior is modified.

## Offline manual smoke and AP scenario

Using the existing prepared WSL environment from the repository root:

```sh
/home/sainath/tessera-demo-venv/bin/python examples/prompt_demo/runtime.py --smoke
```

This injects fixed, bounded transcripts and exercises the real checker and audit chain. It verifies the existing payment-cap/protected-branch/SQL/secret blocks, the cap-boundary allow, and the AP flow below. In a Python 3.12 test environment with the repository runtime dependencies, `cryptography`, and pinned `pytest==9.0.3`, run the focused boundary/fixture tests with:

```sh
python -m pytest examples/prompt_demo/tests/test_ap_scenario.py
```

The prepared prompt-demo smoke venv may not include pytest; keep it unchanged and use a separate test environment when needed.

For a compact machine-readable scenario receipt, use `runtime.py --ap-scenario`. Both commands use scripted tool proposals through Tessera and local `httpx.MockTransport` fixtures. They make no Bedrock/model-provider request and no financial-service request. Each result has `mode: scripted_connector_tool_flow_offline`, `model: null`, per-request tool-use IDs and audit receipt IDs, measured adapter-contact counts, and a verified local audit hash chain. The expected AP totals across the two requests are five synthetic adapter contacts, one blocked INR 500,000 payment with zero contacts, one separate INR 25,000 fake-ledger row, and one INR 500,000 draft awaiting human approval. This is not an LLM run or customer acceptance. The live handler reports the model mode only when it selects its default Bedrock Converse client; injected providers are labeled separately.

Both model and budget callables can be injected into `handler.handle` for offline request validation/failure checks without importing boto3. The fake invoice/payment/draft state is reset for each request. No approval fulfillment, automatic retry or payment integration exists in this example.

Deployment, IAM, API throttling and live dev-host verification are owned separately. Source implementation and offline smoke do not establish deployment or live model availability. The recorded standalone demo remains available as an explicitly recorded fallback.

## Bounded live Nova AP scenario

For an actual model-driven run against the same local-only Tessera and fake AP fixtures, run this command from WSL in the prepared Python 3.12 environment:

~~~sh
python examples/prompt_demo/live_nova_ap.py
~~~

The runner invokes the Windows AWS CLI at /mnt/c/Program Files/Amazon/AWSCLIV2/aws.exe with explicit `--profile Karn` and `--region us-east-1`; Converse requests contain only synthetic prompt/tool JSON and responses return over stdout. The child process receives only the fixed controls `AWS_MAX_ATTEMPTS=1`, `AWS_RETRY_MODE=standard`, and `AWS_PAGER=""`. It does not inherit the WSL environment, including credential variables, profile/config path overrides, web-identity/container credential settings, or endpoint overrides. WSL/Windows interop retains the Windows process's normal user home for profile lookup. The demo never reads or copies profile or credential files.

Before inference, the runner makes read-only AWS preflight calls: `sts:GetCallerIdentity`, Bedrock's model catalog lookup, Bedrock model-availability lookup, and AWS Price List `get-products` lookups for input/output rates. These are control-plane/read-only queries. The separate Bedrock Converse calls are the actual bounded model inference; there are no other cloud writes or finance integrations. The runner stops if the conservative six-call estimate exceeds USD 0.01.

The runner selects amazon.nova-micro-v1:0, uses a 256-token output limit, a 12,000-byte maximum serialized request, a two-turn limit per run, one CLI attempt per call, a 20-second call timeout and a 150-second overall deadline. With the preflight-verified us-east-1 prices, the conservative bound is under USD 0.006; each receipt records the actual returned input/output token usage and price-derived estimate. AWS documents client-side function calling for Nova models through Converse in its [Nova tool-use guide](https://docs.aws.amazon.com/nova/latest/userguide/tool-use.html).

The flow is three model-driven runs: the small approved invoice gets a fresh lookup and exact INR 25,000 fake-ledger payment; the exception invoice gets a fresh lookup and one full INR 500,000 proposal, whose Tessera block ends that conversation with no retry/split turn; only after observing that block does a new request expose the draft and readback tools. Each turn forces one fixed synthetic tool schema, and a pre-gate scenario guard stops any unexpected tool name or arguments before they reach Tessera. The draft request includes the prior block's actual policy ID/reason for context, but its draft/readback actions are new Tessera evaluations with their own request-local audit chain.

All accepted model proposals pass through the actual local /mcp/demo Tessera policy gate and an httpx.MockTransport fake adapter. Apart from the read-only preflight and bounded Bedrock Converse inference described above, the runner does not call handler.py, the live HTTP/DynamoDB admission path, any payment service, or any other cloud action. Receipts are written under examples/prompt_demo/live_nova_receipts/ with mode live_model_synthetic_tools, the actual model ID, tool-use/audit receipt correlations, checked decisions, measured mock-adapter contacts, token usage and price estimate. They contain only synthetic prompt/fixture content. This is a live model result with local fake tools; it is not a customer, payment or production acceptance.

### Mocked live-harness regression checks

Run the offline fixture tests and the live-harness regression tests from the repository root with:

~~~sh
python -m pytest examples/prompt_demo/tests/test_ap_scenario.py examples/prompt_demo/tests/test_live_nova_ap.py
ruff check examples/prompt_demo/live_nova_ap.py examples/prompt_demo/tests/test_live_nova_ap.py
~~~

`test_live_nova_ap.py` stubs the Converse response path; it makes no Bedrock request and never launches the AWS CLI. It labels these responses `injected_provider_synthetic_tools` with `model: null`. Coverage includes exact tool proposal and response validation, unknown actions, per-run and global turn caps, request/output token limits, and a full Tessera block path with zero synthetic payment-adapter calls. That path uses the in-process `httpx.MockTransport` fixture and SQLite audit sink; it does not import or call `handler.py` or DynamoDB. These tests exercise the new harness without asserting or implying another live model run.

The actual 16 October model receipt also has a receipt-derived [trace replay](live_nova_receipts/ap-nova-b4aa2fb56e484c9ca981924dfe45736a.trace-replay.md). It lists the observed provider tool-use responses and correlated Tessera/system events; it is not a new model run, audiovisual capture, or assistant conversation.
