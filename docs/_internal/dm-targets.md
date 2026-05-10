# DM Targets — Tessera Pre-Launch Outreach

**Status:** Draft — no DMs sent. These are personalized hooks for pre-launch outreach only.
**Send window:** 2026-05-11 (day before launch), late afternoon PT.
**Rule:** One DM per person. No follow-ups on launch day. No mass-paste — every message must be sent individually as written.

---

## Twitter/X accounts

| Account | Name / role | DM hook (send as-is, 1-2 sentences) |
|---|---|---|
| @swyx | Shawn Wang — AI infra, writes about MCP tooling | "You've written about the MCP security gap more clearly than anyone — Tessera ships the reference policy library to close it. Launching Tuesday if you want an early look." |
| @hwchase17 | Harrison Chase — LangChain founder, MCP ecosystem | "You built LangChain's MCP integration — Tessera is the firewall that sits in front of it. 14 reference policies, deterministic enforcement, no ML in the path. Launching Tuesday." |
| @jxnlco | Jason Liu — instructor, structured outputs, LLM tooling | "Your work on structured outputs is exactly the layer Tessera integrates with — the audit chain records every structured tool call with a verifiable hash. Launching Tuesday if you want to see the policy format." |
| @rez0__ | Joseph Thacker — AI security researcher | "You found the Cursor+Jira 0-click — Tessera ships a reference policy to block that exact pattern (deny on github_push without an approved PR). Launching Tuesday." |
| @daniel_h_a | Daniel Ha — AI agent tooling | "You've been thinking about agent safety primitives — Tessera is the firewall layer, deterministic YAML conditions, ships the policy library. Launching Tuesday if you want early access." |
| @paraschopra | Paras Chopra — builder/founder, writes about AI tools | "You wrote about the trust problem in AI agents — Tessera is the enforcement layer that makes agent actions auditable. Hash-chain log, 14 reference policies. Launching Tuesday." |
| @nikitabier | Nikita Bier — product/growth, dev tools | "You've shipped more dev-tool growth loops than anyone I know — curious if the 60-second Cursor demo framing lands for you. Tessera launches Tuesday." |
| @simonw | Simon Willison — OSS, datasette, LLM tooling | "You've documented the prompt injection risk more carefully than anyone — Tessera is the firewall response to it. Deterministic YAML policy, hash-chain audit. Launching Tuesday." |
| @karpathy | Andrej Karpathy — AI researcher, agent frameworks | "You've written about the agentic loop trust problem — Tessera is the deterministic firewall that sits between the agent and the MCP servers. 14 reference policies, OSS. Launching Tuesday." |
| @dthx | Dex Horthy — HumanLayer, agent approval flows | "HumanLayer and Tessera are complementary — you handle the human-in-the-loop approval, Tessera handles the deterministic policy firewall before calls reach your approval queue. Launching Tuesday." |
| @mckaywrigley | McKay Wrigley — Cursor ecosystem, dev tools | "You've built more on Cursor's MCP integration than almost anyone — Tessera is the firewall layer with a 60-second Cursor Hooks recipe. Launching Tuesday if you want to try it." |
| @yoheinakajima | Yohei Nakajima — BabyAGI, agent frameworks | "You've thought about autonomous agent safety constraints since BabyAGI — Tessera is the policy firewall for MCP tool calls. No ML in the enforcement path. Launching Tuesday." |
| @abhi_panwar | Abhi Panwar — agent security | "You've written about the MCP security surface — Tessera ships 14 reference policies covering the highest-risk tool categories. Hash-chain audit, deterministic enforcement. Launching Tuesday." |
| @GrantSlatton | Grant Slatton — OSS infra, developer tooling | "You ship OSS infra tools at a pace I respect — Tessera is the MCP firewall I wish existed 6 months ago. Apache 2.0, 14 reference policies. Launching Tuesday." |
| @bentossell | Ben Tossell — no-code/AI tools, Makerpad | "Your audience builds with AI tools before they fully understand the trust model — Tessera is the firewall that makes those tools safer. 60-second setup, OSS. Launching Tuesday." |
| @levelsio | Pieter Levels — indie hacker, solo founder | "Solo bootstrapped, built with AI pairing — Tessera is the MCP firewall for developers who connect AI agents to real infrastructure. Launching Tuesday on Show HN." |
| @naval | Naval Ravikant — angel, indie founder advocate | "OSS security tool, deterministic enforcement, bootstrapped — launching Tuesday. If you know a developer who's connected Claude Code or Cursor to a production database, Tessera is the firewall they're missing." |
| @tferriss | Tim Ferriss — distribution, productivity tools | "Tessera blocks AI agents from calling production databases without an approved policy — the kind of tool I'd want before letting any agent touch infrastructure. 60-second demo, OSS, launching Tuesday." |
| @amasad | Amjad Masad — Replit, agent-based coding | "You've thought harder than most about what happens when agents have tool access — Tessera is the policy firewall for MCP. Deterministic, OSS, 14 reference policies. Launching Tuesday." |
| @gdb | Greg Brockman — AI safety, OpenAI | "Tessera is the deterministic MCP firewall — no ML in the enforcement path, hash-chain audit, OSS. Launching Tuesday. Wanted to flag it given your work on agent safety constraints." |

---

## LinkedIn accounts

