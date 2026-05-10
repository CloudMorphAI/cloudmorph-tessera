# Tessera Launch Schedule — 2026-05-12

**Target launch:** Tuesday 2026-05-12
**Fire window:** 9:00 AM – 1:00 PM PT (UTC-7)
**Status:** Draft — not yet executed.

Tuesday morning PT is historically the highest-engagement slot for developer content on HN, Reddit, and LinkedIn. The four-hour fire window is intentional: HN front page momentum peaks in the first two hours; subsequent posts ride that signal rather than competing with it.

---

## Channel sequence

| Time (PT) | Channel | Action | Notes |
|---|---|---|---|
| 9:00 AM | **Hacker News (Show HN)** | Submit Show HN post | Title: `Show HN: Tessera – open-source MCP firewall with 14 reference policies` — this is the anchor. All other posts reference it once it has a URL. |
| 9:05 AM | **HN monitoring starts** | Watch rank, comments | First comment on HN should be a concise "happy to answer questions" from the founder account. Respond to every comment within 30 min for the first 2 hours. |
| 9:15 AM | **Reddit r/LocalLLaMA** | Post in weekly thread if pinned; standalone otherwise | Check for "Self-Promo" or "Projects" weekly thread before posting. |
| 9:30 AM | **LinkedIn** | Native text post (no link in body) | Add repo link in first comment immediately after posting. Use 3 hashtags: `#mcp #agentic #opensource`. |
| 10:00 AM | **dev.to** | Publish article | Cross-link to HN thread if it has traction. Tags: `mcp`, `security`, `llm`, `aiagents`. |
| 10:30 AM | **Hashnode** | Publish article | Set canonical URL to `cloudmorph.ai/blog/tessera-launch`. Cross-link to HN thread. |
| 11:00 AM | **Twitter/X** | Post 5-tweet thread | Lead tweet: the Cursor+Jira hook. Pin the thread to the profile after posting. |
| 11:30 AM | **Discord — MCP Discord** | Post in `#announcements` or `#show-and-tell` | Include demo link (`recipes/cursor-hooks.md`) and one-paragraph summary. Do not repost the full blog post. |
| 11:45 AM | **Discord — Anthropic Discord** | Post in Claude-adjacent channel | Frame around the Claude Code recipe (`recipes/claude-code.md`). Mention the audit chain as the differentiator. |
| 12:00 PM | **Discord — Cursor Discord** | Post in `#show-your-work` or similar | Frame entirely around the 60-second Cursor demo. Link to `recipes/cursor-hooks.md` directly. |
| 12:30 PM | **r/programming** | Post or comment | If HN has comments, reference those as community validation. Reframe post as "technical write-up" not product announcement. |
| 1:00 PM | Fire window closes | Monitoring mode | Continue responding to HN, Reddit comments. No new channel submissions after this point on launch day. |

---

## HN-specific tactics

- Submit from a founder account with history (even modest karma). New accounts get shadow-penalized.
- The HN title must be factually accurate — "14 reference policies" must be true at submit time.
- Do not ask people to upvote. That violates HN guidelines and can result in down-ranking.
- Reply to every HN comment within 30 minutes during the first 2 hours. HN rewards active founders in the comment section.
- If the post gets flagged or killed, do not resubmit the same day. Wait 48 hours and reframe.

## Traction decision tree

| HN rank at 2 hours | Action |
|---|---|
| Top 30 | Reference HN thread in all subsequent posts. DM priority targets immediately (see `dm-targets.md`). |
| 31–75 | Proceed with schedule as planned. |
| Below 75 / no front page | Post schedule unchanged. Shift energy to Reddit and Discord engagement. Do not reference HN rank in other posts. |

## Pre-launch checklist (complete before 9:00 AM)

- [ ] GitHub repo is public and on main branch
- [ ] All 14 reference policies present in `policies/`
- [ ] `recipes/cursor-hooks.md` is complete and tested
- [ ] `docs/ROADMAP.md` is current (no stale items marked "coming soon" that shipped already)
- [ ] `SECURITY.md` has disclosure email
- [ ] PyPI package `cloudmorph-tessera` is live (or note it as "pip install from GitHub" in posts)
- [ ] Founder's HN account has been used in the past 30 days (avoids new-account rate limits)
- [ ] LinkedIn post draft is ready to paste at 9:30 AM
- [ ] Twitter/X thread draft is ready to paste at 11:00 AM
- [ ] Discord post drafts are ready in a local note
