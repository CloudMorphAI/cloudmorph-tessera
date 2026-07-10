"""Evidence-bundle export for the audit hash chain.

`export_evidence_bundle` produces a self-contained, offline-verifiable
directory with four files:

  - events.jsonl   — the decision events in range, one JSON object per line
  - manifest.json  — range, event counts, chain head/tail hashes, the
                      Ed25519 PUBLIC key used to sign the bundle, and a
                      tool/policy inventory
  - summary.html   — human-readable report (inline CSS, no external assets)
  - VERIFY.md       — exact offline steps to verify the bundle

The bundle is signed with the local instance key from
``tessera.audit.signing`` — see that module's docstring for key resolution
order. Verification (``tessera.audit.verifier.verify_bundle_dir``)
recomputes the chain and the signing payload independently from
events.jsonl rather than trusting manifest.json's stated values, so a
tampered manifest is caught by the signature check even if the tamperer
"fixes up" the manifest's counts/hashes to match their edits.
"""

from __future__ import annotations

import html as html_lib
import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tessera.audit.canonical_json import canonical_json
from tessera.audit.signing import load_or_create_signing_key, public_key_pem, sign
from tessera.audit.sinks.base import AuditSink

__all__ = [
    "BUNDLE_VERSION",
    "parse_ts",
    "format_ts",
    "gather_events",
    "summarize_events",
    "signing_payload",
    "load_compliance_mappings",
    "render_summary_html",
    "render_verify_md",
    "export_evidence_bundle",
]

BUNDLE_VERSION = "1.0"
MAX_BLOCK_ROWS = 200

