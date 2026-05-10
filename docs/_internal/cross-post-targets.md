# Cross-Post Targets — Tessera Launch 2026-05-12

**Status:** Draft — no posts submitted yet.

| Channel | Char limit | Key constraint | Hook / angle for that channel |
|---|---|---|---|
| **Hacker News (Show HN)** | Title ≤80 chars; body uncapped | Self-promo allowed under Show HN label. Must be something you built. No markdown in post body. | Title: `Show HN: Tessera – open-source MCP firewall with 14 reference policies` — lead with the Cursor+Jira 0-click as the concrete risk, then explain the hash-chain audit. HN rewards specificity; cite the August 2025 exploit by name. |
| **Reddit r/LocalLLaMA** | Title ≤300 chars; post body uncapped | Post in the weekly "Self-Promo / Projects" thread if active (avoids mod removal). Outside that thread: must be framed as discussion, not announcement. | Angle: "I built a deterministic MCP firewall after the Cursor+Jira 0-click — no LLM in the enforcement path, 14 reference policies included." r/LocalLLaMA skews technical; the "no ML in enforcement path" differentiator lands here. |
| **Reddit r/programming** | Title ≤300 chars; post body uncapped | Pure self-promo removed by mods. Reframe as a technical article: "How to add a policy firewall to your MCP setup." Link to the blog post as a reference. Comment with Tessera link once discussion starts. | Angle: technical write-up on the MCP security problem class. Use the August 2025 exploit as the hook. Do not lead with product name in title. |
| **dev.to** | No enforced char limit | Max 4 tags. Articles indexed by Google within ~24 hrs. Reactions and comments drive algorithm placement on homepage. | Tags: `mcp`, `security`, `llm`, `aiagents`. Title matches blog post title. Add a "TL;DR" callout block at the top — dev.to readers scan. Cross-link to GitHub repo and Cursor recipe in body. |
| **Hashnode** | No enforced char limit | Canonical URL should be set to `cloudmorph.ai/blog/tessera-launch` to avoid duplicate content penalty. Hashnode has its own SEO distribution and feeds to Hashnode digest. | SEO title: "Open-Source MCP Firewall: Deterministic Policy Enforcement for AI Agents". Set canonical to cloudmorph.ai. Include the ASCII proxy diagram — Hashnode renders code blocks and diagrams cleanly. Publish to Hashnode's "Security" publication. |
| **LinkedIn** | ~3,000 chars for articles; ~1,300 chars for text posts | Text posts outperform link posts in algorithm. Paste blog content summary as native text; add repo link in first comment. 3 hashtags max for reach (more hurts). | Hook line: "AI agents call your production database. Nothing checks them. I built the firewall." Hashtags: `#mcp #agentic #opensource`. End with a specific CTA: "Link to the 60-second demo in comments." |
| **Twitter/X thread** | 280 chars per tweet; threads uncapped | Images and videos get ~2x organic reach vs text-only. First tweet is the hook — algorithm weights thread continuation on whether tweet 1 gets early engagement. | 5-tweet structure: (1) Hook — "The Cursor+Jira 0-click attack worked because there was no firewall between the agent and the tools. Tessera is that firewall." (2) Problem — one paragraph, cite the exploit. (3) How it works — the ASCII diagram as an image. (4) Demo link — 60-second Cursor recipe. (5) CTA — "Star the repo. Try the recipe. Tell us what the policy library is missing." |

## Notes on timing

Post HN first (9:00 AM PT). If HN gains traction in the first two hours, reference the HN thread in all subsequent posts as social proof. If HN is flat, proceed independently — do not wait.

For r/LocalLLaMA: check the weekly thread status on the morning of 2026-05-12. If no weekly thread is pinned, post as a standalone with the "framed as discussion" angle noted above.

For LinkedIn and Twitter/X: schedule natively or via Buffer. Aim for 9:30 AM and 11:00 AM PT respectively (see `launch-schedule.md`).
