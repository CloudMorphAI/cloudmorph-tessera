# Sales demo — 5 minutes, one command

Audience: a technical buyer (platform/security engineer, eng lead) evaluating whether to put a policy firewall in front of their agents' MCP tool calls. Goal: they see a real destructive call get blocked in real time, then see the tamper-evident record of that block, and they leave with the EU AI Act 2026-08-02 date as a reason to move now.

Everything below runs off one command:

```bash
tessera demo
```

Zero config. Run it on your laptop, screen-share the terminal, done in under 60 seconds if you don't pause. Budget 5 minutes with the talk track.

---

## Before you start

- Have the terminal full-screen, font size bumped up. This is a terminal demo — no slides needed.
- Know your audience's environment: if they run RDS, lead with "this is your production database." If not, say "swap AWS for whatever your agents touch — GCP, Azure, GitHub, Slack — same mechanism."

---

## 0:00 — Frame the problem (before running anything)

**Say:**

> "Your AI agents are about to get tool access to production — AWS, your data warehouse, your ticketing system. The moment an agent can call `DeleteDBInstance` or `AttachRolePolicy`, a bad prompt or a hallucinated argument is a production incident, not a bug report. And starting August 2, 2026, if you're running a high-risk AI system, the EU AI Act requires you to have an automatic, tamper-evident record of what it did. Let me show you what sits between an agent and that outcome."

Run:

```bash
tessera demo
```

## 0:30 — The header

The command prints a header identifying what it's about to do: fire a real destructive AWS call through Tessera's live `/mcp` endpoint, unmodified, exactly as an agent would send it.

**Say:**

> "This isn't a mocked screenshot. This is a real MCP `tools/call` request hitting a real proxy. Watch what comes back."

## 1:00 — The dangerous call

`tessera demo` fires:

```
aws_rds_DeleteDBInstance(SkipFinalSnapshot=true)
```

against the running proxy's `/mcp` endpoint — the exact shape an agent would send if it decided (correctly or by hallucination) to tear down a database with no recovery point.

Tessera returns the block verbatim in the terminal:

```
POLICY_BLOCK
policy_id: demo-block-rds-delete
reason: SkipFinalSnapshot on a production-tagged RDS instance is not allowed — this is an irreversible-data-loss operation with no recovery point.

This is a final decision from the Tessera policy gate. Do NOT retry with
different parameters — the policy will reject equivalent calls. Surface
this block to the user.
```

**Say, pointing at the output:**

> "That's it. No LLM scored this, no heuristic guessed at intent. A deterministic policy — `demo-block-rds-delete` — matched the call and stopped it before it reached AWS. Same policy, same call, same answer every time. That determinism is what makes the record we're about to look at defensible instead of 'the model said so.'"

Pause here. Let the block sink in before moving on — this is the moment that sells the product.

## 2:00 — The safe call, allowed

`tessera demo` then fires a second, harmless call:

```
aws_rds_DescribeDBInstances
```

Terminal shows it going through as **ALLOWED** — normal MCP response, no block text, no friction.

**Say:**

> "Tessera isn't a blanket lockdown. Read-only, safe calls sail through with effectively zero added latency — this evaluated in well under a millisecond. The firewall is selective by design: it stops the calls that matter and stays invisible for everything else. That's the difference between a security control people route around and one they don't notice."

## 3:00 — The evidence bundle

`tessera demo` now exports a mini evidence bundle from the two calls you just made and prints its path, e.g.:

```
Evidence bundle written to: ./tessera-demo-evidence/
Verify offline with:
  tessera audit verify --dir ./tessera-demo-evidence
```

Run the verify command it prints:

```bash
tessera audit verify --dir ./tessera-demo-evidence
```

**Say while it runs:**

> "That bundle — `events.jsonl`, a signed `manifest.json`, an HTML summary, and a `VERIFY.md` with the exact offline steps — is Ed25519-signed. `audit verify --dir` just recomputed the hash chain and the signature independently, from the raw events, with zero network access. It didn't trust the manifest's claims — it recomputed them. If anyone edited a single byte of that block decision after the fact, this command catches it."

Point out the exit code came back clean (0) — this is the same command their CI or compliance team would run.

## 4:00 — Open the summary, land the compliance hook

Open `./tessera-demo-evidence/summary.html` in a browser (or `open`/`start` it live).

**Say:**

> "This is the artifact you hand to an auditor or a customer's security review — not log access to your production systems, a self-contained, independently verifiable record. It's already mapped against EU AI Act, India DPDP, and SOC 2 obligations, so you can see exactly which requirements this evidences and which it doesn't — we don't oversell it.
>
> The EU AI Act's record-keeping and traceability requirements — Articles 12, 19, 26 — become enforceable August 2, 2026. If you're deploying agentic AI in scope of that regulation, 'we have logs somewhere' won't hold up. This is what a defensible answer looks like, and it took one command to produce."

## 4:30 — Close

**Say:**

> "Everything you watched — the block, the allow, the signed bundle, the offline verify — runs from `pip install cloudmorph-tessera`, no account needed to try it. The 25 bundled policies cover the AWS attack primitives CIS and NIST call out by default; your own policies, like the one that just fired, are one YAML file. Want to point this at your actual MCP servers?"

Hand off to `QUICKSTART.md` for the real integration path (Tessera Cloud login, dashboard policy authoring, wiring into their IDE).

---

## If something goes wrong live

- **`tessera demo` errors on startup** — run `tessera doctor` first; it checks the environment (Python version, port availability, writable `~/.tessera/`) and prints what's missing before you're in front of a customer.
- **Port conflict** — `tessera demo` binds a local port for the duration of the run; close other local Tessera/proxy instances first.
- **No terminal color / small font** — worth a 30-second check before the call starts. The POLICY_BLOCK text is the visual payoff; make sure it's readable.