DEFAULT_MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "compliance" / "mappings"


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (with or without trailing 'Z') as UTC.

    Accepts date-only ("2026-07-01"), second precision, or microsecond
    precision (the format the audit emitter writes: "%Y-%m-%dT%H:%M:%S.%fZ").
    Naive datetimes (no tzinfo) are assumed UTC.
    """
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts(dt: datetime) -> str:
    """Format a datetime the same way the audit emitter formats occurredAt."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now_iso() -> str:
    return format_ts(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Gathering + summarizing events
# ---------------------------------------------------------------------------


def gather_events(
    sink: AuditSink,
    *,
    scope: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict[str, Any]]:
    """Collect events for `scope` whose occurredAt falls within [from_dt, to_dt] (inclusive)."""
    result: list[dict[str, Any]] = []
    for event in sink.iter_events(scope=scope):
        occurred = event.get("occurredAt")
        if not occurred:
            continue
        try:
            dt = parse_ts(occurred)
        except ValueError:
            continue
        if from_dt <= dt <= to_dt:
            result.append(event)
    return result


def summarize_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute counts, tool/policy inventory, and chain endpoints for a sequence of events.

    Used both when building a bundle (export) and when re-verifying one
    (verifier recomputes this independently from events.jsonl rather than
    trusting manifest.json).
    """
    total = 0
    counts = {"allow": 0, "block": 0, "approval": 0, "log_only": 0, "other": 0}
    tools: dict[str, int] = {}
    policies: dict[str, int] = {}
    first_event: dict[str, Any] | None = None
    last_event: dict[str, Any] | None = None

    for event in events:
        total += 1
        if first_event is None:
            first_event = event
        last_event = event

        payload = event.get("payload") or {}
        if event.get("eventType") == "decision":
            decision = payload.get("decision")
            if decision == "allow":
                counts["allow"] += 1
            elif decision == "block":
                counts["block"] += 1
            elif decision == "require_approval":
                counts["approval"] += 1
            elif decision == "log_only":
                counts["log_only"] += 1
            else:
                counts["other"] += 1
        else:
            counts["other"] += 1

        tool = (
            payload.get("canonical_tool_name")
            or payload.get("effective_tool_name")
            or (payload.get("tool_call") or {}).get("name")
        )
        if tool:
            tools[tool] = tools.get(tool, 0) + 1

        policy_id = payload.get("policy_id")
        if policy_id:
            policies[policy_id] = policies.get(policy_id, 0) + 1

    return {
        "total": total,
        "allow": counts["allow"],
        "block": counts["block"],
        "approval": counts["approval"],
        "log_only": counts["log_only"],
        "other": counts["other"],
        "tools": tools,
        "policies_fired": policies,
        "first_event_id": first_event.get("eventId", "") if first_event else "",
        "first_event_hash": first_event.get("eventHash", "") if first_event else "",
        "first_event_prev_hash": first_event.get("prevEventHash", "") if first_event else "",
        "first_event_at": first_event.get("occurredAt") if first_event else None,
        "last_event_id": last_event.get("eventId", "") if last_event else "",
        "last_event_hash": last_event.get("eventHash", "") if last_event else "",
        "last_event_at": last_event.get("occurredAt") if last_event else None,
    }


def signing_payload(
    *,
    scope: str,
    from_ts: str,
    to_ts: str,
    event_counts: dict[str, int],
    chain: dict[str, str],
) -> bytes:
    """Canonical bytes signed over the bundle (and recomputed for verification).

    Deliberately excludes the "signing" block itself (chicken/egg) and the
    "inventory"/"generated_at" fields (informational, not tamper-evident —
    the tool/policy inventory is fully derivable from events.jsonl, which is
    already covered by the hash chain; it doesn't need double coverage here).
    """
    payload = {
        "bundle_version": BUNDLE_VERSION,
        "scope": scope,
        "range": {"from": from_ts, "to": to_ts},
        "event_counts": dict(event_counts),
        "chain": {
            "first_event_id": chain.get("first_event_id", ""),
            "first_event_hash": chain.get("first_event_hash", ""),
            "first_event_prev_hash": chain.get("first_event_prev_hash", ""),
            "last_event_id": chain.get("last_event_id", ""),
            "last_event_hash": chain.get("last_event_hash", ""),
        },
    }
    return canonical_json(payload)


# ---------------------------------------------------------------------------
# Compliance mapping loader (interface contract with tessera/compliance/*)
# ---------------------------------------------------------------------------


def load_compliance_mappings(
    mappings_dir: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """Load compliance framework mapping YAML files.

    Tolerates the directory (or individual files) being absent or malformed
    — compliance mappings are authored in a separate workstream
    (`tessera/compliance/mappings/*.yaml`) and may not exist yet. Returns an
    empty list in that case; callers should simply omit the compliance
    section rather than error.

    Expected schema per file (see tessera/compliance/mappings/*.yaml):
        framework: "EU AI Act"
        version: "..."
        obligations:
          - id: "record-keeping"
            obligation: "..."
            source: "..."
            review: FOUNDER   # optional
            tessera_controls: ["...", "..."]
            evidenced_by: "..."
    """
    d = Path(mappings_dir) if mappings_dir else DEFAULT_MAPPINGS_DIR
    if not d.exists() or not d.is_dir():
        return []

    mappings: list[dict[str, Any]] = []
    for f in sorted({*d.glob("*.yaml"), *d.glob("*.yml")}):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a malformed sibling file must not break export
            continue
        if not isinstance(data, dict) or not data.get("framework"):
            continue
        if not isinstance(data.get("obligations"), list):
            data["obligations"] = []
        mappings.append(data)
    return mappings


# ---------------------------------------------------------------------------
# summary.html
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value is not None else ""), quote=True)


_HTML_HEAD = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Tessera audit evidence bundle</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5;
         color: #1a1a1a; background: #fff; }
  @media (prefers-color-scheme: dark) {
    body { color: #e6e6e6; background: #121212; }
    table { border-color: #333 !important; }
    th, td { border-color: #333 !important; }
    th { background: #1e1e1e !important; }
    tr:nth-child(even) td { background: #1a1a1a !important; }
    code, .mono { background: #1e1e1e !important; }
    .badge-block { background: #4a1414 !important; color: #ffb4b4 !important; }
    .badge-allow { background: #10321a !important; color: #9fe6b0 !important; }
    .badge-approval { background: #3a3312 !important; color: #f0d585 !important; }
    .badge-review { background: #3a2410 !important; color: #ffbf6b !important; }
    .chip { background: #22314a !important; color: #a9c4ff !important; }
    .muted { color: #999 !important; }
    a { color: #7fb2ff !important; }
  }
  h1 { font-size: 1.5rem; margin-bottom: 0.2rem; }
  h2 { font-size: 1.15rem; margin-top: 2.2rem; border-bottom: 2px solid #ddd; padding-bottom: 0.3rem; }
  .muted { color: #666; font-size: 0.9rem; }
  table { border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.5rem 0; font-size: 0.9rem; }
  th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }
  th { background: #f4f4f4; }
  tr:nth-child(even) td { background: #fafafa; }
  .mono, code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.85em;
                background: #f4f4f4; padding: 0.1rem 0.3rem; border-radius: 3px; word-break: break-all; }
  .stat-row { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
  .stat { border: 1px solid #ddd; border-radius: 8px; padding: 0.6rem 1rem; min-width: 100px; }
  .stat .n { font-size: 1.4rem; font-weight: 700; display: block; }
  .stat .l { font-size: 0.78rem; color: #666; text-transform: uppercase; letter-spacing: 0.03em; }
  .badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 10px; font-size: 0.78rem; font-weight: 600; }
  .badge-block { background: #fde2e2; color: #a11; }
  .badge-allow { background: #e2f7e6; color: #167a2e; }
  .badge-approval { background: #fff6d9; color: #92720a; }
  .badge-review { background: #fff0da; color: #a35c00; }
  .chip { display: inline-block; background: #eef2ff; color: #3949ab; border-radius: 10px;
          padding: 0.05rem 0.5rem; font-size: 0.78rem; margin: 0.1rem 0.2rem 0.1rem 0; }
  footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #ddd; font-size: 0.8rem; color: #888; }
</style>
</head>
<body>
"""

_HTML_TAIL = """
</body>
</html>
"""


def render_summary_html(
    events: list[dict[str, Any]],
    manifest: dict[str, Any],
    mappings: list[dict[str, Any]] | None = None,
) -> str:
    """Render the self-contained summary.html for an evidence bundle."""
    mappings = mappings or []
    parts: list[str] = [_HTML_HEAD]

    scope = manifest.get("scope", "")
    rng = manifest.get("range", {})
    counts = manifest.get("event_counts", {})
    chain = manifest.get("chain", {})
    inventory = manifest.get("inventory", {})
    signing = manifest.get("signing", {})

    parts.append("<h1>Tessera Audit Evidence Bundle</h1>")
    parts.append(
        f'<p class="muted">scope <code>{_esc(scope)}</code> &middot; '
        f"range <code>{_esc(rng.get('from'))}</code> &rarr; "
        f"<code>{_esc(rng.get('to'))}</code> &middot; "
        f"generated <code>{_esc(manifest.get('generated_at'))}</code></p>"
    )

    parts.append("<h2>What was enforced</h2>")
    parts.append('<div class="stat-row">')
    for label, key in (
        ("Total events", "total"),
        ("Allowed", "allow"),
        ("Blocked", "block"),
        ("Required approval", "approval"),
        ("Log only", "log_only"),
        ("Other", "other"),
    ):
        parts.append(
            f'<div class="stat"><span class="n">{_esc(counts.get(key, 0))}</span>'
            f'<span class="l">{_esc(label)}</span></div>'
        )
    parts.append("</div>")

    parts.append("<h2>Chain integrity</h2>")
    parts.append("<table>")
    parts.append(
        "<tr><th>First event</th><td>"
        f"<code>{_esc(chain.get('first_event_id'))}</code><br>"
        f"hash <span class='mono'>{_esc(chain.get('first_event_hash'))}</span><br>"
        f"prev <span class='mono'>{_esc(chain.get('first_event_prev_hash')) or '(none — start of chain)'}</span>"
        "</td></tr>"
    )
    parts.append(
        "<tr><th>Last event</th><td>"
        f"<code>{_esc(chain.get('last_event_id'))}</code><br>"
        f"hash <span class='mono'>{_esc(chain.get('last_event_hash'))}</span>"
        "</td></tr>"
    )
    parts.append(
        "<tr><th>Signature</th><td>"
        f"algorithm <code>{_esc(signing.get('algorithm'))}</code><br>"
        f"public key fingerprint <span class='mono'>{_esc(_fingerprint(signing.get('public_key_pem', '')))}</span><br>"
        "see manifest.json for the full public key PEM and signature"
        "</td></tr>"
    )
    parts.append("</table>")

    # Blocked calls detail (capped)
    block_events = [e for e in events if (e.get("payload") or {}).get("decision") == "block"]
    if block_events:
        shown = block_events[:MAX_BLOCK_ROWS]
        parts.append("<h2>What was blocked, and by which policy</h2>")
        parts.append("<table>")
        parts.append("<tr><th>Time</th><th>Tool</th><th>Policy</th><th>Reason</th></tr>")
        for e in shown:
            payload = e.get("payload") or {}
            tool = (
                payload.get("canonical_tool_name")
                or payload.get("effective_tool_name")
                or (payload.get("tool_call") or {}).get("name")
                or ""
            )
            parts.append(
                "<tr>"
                f"<td>{_esc(e.get('occurredAt'))}</td>"
                f"<td><code>{_esc(tool)}</code></td>"
                f"<td><code>{_esc(payload.get('policy_id'))}</code></td>"
                f"<td>{_esc(payload.get('reason'))}</td>"
                "</tr>"
            )
        parts.append("</table>")
        if len(block_events) > MAX_BLOCK_ROWS:
            parts.append(
                f'<p class="muted">Showing first {MAX_BLOCK_ROWS} of {len(block_events)} '
                "blocked events; the full set is in events.jsonl.</p>"
            )

    # Tool inventory
    tools = inventory.get("tools", {})
    if tools:
        parts.append("<h2>Tool inventory</h2>")
        parts.append("<table><tr><th>Tool</th><th>Calls</th></tr>")
        for name, n in sorted(tools.items(), key=lambda kv: (-kv[1], kv[0])):
            parts.append(f"<tr><td><code>{_esc(name)}</code></td><td>{_esc(n)}</td></tr>")
        parts.append("</table>")

    # Policy inventory
    policies = inventory.get("policies_fired", {})
    if policies:
        parts.append("<h2>Policies that fired</h2>")
        parts.append("<table><tr><th>Policy ID</th><th>Times fired</th></tr>")
        for name, n in sorted(policies.items(), key=lambda kv: (-kv[1], kv[0])):
            parts.append(f"<tr><td><code>{_esc(name)}</code></td><td>{_esc(n)}</td></tr>")
        parts.append("</table>")

    # Compliance mapping (interface contract with tessera/compliance/mappings/*.yaml)
    if mappings:
        parts.append("<h2>Compliance mapping</h2>")
        parts.append(
            '<p class="muted">Cross-reference against "Policies that fired" above — '
            "obligations below list the Tessera controls that generally evidence them; "
            "this bundle's specific enforcement activity is the audit trail itself.</p>"
        )
        for fw in mappings:
            parts.append(
                f"<h3>{_esc(fw.get('framework'))} "
                f'<span class="muted">({_esc(fw.get("version"))})</span></h3>'
            )
            parts.append("<table>")
            parts.append("<tr><th>Obligation</th><th>Tessera controls</th><th>Evidenced by</th></tr>")
            for ob in fw.get("obligations", []):
                if not isinstance(ob, dict):
                    continue
                review_badge = (
                    ' <span class="badge badge-review">NEEDS FOUNDER REVIEW</span>'
                    if ob.get("review") == "FOUNDER"
                    else ""
                )
                controls = ob.get("tessera_controls") or []
                chips = "".join(f'<span class="chip">{_esc(c)}</span>' for c in controls)
                parts.append(
                    "<tr><td>"
                    f"<strong>{_esc(ob.get('id'))}</strong>{review_badge}<br>"
                    f"{_esc(ob.get('obligation'))}<br>"
                    f'<span class="muted">source: {_esc(ob.get("source"))}</span>'
                    f"</td><td>{chips}</td>"
                    f"<td>{_esc(ob.get('evidenced_by'))}</td></tr>"
                )
            parts.append("</table>")

    parts.append(
        "<footer>Generated by <code>tessera audit export</code>. "
        "Verify this bundle offline with <code>tessera audit verify --dir .</code> "
        "— see VERIFY.md.</footer>"
    )
    parts.append(_HTML_TAIL)
    return "".join(parts)


def _fingerprint(pem: str) -> str:
    """Short SHA-256 fingerprint of a PEM public key, for display only (not used for verification)."""
    import hashlib

    digest = hashlib.sha256(pem.encode("utf-8")).hexdigest()
    return ":".join(digest[i : i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------------------
# VERIFY.md
# ---------------------------------------------------------------------------


def render_verify_md(*, scope: str) -> str:
    return f"""# Verifying this evidence bundle

This directory is a self-contained, **offline-verifiable** Tessera audit
evidence bundle for scope `{scope}`.

## Files

- `events.jsonl`  — one hash-chained audit event per line, in order
- `manifest.json` — range, event counts, chain head/tail hashes, the
  Ed25519 **public** key used to sign the bundle, and a tool/policy inventory
- `summary.html`  — human-readable report
- `VERIFY.md`     — this file

## Steps

1. Install `tessera` (or use the same version that produced this bundle):
   `pip install tessera`

2. From a shell, run (no network access required):

   ```
   tessera audit verify --dir <path-to-this-directory>
   ```

3. Expect exit code `0` and a report showing `ok=true` with the chain
   event count matching manifest.json's `event_counts.total`, plus
   `signature_ok=true`.

   Any **non-zero exit code** means the bundle was tampered with. The
   output reports the first broken event by sequence index / event ID
   (`hash_mismatch` or `chain_break`), or `signature_invalid` if the
   Ed25519 signature over the bundle's counts/hashes doesn't match —
   this catches both edits to `events.jsonl` and edits to `manifest.json`
   itself, since the signature is independently recomputed from
   `events.jsonl` during verification rather than trusted from the manifest.

4. This is fully **offline** — the Ed25519 public key needed to verify the
   signature is embedded in `manifest.json`. The private signing key never
   leaves the machine that produced this export and is **not** included in
   this bundle.

5. Optional deeper check: if you also have access to the full, live audit
   database this bundle was exported from, confirm this bundle is a genuine
   slice of that history (not a fabricated chain rooted elsewhere) by
   checking that `manifest.json` &rarr; `chain.first_event_prev_hash`
   appears as an `eventHash` in the live chain:

   ```
   tessera audit verify --audit-path <audit.db> --scope {scope}
   ```
"""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def export_evidence_bundle(
    sink: AuditSink,
    *,
    scope: str,
    from_ts: str | None,
    to_ts: str | None,
    out_dir: str | os.PathLike[str],
    signing_key_path: str | os.PathLike[str] | None = None,
    mappings_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build a full evidence bundle in `out_dir`. Returns the manifest dict.

    `from_ts`/`to_ts` are ISO-8601 strings (any format accepted by
    `parse_ts`); omitted `from_ts` defaults to the epoch, omitted `to_ts`
    defaults to now — i.e. "export everything up to now" when both are
    omitted.
    """
    from_dt = parse_ts(from_ts) if from_ts else datetime(1970, 1, 1, tzinfo=timezone.utc)
    to_dt = parse_ts(to_ts) if to_ts else datetime.now(timezone.utc)
    if from_dt > to_dt:
        raise ValueError(f"export_evidence_bundle: --from ({from_dt}) is after --to ({to_dt})")

    from_str = format_ts(from_dt)
    to_str = format_ts(to_dt)

    events = gather_events(sink, scope=scope, from_dt=from_dt, to_dt=to_dt)
    summary = summarize_events(events)

    event_counts = {
        "total": summary["total"],
        "allow": summary["allow"],
        "block": summary["block"],
        "approval": summary["approval"],
        "log_only": summary["log_only"],
        "other": summary["other"],
    }
    chain = {
        "first_event_id": summary["first_event_id"],
        "first_event_hash": summary["first_event_hash"],
        "first_event_prev_hash": summary["first_event_prev_hash"],
        "last_event_id": summary["last_event_id"],
        "last_event_hash": summary["last_event_hash"],
        "head_hash": summary["last_event_hash"],
        "tail_hash": summary["first_event_prev_hash"],
    }

    payload_bytes = signing_payload(
        scope=scope, from_ts=from_str, to_ts=to_str, event_counts=event_counts, chain=chain
    )
    private_key = load_or_create_signing_key(signing_key_path)
    signature = sign(private_key, payload_bytes)
    pub_pem = public_key_pem(private_key)

    manifest: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": _now_iso(),
        "scope": scope,
        "range": {"from": from_str, "to": to_str},
        "event_counts": event_counts,
        "chain": chain,
        "inventory": {
            "tools": summary["tools"],
            "policies_fired": summary["policies_fired"],
        },
        "signing": {
            "algorithm": "Ed25519",
            "public_key_pem": pub_pem,
            "signature": signature,
        },
    }

    mappings = load_compliance_mappings(mappings_dir)
    if mappings:
        manifest["compliance_frameworks"] = [
            {"framework": m.get("framework"), "version": m.get("version")} for m in mappings
        ]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    events_path = out / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False))
            fh.write("\n")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "summary.html").write_text(render_summary_html(events, manifest, mappings), encoding="utf-8")
    (out / "VERIFY.md").write_text(render_verify_md(scope=scope), encoding="utf-8")

    return manifest
