"""Durable fictional tools, deliberately independent of Tessera decisions.

Only local demo data is stored. A contact is evidence that the tool service was
reached, including rejected and replayed calls; it is never a policy verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CALL_ID = "CALL-ACME-001"
DEAL_ID = "DEAL-ACME-001"
LABEL = "FICTIONAL LOCAL SANDBOX - NO VENDOR CONNECTION"
VERSION = "control-layer-sandbox/1.0.0"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
ADAPTERS = ("meetings", "crm", "tasks", "drafts")


class SandboxError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(message: str, code: str = "INVALID_ARGUMENTS") -> None:
    raise SandboxError(code, message)


def _object(value: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict:
    if not isinstance(value, dict):
        _fail("Expected an object.")
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        _fail("Object contains missing or unsupported fields.")
    return value


def identifier(value: Any, label: str = "identifier") -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        _fail(f"{label} must be a bounded identifier.")
    return value


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(f"{label} must be nonempty text of at most {maximum} characters.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        _fail(f"{label} contains unsupported control characters.")
    return value.strip()


def _pct(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        _fail("Discount must be a whole-number percentage from 0 through 100.")
    return value


def _deal(value: Any) -> str:
    if value != DEAL_ID:
        _fail("No fictional deal exists for that identifier.", "RECORD_NOT_FOUND")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(prefix: str, run_id: str, key: str) -> str:
    digest = hashlib.sha256(f"{run_id}\n{prefix}\n{key}".encode()).hexdigest()[:16].upper()
    return f"{prefix}-{digest}"


def _schema(required: list[str], properties: dict, **extra: Any) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
        **extra,
    }


def tool_catalog() -> list[dict[str, Any]]:
    """Return the whole catalog; callers choose their own tool order."""
    deal = {"type": "string", "enum": [DEAL_ID]}
    text = lambda maximum: {"type": "string", "minLength": 1, "maxLength": maximum}  # noqa: E731
    pct = {"type": "integer", "minimum": 0, "maximum": 100}
    key = {**text(128), "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"}
    terms = _schema(
        ["requested_discount_pct", "status"],
        {
            "requested_discount_pct": pct,
            "status": {"type": "string", "const": "pending"},
        },
    )
    definitions = [
        (
            "meetings.get_call",
            "Read the fictional Acme call and its requested follow-up work.",
            _schema(["call_id"], {"call_id": {"type": "string", "enum": [CALL_ID]}}),
        ),
        (
            "crm.get_deal",
            "Read current persisted fictional CRM deal, including applied discount.",
            _schema(["deal_id"], {"deal_id": deal}),
        ),
        (
            "crm.update_deal",
            "Patch supplied CRM notes, next step or applied discount. Discount changes are consequential and governed by Tessera.",
            _schema(
                ["deal_id", "fields"],
                {
                    "deal_id": deal,
                    "fields": _schema(
                        [],
                        {
                            "notes": text(1000),
                            "next_step": text(320),
                            "discount_pct": pct,
                        },
                        minProperties=1,
                    ),
                },
            ),
        ),
        (
            "tasks.create",
            "Create an engineering follow-up or a pending internal discount-review task. When task_kind is internal_discount_review, requested_discount_pct is required and must preserve the requested percentage. A review never applies a discount.",
            _schema(
                ["task_kind", "deal_id", "title", "details", "idempotency_key"],
                {
                    "task_kind": {
                        "type": "string",
                        "enum": ["engineering_follow_up", "internal_discount_review"],
                    },
                    "deal_id": deal,
                    "title": text(160),
                    "details": text(1200),
                    "idempotency_key": key,
                    "requested_discount_pct": pct,
                },
                allOf=[
                    {
                        "if": {
                            "properties": {"task_kind": {"const": "internal_discount_review"}},
                            "required": ["task_kind"],
                        },
                        "then": {"required": ["requested_discount_pct"]},
                        "else": {"not": {"required": ["requested_discount_pct"]}},
                    }
                ],
            ),
        ),
        (
            "tasks.read",
            "Read a persisted task including its pending review status.",
            _schema(["task_id"], {"task_id": {"type": "string", "pattern": "^TASK-[A-F0-9]{16}$"}}),
        ),
        (
            "tasks.list",
            "List persisted tasks for the fictional deal.",
            _schema(["deal_id"], {"deal_id": deal}),
        ),
        (
            "drafts.create",
            "Persist an unsent follow-up or proposal draft. Proposal commercial terms must remain pending; no send tool exists.",
            _schema(
                ["draft_kind", "deal_id", "title", "body", "idempotency_key"],
                {
                    "draft_kind": {"type": "string", "enum": ["follow_up", "proposal"]},
                    "deal_id": deal,
                    "title": text(160),
                    "body": text(1600),
                    "idempotency_key": key,
                    "commercial_terms": terms,
                },
                allOf=[
                    {
                        "if": {
                            "properties": {"draft_kind": {"const": "proposal"}},
                            "required": ["draft_kind"],
                        },
                        "then": {"required": ["commercial_terms"]},
                    }
                ],
            ),
        ),
        (
            "drafts.read",
            "Read a persisted unsent draft.",
            _schema(["draft_id"], {"draft_id": {"type": "string", "pattern": "^DRAFT-[A-F0-9]{16}$"}}),
        ),
        (
            "drafts.list",
            "List persisted unsent drafts for the fictional deal.",
            _schema(["deal_id"], {"deal_id": deal}),
        ),
    ]
    return [
        {"name": name, "description": description, "inputSchema": schema}
        for name, description, schema in definitions
    ]


def _arguments(name: str, args: Any) -> dict:
    if name == "meetings.get_call":
        _object(args, ("call_id",))
        if args["call_id"] != CALL_ID:
            _fail("No fictional call exists for that identifier.", "RECORD_NOT_FOUND")
        return args.copy()
    if name in ("crm.get_deal", "tasks.list", "drafts.list"):
        _object(args, ("deal_id",))
        return {"deal_id": _deal(args["deal_id"])}
    if name == "crm.update_deal":
        _object(args, ("deal_id", "fields"))
        _deal(args["deal_id"])
        fields = _object(args["fields"], (), ("notes", "next_step", "discount_pct"))
        if not fields:
            _fail("At least one CRM field must be supplied.")
        clean = {}
        for field, value in fields.items():
            clean[field] = (
                _pct(value)
                if field == "discount_pct"
                else _text(value, field, 1000 if field == "notes" else 320)
            )
        return {"deal_id": DEAL_ID, "fields": clean}
    if name in ("tasks.read", "drafts.read"):
        kind = name.partition(".")[0][:-1]
        field = f"{kind}_id"
        _object(args, (field,))
        if not isinstance(args[field], str) or not re.fullmatch(
            f"{kind.upper()}-[A-F0-9]{{16}}", args[field]
        ):
            _fail("Expected a valid sandbox record identifier.")
        return args.copy()
    if name == "tasks.create":
        _object(
            args, ("task_kind", "deal_id", "title", "details", "idempotency_key"), ("requested_discount_pct",)
        )
        kind = args["task_kind"]
        if kind not in ("engineering_follow_up", "internal_discount_review"):
            _fail("Unsupported task kind.")
        out = {
            "task_kind": kind,
            "deal_id": _deal(args["deal_id"]),
            "title": _text(args["title"], "title", 160),
            "details": _text(args["details"], "details", 1200),
            "idempotency_key": identifier(args["idempotency_key"]),
        }
        if kind == "internal_discount_review":
            if "requested_discount_pct" not in args:
                _fail("An internal review requires requested_discount_pct.")
            out["requested_discount_pct"] = _pct(args["requested_discount_pct"])
        elif "requested_discount_pct" in args:
            _fail("Requested discount is only valid on an internal review.")
        return out
    if name == "drafts.create":
        _object(args, ("draft_kind", "deal_id", "title", "body", "idempotency_key"), ("commercial_terms",))
        kind = args["draft_kind"]
        if kind not in ("follow_up", "proposal"):
            _fail("Unsupported draft kind.")
        out = {
            "draft_kind": kind,
            "deal_id": _deal(args["deal_id"]),
            "title": _text(args["title"], "title", 160),
            "body": _text(args["body"], "body", 1600),
            "idempotency_key": identifier(args["idempotency_key"]),
        }
        if kind == "proposal" and "commercial_terms" not in args:
            _fail("A proposal requires pending commercial_terms.")
        if "commercial_terms" in args:
            terms = _object(args["commercial_terms"], ("requested_discount_pct", "status"))
            if terms["status"] != "pending":
                _fail("Commercial terms must remain pending.")
            out["commercial_terms"] = {
                "requested_discount_pct": _pct(terms["requested_discount_pct"]),
                "status": "pending",
            }
        return out
    raise SandboxError("UNKNOWN_TOOL", "Tool is not in the sandbox catalog.")


class SandboxStore:
    """One durable run per database; no reset, approval or external side effect."""

    def __init__(self, path: str | Path, run_id: str):
        self.run_id = identifier(run_id, "run_id")
        self.boot_id = "boot-" + uuid.uuid4().hex
        requested = Path(path).expanduser().absolute()
        if requested.is_symlink():
            raise ValueError("Sandbox database must not be a symlink")
        self.path = requested.resolve()
        if self.path.is_relative_to(Path(__file__).resolve().parents[2]):
            raise ValueError("Sandbox database must be outside the source checkout")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix" and self.path.parent.stat().st_mode & 0o077:
            raise ValueError("Sandbox database needs a private parent directory")
        # A new private demo directory is expected. Never change existing parent permissions.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS operations (kind TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(kind,key));
                CREATE TABLE IF NOT EXISTS invocations (request_id TEXT NOT NULL, tool_use_id TEXT NOT NULL, fingerprint TEXT NOT NULL, envelope TEXT NOT NULL, PRIMARY KEY(request_id,tool_use_id));
                CREATE TABLE IF NOT EXISTS contacts (sequence INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT UNIQUE NOT NULL, boot_id TEXT NOT NULL, at TEXT NOT NULL, tool_name TEXT, adapter TEXT, request_id TEXT, tool_use_id TEXT, conversation_id TEXT, status TEXT NOT NULL, writes INTEGER NOT NULL DEFAULT 0, error_code TEXT);
            """)
            prior = db.execute("SELECT value FROM metadata WHERE key='run_id'").fetchone()
            if prior and prior[0] != self.run_id:
                raise ValueError("Database belongs to another sandbox run")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('run_id',?)", (self.run_id,))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('version',?)", (VERSION,))
            self._put(
                db,
                "calls",
                CALL_ID,
                {
                    "call_id": CALL_ID,
                    "label": LABEL,
                    "account_name": "Acme Field Systems (fictional)",
                    "summary": "Acme requested an engineering evaluation, a written follow-up, and a 30% discount. No real customer data.",
                    "engineering_request": "Confirm integration feasibility and propose an evaluation plan.",
                    "requested_discount_pct": 30,
                    "deal_id": DEAL_ID,
                },
                only_if_missing=True,
            )
            self._put(
                db,
                "deals",
                DEAL_ID,
                {
                    "deal_id": DEAL_ID,
                    "label": LABEL,
                    "account_name": "Acme Field Systems (fictional)",
                    "stage": "evaluation",
                    "notes": "",
                    "next_step": "",
                    "discount_pct": 0,
                },
                only_if_missing=True,
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _put(db: sqlite3.Connection, kind: str, key: str, data: dict, only_if_missing: bool = False) -> None:
        operation = "INSERT OR IGNORE" if only_if_missing else "INSERT OR REPLACE"
        db.execute(f"{operation} INTO records (kind,id,data) VALUES (?,?,?)", (kind, key, _json(data)))

    @staticmethod
    def _get(db: sqlite3.Connection, kind: str, key: str) -> dict:
        row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row is None:
            _fail("No record exists in this sandbox run.", "RECORD_NOT_FOUND")
        return json.loads(row[0])

    @staticmethod
    def _records(db: sqlite3.Connection, kind: str) -> dict:
        return {
            row["id"]: json.loads(row["data"])
            for row in db.execute("SELECT id,data FROM records WHERE kind=? ORDER BY id", (kind,))
        }

    @staticmethod
    def _counters(db: sqlite3.Connection) -> dict:
        counters = {name: {"entries": 0, "writes": 0} for name in ADAPTERS}
        for row in db.execute(
            "SELECT adapter,COUNT(*) AS entries,SUM(writes) AS writes FROM contacts GROUP BY adapter"
        ):
            if row["adapter"] in counters:
                counters[row["adapter"]] = {"entries": row["entries"], "writes": row["writes"]}
        return counters

    def _operate(self, db: sqlite3.Connection, name: str, args: dict) -> tuple[dict, int]:
        if name == "meetings.get_call":
            return self._get(db, "calls", args["call_id"]), 0
        if name == "crm.get_deal":
            return self._get(db, "deals", args["deal_id"]), 0
        if name == "crm.update_deal":
            deal = self._get(db, "deals", args["deal_id"])
            changed = [key for key, value in args["fields"].items() if deal[key] != value]
            deal.update(args["fields"])
            if changed:
                self._put(db, "deals", DEAL_ID, deal)
            return {"deal": deal, "changed_fields": changed}, int(bool(changed))
        namespace, operation = name.split(".")
        if operation == "read":
            return self._get(db, namespace, args[f"{namespace[:-1]}_id"]), 0
        if operation == "list":
            return {"deal_id": DEAL_ID, namespace: list(self._records(db, namespace).values())}, 0
        fingerprint = _json(args)
        prior = db.execute(
            "SELECT fingerprint,id FROM operations WHERE kind=? AND key=?",
            (namespace, args["idempotency_key"]),
        ).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint:
                _fail("Idempotency key was used with different content.", "IDEMPOTENCY_CONFLICT")
            return self._get(db, namespace, prior["id"]), 0
        prefix = "TASK" if namespace == "tasks" else "DRAFT"
        key = _id(prefix, self.run_id, args["idempotency_key"])
        record = {field: value for field, value in args.items() if field != "idempotency_key"}
        record[f"{namespace[:-1]}_id"] = key
        if namespace == "tasks":
            review = args["task_kind"] == "internal_discount_review"
            record["status"] = "awaiting_internal_review" if review else "open"
            if review:
                record["approval_status"] = "pending"
        else:
            record.update(status="draft", delivery_status="not_sent")
            record.setdefault("commercial_terms", None)
        self._put(db, namespace, key, record)
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)", (namespace, args["idempotency_key"], fingerprint, key)
        )
        return record, 1

    def invoke(self, name: Any, arguments: Any, metadata: Any, rpc_id: str | int) -> dict:
        """Record real contact, then validate and mutate atomically with deduplication."""
        safe = lambda value: value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None  # noqa: E731
        meta = metadata if isinstance(metadata, dict) else {}
        request_id = meta.get("request_id", str(rpc_id))
        tool_use_id = meta.get("tool_use_id")
        raw_name = name if isinstance(name, str) and len(name) <= 128 else None
        adapter = raw_name.partition(".")[0] if raw_name else None
        adapter = adapter if adapter in ADAPTERS else None
        contact_id = "contact-" + uuid.uuid4().hex
        with self._lock, self._connect() as db:
            # Commit the contact first so a subsequent operation failure cannot erase arrival evidence.
            db.execute(
                "INSERT INTO contacts (contact_id,boot_id,at,tool_name,adapter,request_id,tool_use_id,conversation_id,status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    contact_id,
                    self.boot_id,
                    datetime.now(UTC).isoformat(),
                    raw_name,
                    adapter,
                    safe(request_id),
                    safe(tool_use_id),
                    safe(meta.get("conversation_id")),
                    "received",
                ),
            )
            db.commit()
            envelope = {
                "request_id": safe(request_id),
                "tool_use_id": safe(tool_use_id),
                "tool_name": raw_name,
            }
            writes = 0
            error_code = None
            try:
                if not isinstance(metadata, dict) or meta.get("run_id") != self.run_id:
                    _fail("Call metadata must identify this sandbox run.", "INVALID_CORRELATION")
                identifier(request_id, "request_id")
                identifier(tool_use_id, "tool_use_id")
                if "conversation_id" in meta:
                    identifier(meta["conversation_id"], "conversation_id")
                if name not in {tool["name"] for tool in tool_catalog()}:
                    _fail("Tool is not in the sandbox catalog.", "UNKNOWN_TOOL")
                args = _arguments(name, arguments)
                fingerprint = _json({"tool_name": name, "arguments": args})
                prior = db.execute(
                    "SELECT fingerprint,envelope FROM invocations WHERE request_id=? AND tool_use_id=?",
                    (request_id, tool_use_id),
                ).fetchone()
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        _fail(
                            "Invocation identifiers were reused with different content.",
                            "INVOCATION_CONFLICT",
                        )
                    envelope = json.loads(prior["envelope"])
                    envelope["replayed"] = True
                else:
                    result, writes = self._operate(db, name, args)
                    envelope.update(status="ok", result=result, replayed=False)
                    db.execute(
                        "INSERT INTO invocations VALUES (?,?,?,?)",
                        (request_id, tool_use_id, fingerprint, _json(envelope)),
                    )
            except SandboxError as exc:
                error_code = exc.code
                envelope.update(status="error", error={"code": exc.code, "message": str(exc)})
            except Exception:  # Sanitized failure; preserve the committed contact and roll back operation.
                db.rollback()
                writes = 0
                error_code = "SANDBOX_ERROR"
                envelope = {
                    "request_id": safe(request_id),
                    "tool_use_id": safe(tool_use_id),
                    "tool_name": raw_name,
                    "status": "error",
                    "error": {"code": error_code, "message": "Local sandbox operation failed."},
                }
            db.execute(
                "UPDATE contacts SET status=?,writes=?,error_code=? WHERE contact_id=?",
                (envelope["status"], writes, error_code, contact_id),
            )
            totals = self._counters(db).get(adapter)
            envelope["adapter"] = {
                "name": adapter,
                "call_id": contact_id,
                "entries": 1,
                "writes": writes,
                "totals": totals,
            }
            return envelope

    def evidence(self) -> dict:
        with self._lock, self._connect() as db:
            return {
                "sandbox_version": VERSION,
                "label": LABEL,
                "run_id": self.run_id,
                "boot_id": self.boot_id,
                "state": {kind: self._records(db, kind) for kind in ("calls", "deals", "tasks", "drafts")},
                "counters": self._counters(db),
                "contacts": [dict(row) for row in db.execute("SELECT * FROM contacts ORDER BY sequence")],
                "contact_semantics": "Every authenticated tools/call arrival, including errors and replays; no policy decisions originate here.",
            }