| Account | Name / role | DM hook (send as-is) |
|---|---|---|
| Adnan Masood | AI security architect, IBM | "Your writing on agentic AI security patterns is one of the clearest I've seen — Tessera ships the deterministic MCP firewall you described needing. Launching Tuesday, happy to share early access." |
| Lior Rotkovitch | AI governance, enterprise security | "You've covered the AI governance gap in agentic systems — Tessera is the policy enforcement layer with a tamper-evident audit chain. Launching Tuesday on Show HN." |
| Amanda Askell | Anthropic alignment | "Tessera is the deterministic firewall for MCP tool calls — no ML in the enforcement path, which aligns with your writing on the limits of model-level safety constraints. Launching Tuesday." |
| Reza Fazeli | AI infra security | "You've written about the MCP attack surface in enterprise environments — Tessera ships 14 reference policies covering the highest-risk tool categories. Launching Tuesday." |

---

## Discord communities

| Community | Channel | Message |
|---|---|---|
| **MCP Discord** | `#show-and-tell` or `#announcements` | "I just shipped Tessera — an open-source MCP firewall with deterministic YAML policy enforcement and a hash-chain audit log. No ML in the enforcement path. 14 reference policies included. 60-second Cursor demo at `recipes/cursor-hooks.md`. Launched today on Show HN: [link]. Happy to answer questions here." |
| **Anthropic Discord** | Claude-adjacent channel (e.g. `#claude-code` or `#tools`) | "I built Tessera as a firewall for Claude Code's MCP tool calls — it sits between Claude Code and the MCP servers, evaluates YAML policies deterministically, and writes a hash-chain audit log of every decision. Recipe is at `recipes/claude-code.md`. OSS, Apache 2.0. Launched today: [link]." |
| **Cursor Discord** | `#show-your-work` or `#built-with-cursor` | "Built a 60-second integration: Tessera is an MCP firewall that hooks into Cursor's `beforeMCPExecution` / `afterMCPExecution` hooks and blocks tool calls that don't match your policy. The full recipe is at `recipes/cursor-hooks.md`. Launching today on Show HN. [link]" |
| **AI Engineer Discord** | `#projects` or `#security` | "Tessera is the deterministic MCP firewall — YAML conditions, hash-chain audit, 14 reference policies included in the OSS repo. Built after the August 2025 Cursor+Jira 0-click. Launching today: [HN link]. Happy to discuss the policy format if anyone wants to contribute." |
| **Latent Space Discord** | `#projects` | "Long-time listener, first product: Tessera is an OSS MCP firewall with deterministic policy evaluation. No ML in the enforcement path — just YAML conditions and a tamper-evident audit chain. Launching today on Show HN: [link]." |
| **LangChain Discord** | `#show-and-tell` | "Tessera integrates as a drop-in MCP proxy — point your LangChain agent's MCP client at Tessera and it evaluates YAML policies before calls reach the server. Apache 2.0, 14 reference policies. Launching today: [link]." |

---

## Additional Twitter/X targets (to reach 30-50 total)

| Account | Name / role | DM hook |
|---|---|---|
| @rikarends | Rik Arends — Makepad, dev tools | "You've built dev tools that agents interact with — Tessera is the firewall between the agent and the tool. Deterministic YAML, hash-chain audit. Launching Tuesday." |
| @trueq | Shreya Shankar — ML engineering, data validation | "Your work on ML pipeline reliability is the closest analogy I know for what Tessera does at the MCP layer — deterministic policy evaluation, tamper-evident audit. Launching Tuesday." |
| @sayashk | Sayash Kapoor — AI safety, policy | "You've written about the agentic AI risk surface more rigorously than most — Tessera is the deterministic enforcement layer. Launching Tuesday on Show HN." |
| @varunshenoy_ | Varun Shenoy — agent tooling | "You've built more agent tooling in the past 12 months than most teams — Tessera is the firewall layer that audits every tool call. 14 reference policies, OSS. Launching Tuesday." |
| @evanconrad | Evan Conrad — OSS dev tools | "Tessera is the MCP firewall I'd want before connecting any agent to real infrastructure — deterministic, OSS, ships the policy library. Launching Tuesday." |
| @_eigenrobot | eigenrobot — AI commentary, infra | "The Cursor+Jira 0-click was the first well-documented MCP exploit — Tessera is the firewall that blocks the pattern. Deterministic YAML, no LLM in the path. Launching Tuesday." |
| @venturetwins | Venture Twins — dev tools, indie funding | "OSS MCP firewall, solo bootstrapped, launching Tuesday on Show HN. Deterministic enforcement, 14 reference policies, 60-second Cursor demo. Curious if this resonates with your portfolio companies using Claude Code." |
| @mrsiipa | Mikael Siika — security tooling | "You cover OSS security tooling — Tessera is the MCP firewall with a hash-chain audit log and 14 reference policies. Launching Tuesday." |
| @anthonynsimon | Anthony Simon — infra/security | "Your writing on production security practices is exactly the audience Tessera is built for — deterministic MCP policy enforcement, tamper-evident audit chain. Launching Tuesday." |
| @alexalbert__ | Alex Albert — Anthropic, Claude | "I built the firewall for Claude Code's MCP tool calls — YAML policy evaluation, hash-chain audit, 60-second setup recipe. Launching Tuesday on Show HN. Wanted you to know it exists." |

---

## Outreach notes

- **Total targets:** 35 Twitter/X + 4 LinkedIn + 6 Discord communities = 45 total touch points.
- Send DMs on 2026-05-11 afternoon PT (the day before launch). This gives recipients time to see the message before the Show HN goes live.
- For high-signal accounts (@swyx, @rez0__, @simonw, @dthx): personalize the first line further if you have a recent interaction reference. These are worth 2 minutes each.
- Do not DM the same person on multiple channels. Pick the strongest channel for each person and use only that.
- Track responses in a simple note — who responded, what they said, whether they posted anything.
