# How Tessera is Built

Tessera is built by one person, using AI as a genuine pair programmer rather than an
autocomplete tool. This page explains what that actually looks like day to day.

## The setup

The founder works full-time in infrastructure and builds Tessera in the evenings and on
weekends — roughly 20 hours of human time per week. Claude Code (running Claude Sonnet and
Opus models from Anthropic) handles the majority of code execution, while the human role is
architecture, judgment calls, and final verification.

This is not the typical "AI writes boilerplate" story. The AI is involved from the first
line of a spec through to the final diff review.

## How decisions get made

Before any meaningful feature is coded, it goes through a spec review. The process:

1. The founder drafts a rough problem statement — what needs to change and why.
2. Opus reviews the draft, asks clarifying questions, and returns a structured spec: data
   models, API shape, failure modes, security considerations, and open questions.
3. The founder reviews the spec, resolves the open questions, and approves or revises.
4. Only then does any code get written.

This upfront cost pays off. Reworking a spec takes minutes. Reworking code that was built
on a bad spec takes days.

## How code gets written

Once a spec is approved, Sonnet executes it. The typical pattern is a batched task list —
anywhere from 5 to 20 concrete sub-tasks queued up in a single session prompt. Sonnet works
through them sequentially, surfacing blockers and flagging anything that deviates from the
spec.

The founder reviews the diff before anything is committed. No automated merge. No CI gate
that substitutes for a human read.

## Overnight sessions

One of the more unusual parts of this workflow: the founder queues up a large batch of tasks
before sleeping, and Sonnet works through them overnight. The next morning there is a
completed diff ready for review. This compresses timelines significantly — tasks that would
take two full weekends of interrupted evening work can be turned around in 48 hours.

The sessions are structured to be autonomous-safe. Tasks are scoped so that if Sonnet hits
an ambiguity, it documents the question and continues with the rest of the batch rather than
blocking. Nothing gets committed without a human decision at the end.

## Why this works for a solo founder

20 hours per week of human time is not a lot. What makes it viable:

- Human time is spent on judgment — architecture, security review, product decisions.
- Mechanical work — boilerplate, refactors, test scaffolding, documentation — goes to the AI.
- The spec-first discipline prevents the most common failure mode: building the wrong thing
  quickly.
- Async overnight sessions mean the effective throughput is closer to 40-50 hours of work
  per week, from 20 hours of human input.

## Honest limitations

This approach has real gaps, and it is worth naming them:

**No human code reviewers.** The founder reads every diff, but there is no second engineer
checking the work. Subtle bugs — the kind a second reader catches immediately — can survive
to production.

**Testing is manual.** Automated test coverage is minimal at v0.1. The founder verifies
behavior manually before shipping. This is a known risk that will be addressed as the
project matures.

**AI can miss context.** Sonnet executes what is in the spec. If the spec is incomplete,
the code will be incomplete. The spec review step catches most of this, but not all.

**Overnight sessions need careful scoping.** A poorly scoped overnight task can produce a
large diff that solves the wrong problem. The session structure has been refined over several
months to minimize this, but it is not a solved problem.

None of these are reasons to abandon the approach. They are reasons to be disciplined about
the process and honest about the current state of the software.
