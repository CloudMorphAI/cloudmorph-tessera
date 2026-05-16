# Tessera smoke-test scenarios

Human-readable scenario files that describe concrete user journeys to exercise manually before each release. Not pytest, not playwright — these are walkthroughs for a human operator (or a future automated runner) that pin starting state, actions, expected observable result, and failure modes by subsystem owner.

## Why these exist

Releases land on PyPI + GHCR + ECR every few weeks. Unit tests cover the engine; integration tests cover the OAuth/CDN paths. What's missing is the end-to-end customer journey check: install → configure license → fetch intelligence → enforce a policy → see the audit log. These scenarios fill that gap.

They are also source material for marketing/demos (Antler reviewers, Show HN) and the seed for an eventual playwright-style automated smoke runner.

## What's in scope

Six scenarios covering the highest-value paths:

| # | Title | Requires |
|---|---|---|
| 01 | Fresh install + import smoke | Local Python ≥3.11 |
| 02 | Intelligence fetch + verify | Live CDN + valid license JWT |
| 03 | MCP call allowed by policy | None (bundled policies) |
| 04 | MCP call blocked by cost cap | None (bundled policies) |
| 05 | Tier downgrade — cached packs persist | Live CDN + license-tier transition |
| 06 | Anonymous CDN fetch returns 401 | Live CDN |

## How to run them

Open the scenario file. Read the "Starting state" block to set up. Run the "How to verify manually" commands as the operator. Check each "Expected observable result" line by line. If any one fails, the "Failure modes to watch for" block names the likely owner subsystem.

A clean release run-through is all 6 scenarios green. Skip-with-reason is acceptable for the CDN-dependent ones if the CDN is undergoing maintenance — note the skip in the release commit message.

## Adding a new scenario

Copy the template at the top of any existing scenario file. Keep under 80 lines. Reuse real subsystem names from `arch/status/` so the failure-mode-to-owner mapping stays tight.
