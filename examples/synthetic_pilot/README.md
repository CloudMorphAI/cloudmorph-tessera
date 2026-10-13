# Offline synthetic pilot records

This development tool uses the actual Tessera FastAPI policy engine and
Streamable HTTP adapter with in-process synthetic tools. It starts no listener,
denies IP sockets/DNS, calls no model/cloud API and uses no production keys.
It runs in a child with a scrubbed environment and temporary home directory;
the internal worker also enforces isolation when invoked directly.

From WSL in this checkout, using an environment with the package dependencies:

```sh
python examples/synthetic_pilot/run.py --output-dir /path/to/new-evidence-directory
```

Always use a new directory. Existing evidence is never overwritten. The command
exits0 only when its measured checks pass; failure/timeout/interruption retains
the completed journal and progress. A failed run is not proof of acceptance.

The run covers local initialization and empty notification202, JSON/SSE upstream
discovery, response-ID correlation, a policy-blocked fictional deletion with zero
tool contacts, a permitted fictional read, per-principal session separation,
upstream initialization/version headers, Origin/protocol rejection, authenticated
GET405 and persisted audit-chain/decision-receipt linkage. Assertions require the
named policy decision and unique matching tool/arguments/principal receipts.

Loose output files:

- `run.json` and `SUMMARY.md`: acceptance results, measured requests/responses,
  contact counts, source/policy hashes and remaining gates.
- `events.jsonl` and `audit.db`: actual synthetic decision/audit records.
- `journal.jsonl` and `progress.json`: durable checkpoints after each stage;
  interrupted runs can be investigated without rerunning over their evidence.
- `policies/`: exact generated synthetic allow/block rules.
- `failure.json`: failure information when applicable; never an attestation.

Records are **unsigned**. Hash-chain verification checks internal consistency;
it does not establish an independently trusted signer or cover mock counters
with signatures. No ZIP is generated. All names and data are fictional.

This is not an independent security assessment, a 500-case corpus, real partner
pilot, desktop-client transcript, published release or deployment. Use the
[pilot preparation records](../../docs/pilot/README.md) to collect those separate
approvals and results. Current archived content has unresolved runtime-schema
and price-reference findings; select reviewed policies before any real pilot.
