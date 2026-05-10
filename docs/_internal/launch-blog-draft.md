# Tessera — the deterministic MCP firewall, open-source, with a working policy library

**Target publish:** 2026-05-12 (Tuesday, Tessera launch day)
**Word count target:** 800–1,200 words
**By CloudMorph AI**

---

## The problem

In August 2025, a security researcher published a working exploit against Cursor and Jira that required zero user interaction beyond opening a ticket. The attack was straightforward in retrospect: a Jira issue contained a crafted comment. Cursor's AI agent read it. The comment was a prompt-injection payload. Cursor, following the injected instruction, called an MCP tool. That tool had full write access to the connected GitHub repository. Within seconds, the agent had silently pushed a change the developer never reviewed.

Nobody in that chain behaved incorrectly. Cursor did what AI editors do — it processed context and called tools. The MCP server did what MCP servers do — it accepted an authenticated request and executed it. Jira did what Jira does — it served a comment. The failure was architectural: there was no firewall between the agent and the tools it could invoke.

This is not an exotic attack surface. Every developer who connects Claude Code, Cursor, or any MCP-aware agent to GitHub, Slack, Jira, or a production database is exposed to the same class of risk. The agent has a tool list. The tools have real permissions. A single injected instruction in any context the agent processes is a potential execution vector.

The MCP protocol is three years old and already has the full trust problem that shell scripting had in the 1990s: it is powerful, composable, and completely without guardrails. Every agent that calls `github_push`, `postgres_execute`, or `slack_post` does so with the full permission set of the authenticated token. No rate limit. No policy check. No tamper-evident record of what was called.

The 2025 Cursor + Jira zero-click was the first well-documented exploit. It will not be the last.

---

## The wedge

Tessera is an open-source MCP firewall. It sits between your AI agent and your MCP servers. Before any tool call reaches the server, Tessera evaluates it against a YAML policy and either permits, blocks, or logs it. After every decision — permit or block — Tessera writes a cryptographically linked audit entry.

Three things separate Tessera from the funded competitors that announced in Q1 2026:

**Purely deterministic enforcement — no ML in the path.** The current generation of MCP security products use large language models to infer "intent" before deciding whether to permit a tool call. That approach has an obvious problem: if your security control is itself a language model, it is itself susceptible to prompt injection. Tessera's enforcement path is YAML conditions evaluated deterministically. `tool_name matches ^github_push$` either matches or it does not. There is no model to mislead, no embedding space to craft an adversarial query against.

**The policy library ships with the OSS version.** Most competitors gate their reference policies behind paid plans. Tessera ships 14 reference policies in the open-source repository today, covering the highest-risk tool categories: destructive GitHub operations, Slack DM exfiltration, database mutations, file system writes outside project directories, and others. You do not pay to see what a sensible policy looks like. You clone the repo and read it.

**Hash-chain audit log.** Every Tessera decision — permit, block, or log — is written to an append-only chain where each entry includes the SHA-256 hash of the previous entry. The chain is verifiable: you can confirm after the fact that no entry was deleted or modified. This is not a marketing feature. In regulated environments, "prove that the AI agent never called that endpoint" is a real requirement that a flat log file does not satisfy.

---

## How it works

Tessera is a lightweight proxy written in Python. You point your AI agent's MCP client at Tessera's address instead of the upstream MCP server. Tessera forwards compliant requests and blocks non-compliant ones before they reach the server.

```
Cursor / Claude Code
       |
       v
  Tessera Proxy  ──► policy eval  (YAML, deterministic, <1 ms)
       |               |
       |               v
       |          audit chain  (SHA-256 linked, append-only)
       |
       v
  MCP Server  (GitHub / Postgres / Slack / etc.)
```

Three enforcement modes:

- **enforcement** — non-matching requests are blocked with a structured error response. The agent sees a denial. The audit chain records the block.
- **log\_only** — all requests pass through, but non-matching requests are flagged in the audit log. Use this when you are building a policy and need to observe traffic before enforcing.
- **observation** — full passthrough with audit logging. No policy evaluation. Use this to baseline what your agents are actually calling before writing any rules.

Policy conditions are evaluated against the full MCP tool call: tool name, input parameters, token identity, and timestamp. A condition like `tool_name matches ^(github_delete|github_push)$ AND NOT request.meta.approved` blocks unapproved pushes and deletes while permitting reads. Conditions are composable — `AND`, `OR`, `NOT` — and the full condition grammar is documented in `docs/POLICIES.md`.

---

## The 60-second demo

The fastest way to see Tessera in action is the Cursor Hooks recipe. It shows Tessera blocking a destructive Cursor action — a `github_push` to a branch with no open PR — within 60 seconds of setup.

See `recipes/cursor-hooks.md` in the repository for the complete walkthrough.

---

## What Tessera is not yet

We are launching transparently, which means being direct about what is missing.

**No Tessera Cloud.** A hosted version — managed proxy, managed audit storage, web dashboard — is on the 30-60 day roadmap. It is not live today. If you need it now, you are self-hosting.

**No Postgres audit sink.** The audit chain today writes to a local append-only file. Postgres and S3 sinks are on the roadmap; they are not shipped.

**No OAuth 2.1.** Token-based authentication (bearer tokens, per-agent tokens) is supported. OAuth authorization server integration is not.

**No SOC 2.** Tessera is a v0.1.0 OSS project. No compliance certifications exist. If your procurement requires SOC 2 Type II before evaluation, Tessera is not ready for you yet. Check back in six months.

**No enterprise SLA.** Support is GitHub Issues and the project Discord. Response times are best-effort.

This list will get shorter over the next 90 days. The public roadmap is at `docs/ROADMAP.md`.

---

## Founder note

Tessera is a solo project, built with Claude Code (Sonnet and Opus) as an AI pair programmer. The policy library depth comes from years of infrastructure work managing cloud-scale AWS environments — the kind of background that makes you opinionated about what "a reasonable default deny rule" actually looks like in production. The project is bootstrapped and Apache 2.0 licensed. The OSS version will not acquire a license key.

If you find a security issue, use the disclosure process in `SECURITY.md`. If you want to contribute, read `CONTRIBUTING.md` first — there is a structured policy review process for submissions to the reference library.

Tessera is available now. Star the repo. Try the Cursor recipe. Tell us what the audit log is missing.

---

*Tessera v0.1.0 — MIT-adjacent Apache 2.0 — [github.com/cloudmorph-ai/tessera](https://github.com/cloudmorph-ai/tessera)*
