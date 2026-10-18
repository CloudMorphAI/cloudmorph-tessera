"""Fresh-process Hermes bridge; private stdin/stdout pipes only, no login/model.

The reviewed parent owns admission and the one OAuth lifecycle. This worker gets
only a short-lived loopback proxy bearer, never the hosted OAuth grant. A denied
first task cannot resume; the review probe is a separate explicitly admitted task.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import re
import stat
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.dont_write_bytecode = True

TASKS = {
    "read-and-deny": [
        {"name": "aws___list_regions", "arguments": {}},
        {"name": "fixture__request_access_change", "arguments": {"request": "production_access_change"}},
    ],
    "review-stop": [
        {"name": "fixture__request_diagnostics_review", "arguments": {"request": "read_only_diagnostics_review"}},
    ],
}


class ChildError(Exception):
    pass


def _need(value):
    if not value:
        raise ChildError


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def _private_tmp(home):
    """Pin stdlib temp probes to this child; later socket fallbacks stay guarded."""
    temporary = home / "tmp"
    _need(home.is_absolute() and home.resolve() == home and home.is_relative_to(Path("/dev/shm")))  # noqa: S108 -- validate existing private tmpfs child home
    for path in (home, temporary):
        info = path.lstat()
        _need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
              and info.st_uid == os.getuid() and path.resolve() == path)
    _need(all(os.environ.get(key) == str(temporary) for key in ("TMPDIR", "TMP", "TEMP")))
    return temporary


def execute(request: dict, connector_module) -> dict:
    _need(type(request) is dict and set(request) == {"schema", "task", "source", "url", "bearer", "run_id"}
          and request["schema"] == "tessera.original_hermes_task.v1"
          and type(request["task"]) is str and request["task"] in TASKS
          and request["url"] == "http://127.0.0.1:8787/mcp/control"
          and type(request["run_id"]) is str and re.fullmatch(r"original1-[a-f0-9-]{36}", request["run_id"])
          and type(request["source"]) is str and Path(request["source"]).is_absolute())
    task = request["task"]
    home = Path(os.environ["HOME"])
    _private_tmp(home)
    with connector_module.guarded_loopback_runtime(source=Path(request["source"]), profile_parent=home) as guard:
        client = None
        try:
            client = connector_module.HermesClient(source=Path(request["source"]), url=request["url"],
                bearer=request["bearer"], run_id=request["run_id"], probes=TASKS[task], profile_parent=home)
            catalog = client.connect(runtime_admitted=True)
            _need(type(catalog) is list and {probe["name"] for probe in TASKS[task]} <= {tool["name"] for tool in catalog})
            rows = []
            for index, probe in enumerate(TASKS[task]):
                row = client.call(probe["name"], probe["arguments"], "hermes-" + task + "-" + str(index))
                rows.append(row)
                expected = "REVIEW_STOP" if task == "review-stop" else ("RESPONSE" if index == 0 else "DENIED")
                _need(row.get("status") == expected)
                if expected != "RESPONSE":
                    _need(client.stopped is True)
            summary = client.public_summary()
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                request["bearer"] = ""
        guard.assert_clean()
        return {"schema": "tessera.original_hermes_task_result.v1", "task": task,
                "receipts": rows, "summary": summary, "guard_clean": True}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(16385)
        _need(0 < len(raw) <= 16384)
        request = json.loads(raw, object_pairs_hook=_pairs)
        # Parent pins this exact connector before process creation. Its SHA is
        # provided out of band through a public code pin, never a bearer value.
        pin = os.environ.get("CM095_HERMES_CONNECTOR_SHA256")
        _need(type(pin) is str and re.fullmatch(r"[a-f0-9]{64}", pin))
        path = Path(__file__).with_name("hermes_client.py")
        source = path.read_bytes()
        _need(hashlib.sha256(source).hexdigest() == pin)
        spec = importlib.util.spec_from_file_location("_original_reviewed_hermes", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 -- hash-verified connector
            result = execute(request, module)
        encoded = json.dumps(result, allow_nan=False, separators=(",", ":")).encode("ascii")
        _need(len(encoded) <= 1_048_576)
        sys.stdout.buffer.write(encoded + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except BaseException:
        print('{"status":"BLOCKED","reason_code":"HERMES_SCRIPTED_TASK_FAILED"}', flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
