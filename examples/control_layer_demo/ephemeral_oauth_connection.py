"""One explicitly approved, RAM-only OAuth connection and exact-row cleanup.

Preparation is inert. Execution requires explicit run-input/approval hashes, the approved CLI flag,
reviewed source pins and a new run-bound founder approval. Old attempt1 is never
selected, rewritten or replayed. No ordinary OAuth cache, refresh request,
AWS credential value, raw error or private identity is printed or persisted.
The EXCHANGE_STARTED marker is deliberately durable and never removed/reused.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import re
import signal
import stat
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Suppress raw library diagnostics before any SDK/helper import or sensitive I/O.
logging.disable(logging.CRITICAL)

import boto3  # noqa: E402 - logging guard must precede third-party imports
import httpx  # noqa: E402
import jwt  # noqa: E402
from botocore.config import Config  # noqa: E402

_ROOT = Path("/mnt/c/Users/found/Desktop/CloudMorph")
_AUDIT = _ROOT / "workspace-support/audit/2026-10-18-cm095-resume"
_SOURCE = Path("/tmp/arjun-cm095-local-inventory-20261018-lxm6vkqv/source")  # noqa: S108 - fixed hash-pinned source only
_PLAN_SHA = "798b67f5e02dcab1cbbab6af4b89fb673a485b51e1b4e580c2cdfc41728f63e9"
_SEED = Path("/mnt/c/Users/found/AppData/Local/Temp/arjun-cm095-draft-tenant-seed-20261016.json")
_SEED_SHA = "52f4fa6d72bd9a1ebeefeae8217f2df78b553321e2f1c60b1dda2b77d1526f48"
_RECEIVER_SHA = "48f0d1d011411a2aeec9ee8cd460da094bb60df0b208f938eac5df28e0ac7c78"
_USE_SHA = "1b4da623d9d1fe8ea229d702d3964476df53a42ff42baa7043cf375587d7c9f0"
_ACCOUNT = "237509402889"
_REGION = "us-east-1"
_POOL = "us-east-1_BoyfHFCMI"
_TABLE = "tessera-oauth-refresh-tokens-prod"
_ARN = f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{_TABLE}"
_IDENTITY_TABLE = "tessera-identity-bindings-prod"
_SCOPE = "tessera:policies:read tessera:audit:write"
_TOKEN_URL = "https://auth.tessera.cloudmorph.ai/oauth/token"  # noqa: S105 - public endpoint, no credential
_JWKS_URL = "https://auth.tessera.cloudmorph.ai/oauth/jwks.json"
_SOURCE_PINS = {
    "amplify/backend/tessera/oauth/store.py": "903a456d5165e7fb784c8517a1a02c6cb3c59b5f6d3f0c39e4e26ff9e0a92ed8",
    "amplify/backend/tessera/oauth/handler.py": "eb5d7b6bf3273feb795ebb9df1936ecf5a9409ca36908dec3a977e5db50fae2a",
    "amplify/backend/tessera/oauth/authorizer.py": "13188351a14ff4ec682a2c11f01d99ad89fc71e558b5dafe86be678cbe58f7db",
    "amplify/backend/tessera/oauth/jwt_signer.py": "91a0b680271edf009a7c7557fa8c6f85b0bc5ccd749eada7f322abe20b7ac55d",
    "amplify/backend/tessera/identity/python/tessera_identity.py": "6a302337d682680713c4654d7017ef03c34c67c18124a9323acc95705f5b43fa",
}
_CORE_PINS = {'schemas/audit_event.schema.json': 'b620e3145bcfbc442d0da79e0d5ab4b92a9fb678b37f900691a52d3367ed1672', 'tessera/audit/canonical_json.py': '49c97ce89ce47057bf21f209868e047a74ee977efbc4785599dc45f2cf67ee14', 'tessera/audit/chain.py': '439f542596a7d17f15c0b644c2d005fb547297575fbf50f70d5d4c0975ad8aeb', 'tessera/audit/cloud_uploader.py': 'fdf63a5395dfcfbf2543538541541eedbeb821911ebc4362ab3281f689bc9d3b', 'tessera/audit/emitter.py': '78d4aef06076a9a36b0d271afcb06e4e616d68e2bea72f8ff995bea8779b5ed9', 'tessera/audit/sinks/sqlite.py': 'ce18d10955f657e45975e477af05239448ac7cac30edd232b40677b3dd187bc4', 'tessera/auth/base.py': '40635e55ba267bdcd505ef303276de9166a9368a5726cdb65ca5945bb50dac52', 'tessera/cloud_sync.py': '738cc950a1f4ae7eadc97554fc993267924c4c1128d97dc8a5e6e5a033cefa08', 'tessera/policy/cloud_loader.py': '108c91f5ed2cf3f319a51ae05a36907c4ddf57c0621e51eea9e20a05041e9c59'}
_CORE_SHA = "702caf70e196535b559a147aef291d6661d8a90c7afb9d3700b551d496ef538a"
_ROW_KEYS = {"tenant_id", "token_id", "kind", "sub", "principal_pool", "binding_version",
             "scope", "client_id", "created_at", "expires_at", "revoked"}
_CONSUMER_CODES = {
    "AUDIT_ACK_JSON_INVALID", "AUDIT_HTTP_DEADLINE", "AUDIT_HTTP_FAILED", "AUDIT_HTTP_TIMEOUT",
    "AUDIT_HTTP_TRANSPORT_FAILED", "CANONICAL_ACK_VALIDATION_FAILED", "HTTP_CLIENT_INIT_FAILED",
    "LOCAL_AUDIT_CHECKPOINT_FAILED", "LOCAL_CANONICAL_AUDIT_FAILED", "POLICY_CACHE_INIT_FAILED",
    "POLICY_CACHE_VERIFICATION_FAILED", "POLICY_HTTP_DEADLINE", "POLICY_HTTP_FAILED", "POLICY_HTTP_TIMEOUT",
    "POLICY_HTTP_TRANSPORT_FAILED", "POLICY_JSON_INVALID", "PRIVATE_RAM_SETUP_FAILED", "SNAPSHOT_VALIDATION_FAILED",
    "STRICT_POLICY_LOAD_REJECTED", "STRICT_SNAPSHOT_FAILED", "STRICT_SNAPSHOT_REJECTED",
    "VERIFIED_INPUT_PROCESSING_FAILED",
    "CACHE_EXACT_BYTES_MISMATCH", "CANONICAL_ACK_INVALID", "CONNECTION_USE_ALREADY_ATTEMPTED",
    "CONNECTION_USE_FAILED", "CONNECTION_USE_TIMEOUT", "HTTP_CALL_LIMIT", "HTTP_HEADERS_TOO_LARGE",
    "HTTP_REQUEST_TOO_LARGE", "HTTP_RESPONSE_REJECTED", "HTTP_RESPONSE_TOO_LARGE", "HTTP_SCOPE_INVALID",
    "LOCAL_CANONICAL_AUDIT_INVALID", "NEW_TEST_WRITE_NOT_ACKNOWLEDGED", "NOT_STARTED", "PRIVATE_RAM_CLEANUP_FAILED",
    "PRIVATE_RAM_UNAVAILABLE", "SNAPSHOT_DIGEST_INVALID", "SNAPSHOT_INCOMPLETE", "SNAPSHOT_LIMIT",
    "SNAPSHOT_POLICY_LIMIT", "STRICT_POLICY_ID_MISMATCH", "STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
    "SYNCHRONOUS_OPERATOR_REQUIRED", "VERIFIED_INPUT_REQUIRED", "VERIFIED_SCOPE_OR_TENANT_REQUIRED",
}
_DIAGNOSTIC_PHASES = {
    "INPUT", "RAM_SETUP", "CACHE_INIT", "HTTP_CLIENT_INIT", "POLICY_HTTP", "POLICY_JSON", "SNAPSHOT_SHAPE",
    "STRICT_SNAPSHOT", "CACHE_VERIFY", "STRICT_POLICY_LOAD", "AUDIT_LOCAL", "AUDIT_HTTP", "AUDIT_JSON",
    "AUDIT_ACK", "AUDIT_CHECKPOINT", "RAM_CLEANUP", "HTTP_START", "HTTP_HEADERS", "HTTP_BODY_COMPLETE",
}
_DIAGNOSTIC_EXCEPTIONS = {
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ConnectError", "ReadError", "WriteError",
    "CloseError", "LocalProtocolError", "RemoteProtocolError", "ProxyError", "UnsupportedProtocol", "TimeoutError",
    "JSONDecodeError", "ValueError", "TypeError", "ValidationError", "Other",
}
_REQUEST_ID_HEADERS = {"apigw-requestid", "x-amzn-requestid", "x-amz-apigw-id"}
_SAFE = {"RUN_INPUTS_REQUIRED", "RUN_INPUTS_INVALID", "RUN_APPROVAL_INVALID", "RUN_INPUTS_ARGUMENTS_INVALID", "PREFLIGHT_FAILED", "SOURCE_PIN_MISMATCH", "HELPER_NOT_REVIEWED", "PRIVATE_INPUT_INVALID",
         "ACCOUNT_MISMATCH", "TABLE_OR_TTL_MISMATCH", "BINDING_MISMATCH", "DEADLINE_BUDGET",
         "CALLBACK_NOT_VALIDATED", "EXCHANGE_CLAIM_EXISTS", "EXCHANGE_UNKNOWN", "JWT_INVALID",
         "ROW_INVALID", "ROW_CHANGED", "CONSUMER_INVALID", "CONSUMER_FAILED", "OPERATOR_STOPPED", "INTERNAL_ERROR"}


class _OperatorError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if code in _SAFE else "INTERNAL_ERROR"
        super().__init__(self.code)


def _require(condition: object, code: str) -> None:
    if not condition:
        raise _OperatorError(code)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        _require(key not in result, "PRIVATE_INPUT_INVALID")
        result[key] = value
    return result


def _json(raw: bytes) -> dict:
    value = json.loads(raw, object_pairs_hook=_pairs)
    _require(type(value) is dict, "PRIVATE_INPUT_INVALID")
    return value


def _read_pinned(filename: Path, expected: str, limit: int = 1_048_576) -> bytes:
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "PRIVATE_INPUT_INVALID")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        _require(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == expected, "SOURCE_PIN_MISMATCH")
        return raw
    finally:
        os.close(descriptor)


def _load_helper(name: str, expected: str | None):
    _require(isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected), "HELPER_NOT_REVIEWED")
    filename = Path(__file__).with_name(name + ".py")
    raw = _read_pinned(filename, expected)
    spec = importlib.util.spec_from_file_location(name, filename)
    _require(spec is not None, "HELPER_NOT_REVIEWED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Execute the already hash-verified bytes; no import reread/TOCTOU fallback.
    exec(compile(raw, str(filename), "exec"), module.__dict__)  # noqa: S102 - exact reviewed local module
    return module


def _integer(value: object, minimum: int = 0) -> int:
    _require(type(value) is int and minimum <= value <= 2**53, "ROW_INVALID")
    return value


def _unwire(item: dict) -> dict:
    result = {}
    for key, value in item.items():
        _require(type(value) is dict and len(value) == 1, "ROW_INVALID")
        kind, raw = next(iter(value.items()))
        if (kind == "S" and type(raw) is str) or (kind == "BOOL" and type(raw) is bool):
            result[key] = raw
        elif kind == "N" and type(raw) is str and re.fullmatch(r"0|[1-9][0-9]{0,15}", raw):
            result[key] = _integer(int(raw))
        else:
            raise _OperatorError("ROW_INVALID")
    return result


class _Operator:
    def __init__(self, run_args: dict | None = None) -> None:
        self.run_args = run_args
        self.run_label: str | None = None
        self.run_inputs_sha: str | None = None
        self.approval_sha: str | None = None
        self.run_validated = False
        self.deadline = time.monotonic() + 360
        self.ddb = None
        self.http = None
        self.binding = None
        self.refresh = None
        self.access = None
        self.original = None
        self.window = None
        self.plan = None
        self.seed = None
        self.keys = {}
        self.receipt = {
            "schema": "tessera.single_connection_receipt.v2", "testRunId": None, "runLabel": None,
            "account": _ACCOUNT, "region": _REGION, "tableArn": _ARN, "client": "tessera-cli", "scope": _SCOPE,
            "mintOutcome": "NOT_STARTED", "cleanupOutcome": "NOT_REQUIRED", "updateAttemptCount": 0,
            "confirmedRevoked": False, "confirmedRevokedAt": None, "compoundOriginalRowCommitmentSha256": None,
            "cleanupErrorCode": None,
            "originalCreatedAt": None, "originalExpiresAt": None, "revokeOperationPlannedEpoch": None,
            "accessExpiresAt": None, "accessLifetimeVerified": False, "normalFlowGeneratedCodeConsumed": "UNKNOWN",
            "accessRevoked": False, "status": "BLOCKED", "errorCode": None, "connectionUse": None,
            "sourcePins": {"planSha256": _PLAN_SHA, "seedManifestSha256": _SEED_SHA,
                           "receiverSha256": _RECEIVER_SHA, "acceptanceUseSha256": _USE_SHA,
                           "consumerCoreManifestSha256": _CORE_SHA, "runInputsSha256": None, "approvalSha256": None},
        }

    def remaining(self, reserve: float = 0) -> None:
        _require(time.monotonic() + reserve < self.deadline, "DEADLINE_BUDGET")

    def _http_json(self, method: str, url: str, data: dict | None = None) -> dict:
        self.remaining(5)
        _require((method, url) in {("GET", _JWKS_URL), ("POST", _TOKEN_URL)}, "PREFLIGHT_FAILED")
        with self.http.stream(method, url, data=data) as response:
            _require(response.status_code == 200, "EXCHANGE_UNKNOWN" if method == "POST" else "PREFLIGHT_FAILED")
            raw = bytearray()
            _require(response.headers.get("Content-Encoding") in (None, "identity")
                     and sum(len(k) + len(v) for k, v in response.headers.items()) <= 8192,
                     "EXCHANGE_UNKNOWN" if method == "POST" else "PREFLIGHT_FAILED")
            for chunk in response.iter_bytes(chunk_size=4096):
                self.remaining(4)
                raw.extend(chunk)
                _require(len(raw) <= 65536, "EXCHANGE_UNKNOWN" if method == "POST" else "PREFLIGHT_FAILED")
            return _json(bytes(raw))

    def validate_run_inputs(self) -> None:
        # Pure local admission comes FIRST: no SDK, helpers, network or claim writes.
        args = self.run_args
        _require(type(args) is dict and set(args) == {"run_inputs", "run_inputs_sha256", "approval_sha256"},
                 "RUN_INPUTS_REQUIRED")
        for key in ["run_inputs_sha256", "approval_sha256"]:
            _require(type(args[key]) is str and re.fullmatch(r"[a-f0-9]{64}", args[key]), "RUN_INPUTS_INVALID")
        _require(type(args["run_inputs"]) is str, "RUN_INPUTS_INVALID")
        filename = Path(args["run_inputs"])
        # This reviewed preparation admits attempt2 only; no automatic label rollover.
        run_label = "attempt2"
        _require(filename.is_absolute(), "RUN_INPUTS_INVALID")
        _require(args["run_inputs"] == str(_AUDIT / f"connection-{run_label}-inputs.json"), "RUN_INPUTS_INVALID")
        inputs = _json(_read_pinned(filename, args["run_inputs_sha256"], 65536))
        pins = {"schema": "tessera.single_connection_run_inputs.v1", "runLabel": run_label,
                "account": _ACCOUNT, "region": _REGION, "client": "tessera-cli", "scope": _SCOPE,
                "planSha256": _PLAN_SHA, "seedManifestSha256": _SEED_SHA, "receiverSha256": _RECEIVER_SHA,
                "acceptanceUseSha256": _USE_SHA, "consumerCoreManifestSha256": _CORE_SHA}
        _require(set(inputs) == set(pins) | {"testRunId"} and all(inputs[k] == v for k, v in pins.items()),
                 "RUN_INPUTS_INVALID")
        run_id = inputs["testRunId"]
        _require(type(run_id) is str and len(run_id) == 36, "RUN_INPUTS_INVALID")
        try:
            parsed_id = uuid.UUID(run_id)
        except ValueError:
            raise _OperatorError("RUN_INPUTS_INVALID") from None
        _require(str(parsed_id) == run_id and parsed_id.version == 4 and parsed_id.variant == uuid.RFC_4122,
                 "RUN_INPUTS_INVALID")
        approval_path = _AUDIT / f"single-connection-{run_label}-founder-approval.json"
        approval = _json(_read_pinned(approval_path, args["approval_sha256"], 65536))
        expected = {"schema": "tessera.single_connection_approval.v2", "approvalStatus": "APPROVED",
                    "runLabel": run_label, "testRunId": run_id, "runInputsSha256": args["run_inputs_sha256"],
                    "client": "tessera-cli", "scope": _SCOPE, "planSha256": _PLAN_SHA,
                    "noOrdinaryDiskTokenCache": True, "residualAccessAcknowledged": True,
                    "unknownExchangeNoRetry": True, "decision": "Approve one test"}
        _require(set(approval) == set(expected) | {"operatorSha256", "approvedAtUTC"}
                 and all(type(approval[k]) is type(v) and approval[k] == v for k, v in expected.items()),
                 "RUN_APPROVAL_INVALID")
        _require(type(approval["operatorSha256"]) is str
                 and re.fullmatch(r"[a-f0-9]{64}", approval["operatorSha256"]), "RUN_APPROVAL_INVALID")
        _read_pinned(Path(__file__), approval["operatorSha256"])
        approved_at = approval["approvedAtUTC"]
        _require(type(approved_at) is str and 20 <= len(approved_at) <= 40
                 and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]{1,9})?Z", approved_at),
                 "RUN_APPROVAL_INVALID")
        try:
            datetime.fromisoformat(approved_at[:-1] + "+00:00")
        except ValueError:
            raise _OperatorError("RUN_APPROVAL_INVALID") from None
        self.run_label, self.run_inputs_sha, self.approval_sha = run_label, args["run_inputs_sha256"], args["approval_sha256"]
        self.receipt.update(testRunId=run_id, runLabel=run_label)
        self.receipt["sourcePins"].update(runInputsSha256=self.run_inputs_sha, approvalSha256=self.approval_sha)
        self.run_validated = True

    def claim_names(self) -> tuple[str, str]:
        _require(self.run_validated and self.run_label is not None, "RUN_INPUTS_REQUIRED")
        return (f"connection-flow-{self.run_label}.started.json", f"connection-exchange-{self.run_label}.started.json")

    def _reviewed_helpers(self):
        self.plan = _json(_read_pinned(_AUDIT / "oauth-one-test-revocation-plan-v2.json", _PLAN_SHA))
        for relative, expected in _SOURCE_PINS.items():
            _read_pinned(_SOURCE / relative, expected)
        runtime = _ROOT / "cloudmorph-tessera"
        for relative, expected in _CORE_PINS.items():
            _read_pinned(runtime / relative, expected)
        # Select only the reviewed workspace core, then verify actual loaded origins.
        sys.path.insert(0, str(runtime))
        receiver = _load_helper("ephemeral_oauth_receiver", _RECEIVER_SHA)
        consumer = _load_helper("ephemeral_oauth_acceptance_use", _USE_SHA)
        for relative, expected in _CORE_PINS.items():
            if not relative.endswith(".py"):
                continue
            module_name = relative[:-3].replace("/", ".")
            loaded = sys.modules.get(module_name)
            actual = getattr(loaded, "__file__", None)
            _require(isinstance(actual, str) and Path(actual).resolve() == (runtime / relative).resolve(),
                     "HELPER_NOT_REVIEWED")
            _read_pinned(Path(actual), expected)
        _require(callable(getattr(consumer, "run_connection_use", None))
                 and getattr(consumer, "SCHEMA", None) == "tessera.scripted_connection_test.v2", "HELPER_NOT_REVIEWED")
        return receiver, consumer

    def preflight(self):
        self.validate_run_inputs()
        _require(sys.platform == "linux" and not os.environ.get("TESSERA_DEBUG"), "PREFLIGHT_FAILED")
        for name in self.claim_names():
            try:
                (_AUDIT / name).lstat()
            except FileNotFoundError:
                pass
            else:
                raise _OperatorError("EXCHANGE_CLAIM_EXISTS")
        receiver, consumer = self._reviewed_helpers()
        seed = _json(_read_pinned(_SEED, _SEED_SHA, 65536))
        _require(seed.get("format") == "tessera.identity.migration.v1" and type(seed.get("bindings")) is list
                 and len(seed["bindings"]) == 1, "BINDING_MISMATCH")
        self.seed = seed["bindings"][0]
        _require(self.seed.get("pool_id") == _POOL and self.seed.get("platform_admin") is False
                 and self.seed.get("entitlement_tier") == "free" and self.seed.get("role") in {"owner", "admin", "member", "viewer"}
                 and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.seed.get("sub", ""))
                 and re.fullmatch(r"[a-z0-9_-]{1,64}", self.seed.get("tenant_id", ""))
                 and not self.seed["tenant_id"].startswith("__"), "BINDING_MISMATCH")
        config = Config(region_name=_REGION, signature_version="v4", retries={"total_max_attempts": 1},
                        connect_timeout=2, read_timeout=3)
        session = boto3.Session(region_name=_REGION)
        sts = session.client("sts", config=config, endpoint_url=f"https://sts.{_REGION}.amazonaws.com")
        try:
            _require(sts.get_caller_identity().get("Account") == _ACCOUNT, "ACCOUNT_MISMATCH")
        finally:
            sts.close()
        self.ddb = session.client("dynamodb", config=config, endpoint_url=f"https://dynamodb.{_REGION}.amazonaws.com")
        table = self.ddb.describe_table(TableName=_TABLE).get("Table", {})
        _require(table.get("TableArn") == _ARN and table.get("TableStatus") == "ACTIVE"
                 and table.get("KeySchema") == [{"AttributeName": "tenant_id", "KeyType": "HASH"},
                                                {"AttributeName": "token_id", "KeyType": "RANGE"}], "TABLE_OR_TTL_MISMATCH")
        ttl = self.ddb.describe_time_to_live(TableName=_TABLE).get("TimeToLiveDescription", {})
        _require(ttl == {"TimeToLiveStatus": "ENABLED", "AttributeName": "expires_at"}, "TABLE_OR_TTL_MISMATCH")
        self.binding = self.live_binding()
        # HTTPX's default transport retries=0. Preserve existing proxy/certificate
        # settings; never set a redirect/auth/retry hook or change process env.
        self.http = httpx.Client(follow_redirects=False, trust_env=True, timeout=httpx.Timeout(3, connect=2),
                                 headers={"Accept-Encoding": "identity"})
        jwks = self._http_json("GET", _JWKS_URL)
        _require(set(jwks) == {"keys"} and type(jwks["keys"]) is list and 1 <= len(jwks["keys"]) <= 4,
                 "JWT_INVALID")
        for key in jwks["keys"]:
            _require(type(key) is dict and set(key) <= {"kty", "crv", "x", "kid", "use", "alg"}
                     and key.get("kty") == "OKP" and key.get("crv") == "Ed25519" and key.get("alg") == "EdDSA"
                     and key.get("use") == "sig" and isinstance(key.get("kid"), str)
                     and 0 < len(key["kid"]) <= 128 and key["kid"] not in self.keys, "JWT_INVALID")
            self.keys[key["kid"]] = jwt.PyJWK.from_dict(key, algorithm="EdDSA").key
        self.remaining(345)  # Leave a full300s callback window plus bounded cleanup reserve.
        return receiver, consumer

    def live_binding(self) -> dict:
        self.remaining(5)
        fields = ["principal_id", "pool_id", "sub", "tenant_id", "role", "platform_admin", "entitlement_tier",
                  "status", "binding_version", "min_token_iat", "migration_manifest_sha256"]
        names = {f"#f{i}": value for i, value in enumerate(fields)}
        result = self.ddb.get_item(TableName=_IDENTITY_TABLE,
            Key={"principal_id": {"S": _POOL + "#" + self.seed["sub"]}}, ConsistentRead=True,
            ProjectionExpression=", ".join(names), ExpressionAttributeNames=names)
        item = _unwire(result.get("Item", {}))
        _require(set(item) == set(fields) and item["principal_id"] == _POOL + "#" + self.seed["sub"]
                 and item["status"] == "active" and type(item["platform_admin"]) is bool
                 and item["platform_admin"] is False and item["migration_manifest_sha256"] == _SEED_SHA
                 and all(item[key] == self.seed[key] for key in
                         ["pool_id", "sub", "tenant_id", "role", "platform_admin", "entitlement_tier"]), "BINDING_MISMATCH")
        _integer(item["binding_version"], 1)
        _integer(item["min_token_iat"])
        return item

    def claim_once(self, *, exchange: bool) -> None:
        # This marker covers one Allow/nonce/code flow even if callback/exchange never completes.
        names = self.claim_names()
        name = names[1] if exchange else names[0]
        filename = _AUDIT / name
        marker = {"status": "EXCHANGE_STARTED" if exchange else "FLOW_STARTED", "testRunId": self.receipt["testRunId"], "at": int(time.time()),
                  "account": _ACCOUNT, "region": _REGION, "planSha256": _PLAN_SHA, "runLabel": self.run_label,
                  "runInputsSha256": self.run_inputs_sha, "approvalSha256": self.approval_sha}
        try:
            descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise _OperatorError("EXCHANGE_CLAIM_EXISTS") from None
        try:
            raw = json.dumps(marker, sort_keys=True).encode("ascii") + b"\n"
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(descriptor)
            directory = os.open(_AUDIT, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.close(descriptor)

    def verify_token(self, token: object, kind: str) -> dict:
        _require(type(token) is str and len(token) <= 16384, "JWT_INVALID")
        segments = token.split(".")
        _require(len(segments) == 3, "JWT_INVALID")
        _json(jwt.utils.base64url_decode(segments[0]))
        _json(jwt.utils.base64url_decode(segments[1]))
        header = jwt.get_unverified_header(token)
        _require(set(header) <= {"alg", "kid", "typ"} and header.get("alg") == "EdDSA"
                 and header.get("typ") == "JWT" and header.get("kid") in self.keys, "JWT_INVALID")
        required = ["iss", "aud", "sub", "tenant_id", "scope", "client_id", "tier", "iat", "exp", "jti",
                    "token_type", "principal_pool", "binding_version"]
        claims = jwt.decode(token, key=self.keys[header["kid"]], algorithms=["EdDSA"],
                            audience="tessera.cloudmorph.ai/api", issuer="tessera.cloudmorph.ai", leeway=5,
                            options={"require": required, "strict_aud": True})
        _require(set(claims) == set(required) and claims["token_type"] == kind and claims["client_id"] == "tessera-cli"
                 and claims["scope"] == _SCOPE and claims["tier"] == "free" and claims["principal_pool"] == _POOL
                 and claims["tenant_id"] == self.seed["tenant_id"] and claims["sub"] == self.seed["sub"]
                 and type(claims["binding_version"]) is int and claims["binding_version"] == self.binding["binding_version"],
                 "JWT_INVALID")
        issued = _integer(claims["iat"])
        expires = _integer(claims["exp"])
        _require(self.window[0] - 5 <= issued <= self.window[1] + 5 and issued >= self.binding["min_token_iat"]
                 and expires - issued == (3600 if kind == "access" else 2592000) and expires > int(time.time()), "JWT_INVALID")
        if kind == "refresh":
            _require(re.fullmatch(r"[a-f0-9]{64}", claims["jti"]), "JWT_INVALID")
        else:
            _require(str(uuid.UUID(claims["jti"], version=4)) == claims["jti"], "JWT_INVALID")
        return claims

    def read_row(self) -> dict:
        self.remaining(5)
        result = self.ddb.get_item(TableName=_TABLE, Key=self.row_key(), ConsistentRead=True)
        item = _unwire(result.get("Item", {}))
        revoked = item.get("revoked") is True
        _require(set(item) == _ROW_KEYS | ({"revoked_at"} if revoked else set()), "ROW_INVALID")
        expected = {"tenant_id": self.refresh["tenant_id"], "token_id": self.refresh["jti"], "kind": "refresh_token",
                    "sub": self.refresh["sub"], "principal_pool": self.refresh["principal_pool"],
                    "binding_version": self.refresh["binding_version"], "scope": _SCOPE, "client_id": "tessera-cli"}
        _require(all(item.get(key) == value for key, value in expected.items()) and type(item["revoked"]) is bool,
                 "ROW_INVALID")
        _integer(item["binding_version"], 1)
        created = _integer(item["created_at"])
        expires = _integer(item["expires_at"])
        _require(self.window[0] - 5 <= created <= self.window[1] + 5 and expires == created + 2592000
                 and expires > int(time.time()) and abs(created - self.refresh["iat"]) <= 5, "ROW_INVALID")
        if revoked:
            _integer(item["revoked_at"])
        return item

    def row_key(self) -> dict:
        return {"tenant_id": {"S": self.refresh["tenant_id"]}, "token_id": {"S": self.refresh["jti"]}}

    def remember_original(self, row: dict) -> None:
        _require(row["revoked"] is False, "ROW_CHANGED")
        self.original = row
        canonical = json.dumps({"targetArn": _ARN, "originalRow": row}, sort_keys=True,
                               separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self.receipt.update(compoundOriginalRowCommitmentSha256=hashlib.sha256(canonical).hexdigest(),
                            originalCreatedAt=row["created_at"], originalExpiresAt=row["expires_at"])

    def cleanup(self) -> None:
        if self.refresh is None:
            return
        self.receipt["cleanupOutcome"] = "RECONCILIATION_REQUIRED"
        try:
            self.remaining(20)
            _require(self.live_binding() == self.binding, "BINDING_MISMATCH")
            fresh = self.read_row()
            if fresh["revoked"] is True:
                if self.original is not None:
                    _require({**fresh, "revoked": False, "revoked_at": None}
                             == {**self.original, "revoked_at": None}, "ROW_CHANGED")
                self.receipt.update(cleanupOutcome="ALREADY_REVOKED", confirmedRevoked=True,
                                    confirmedRevokedAt=fresh["revoked_at"])
                return
            if self.original is None:
                self.remember_original(fresh)
            _require(fresh == self.original and self.receipt["updateAttemptCount"] == 0, "ROW_CHANGED")
            now = int(time.time())
            self.receipt["revokeOperationPlannedEpoch"] = now
            update = dict(self.plan["proposedUpdateItem"])
            update.pop("sdkRetries")
            update["Key"] = self.row_key()
            update["ExpressionAttributeValues"] = {
                ":kind": {"S": "refresh_token"}, ":client": {"S": "tessera-cli"}, ":pool": {"S": fresh["principal_pool"]},
                ":sub": {"S": fresh["sub"]}, ":version": {"N": str(fresh["binding_version"])}, ":scope": {"S": _SCOPE},
                ":created": {"N": str(fresh["created_at"])}, ":expires": {"N": str(fresh["expires_at"])},
                ":now": {"N": str(now)}, ":false": {"BOOL": False}, ":true": {"BOOL": True},
            }
            self.receipt["updateAttemptCount"] = 1
            try:
                self.ddb.update_item(**update)
            except Exception:
                pass  # Outcome is reconciled only through the same exact key, never a write retry.
            after = self.read_row()
            _require(after == {**self.original, "revoked": True, "revoked_at": now}, "ROW_CHANGED")
            self.receipt.update(cleanupOutcome="CONFIRMED_REVOKED", confirmedRevoked=True, confirmedRevokedAt=now)
        except Exception as error:
            self.receipt["cleanupOutcome"] = "RECONCILIATION_REQUIRED"
            self.receipt["cleanupErrorCode"] = error.code if isinstance(error, _OperatorError) else "READ_OR_UPDATE_UNKNOWN"

    def run(self) -> dict:
        # Reserve30s for cleanup; POSIX alarm interrupts synchronous parser work,
        # not only cooperative HTTP deadlines. This process owns this timer.
        previous_alarm_handler = signal.signal(signal.SIGALRM, _deadline_stopped)
        signal.setitimer(signal.ITIMER_REAL, max(0.01, self.deadline - 30 - time.monotonic()))
        pair = callback = None
        access_token = None
        try:
            receiver_module, consumer = self.preflight()
            self.claim_once(exchange=False)
            with receiver_module.EphemeralOAuthReceiver(timeout=300) as receiver:
                redirect_uri = receiver.redirect_uri
                print(json.dumps({"status": "READY", "authorizationURL": receiver.authorization_url}), flush=True)
                callback = receiver.wait_for_callback()
            _require(callback.outcome == "VALIDATED_CALLBACK", "CALLBACK_NOT_VALIDATED")
            self.remaining(40)
            self.claim_once(exchange=True)
            self.receipt.update(mintOutcome="UNKNOWN", cleanupOutcome="UNKNOWN_TARGET")
            self.window = [int(time.time()), None]
            pair = self._http_json("POST", _TOKEN_URL, {"grant_type": "authorization_code", "client_id": "tessera-cli",
                "code": callback.code, "code_verifier": callback.verifier, "redirect_uri": redirect_uri})
            self.window[1] = int(time.time())
            # Establish an authenticated cleanup target even if the access half/response envelope is invalid.
            self.refresh = self.verify_token(pair.get("refresh_token"), "refresh")
            self.receipt.update(mintOutcome="VERIFIED_REFRESH_ONLY", cleanupOutcome="RECONCILIATION_REQUIRED")
            _require(set(pair) == {"access_token", "refresh_token", "token_type", "expires_in", "scope"}
                     and pair["token_type"] == "Bearer" and type(pair["expires_in"]) is int  # noqa: S105 - protocol literal
                     and pair["expires_in"] == 3600 and pair["scope"] == _SCOPE, "EXCHANGE_UNKNOWN")
            self.access = self.verify_token(pair["access_token"], "access")
            _require(all(self.access[k] == self.refresh[k] for k in
                         ["tenant_id", "sub", "principal_pool", "binding_version", "scope", "client_id", "tier"]), "JWT_INVALID")
            self.receipt.update(mintOutcome="VERIFIED_PAIR", accessExpiresAt=self.access["exp"],
                                accessLifetimeVerified=True, normalFlowGeneratedCodeConsumed=True)
            self.remember_original(self.read_row())
            access_token = pair["access_token"]
            pair = callback = None
            if time.monotonic() + 50 < self.deadline:
                try:
                    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                        result = consumer.run_connection_use(access_token, self.access)
                except Exception:
                    raise _OperatorError("CONSUMER_FAILED") from None
                self.receipt["connectionUse"] = _safe_consumer(result)
            else:
                self.receipt["connectionUse"] = {"status": "SKIPPED_CLEANUP_BUDGET"}
        except BaseException as error:
            self.receipt["errorCode"] = error.code if isinstance(error, _OperatorError) else "INTERNAL_ERROR"
        finally:
            # Cleanup has its own absolute interruption before the360s outer cap.
            signal.setitimer(signal.ITIMER_REAL, max(0.01, self.deadline - 5 - time.monotonic()))
            try:
                self.cleanup()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_alarm_handler)
                with contextlib.suppress(Exception):
                    if self.http is not None:
                        self.http.close()
                with contextlib.suppress(Exception):
                    if self.ddb is not None:
                        self.ddb.close()
                pair = callback = access_token = None
                self.refresh = self.access = self.original = self.binding = self.seed = None
                self.keys.clear()
        if self.receipt["mintOutcome"] == "VERIFIED_PAIR" and self.receipt["confirmedRevoked"]:
            self.receipt["status"] = "PAIR_VERIFIED_REFRESH_REVOKED_ACCESS_SURVIVES_TO_EXPIRY"
        if (self.receipt["mintOutcome"] == "VERIFIED_PAIR" and self.receipt["confirmedRevoked"]
                and self.receipt["errorCode"] is None and self.receipt["connectionUse"] is not None
                and self.receipt["connectionUse"].get("status") == "ACKNOWLEDGED"):
            self.receipt["status"] = "CONNECTION_TEST_ACKNOWLEDGED_REFRESH_REVOKED_ACCESS_SURVIVES"
        return self.receipt


def _safe_consumer(value: object) -> dict:
    # The helper owns semantic ACK validation. Never print unknown keys or values.
    keys = {"schema", "status", "reason_code", "snapshot_digest", "policy_count", "ack_written", "stream_id",
            "chain_head", "label", "proxy_runtime_active", "independent_agent_observed", "diagnostics"}
    _require(type(value) is dict and set(value) == keys and value["schema"] == "tessera.scripted_connection_test.v2"
             and value["status"] in {"ACKNOWLEDGED", "BLOCKED", "UNKNOWN"}
             and value["label"] == "SCRIPTED CONNECTION TEST" and value["proxy_runtime_active"] is False
             and value["independent_agent_observed"] is False, "CONSUMER_INVALID")
    _require(type(value["reason_code"]) is str and value["reason_code"] in _CONSUMER_CODES, "CONSUMER_INVALID")
    for key in ["snapshot_digest", "chain_head"]:
        _require(value[key] is None or type(value[key]) is str and re.fullmatch(r"[a-f0-9]{64}", value[key]), "CONSUMER_INVALID")
    _require(value["policy_count"] is None or type(value["policy_count"]) is int and 0 <= value["policy_count"] <= 10000,
             "CONSUMER_INVALID")
    _require(value["ack_written"] is None or type(value["ack_written"]) is int and value["ack_written"] in {0, 1}, "CONSUMER_INVALID")
    _require(value["stream_id"] is None or type(value["stream_id"]) is str
             and re.fullmatch(r"scripted-connection-test-[a-f0-9]{32}", value["stream_id"]), "CONSUMER_INVALID")
    _safe_diagnostics(value["diagnostics"])
    return value


def _safe_diagnostics(records: object) -> None:
    _require(type(records) is list and len(records) <= 2, "CONSUMER_INVALID")
    keys = {"method", "phase", "started", "headers_received", "body_complete", "elapsed_ms", "http_status",
            "exception_class", "request_id_sha256"}
    for index, record in enumerate(records):
        _require(type(record) is dict and set(record) == keys, "CONSUMER_INVALID")
        _require(record["method"] == ("GET" if index == 0 else "POST")
                 and type(record["phase"]) is str and record["phase"] in _DIAGNOSTIC_PHASES, "CONSUMER_INVALID")
        _require(all(type(record[k]) is bool for k in ["started", "headers_received", "body_complete"])
                 and (not record["body_complete"] or record["headers_received"])
                 and (not record["headers_received"] or record["started"]), "CONSUMER_INVALID")
        _require(type(record["elapsed_ms"]) is int and 0 <= record["elapsed_ms"] <= 30000
                 and (record["started"] or record["elapsed_ms"] == 0), "CONSUMER_INVALID")
        status = record["http_status"]
        _require((status is None or type(status) is int and 100 <= status <= 599)
                 and (record["headers_received"] == (status is not None)), "CONSUMER_INVALID")
        _require(record["exception_class"] is None or type(record["exception_class"]) is str
                 and record["exception_class"] in _DIAGNOSTIC_EXCEPTIONS, "CONSUMER_INVALID")
        hashes = record["request_id_sha256"]
        _require(type(hashes) is dict and set(hashes) <= _REQUEST_ID_HEADERS and len(hashes) <= 3
                 and (record["headers_received"] or not hashes)
                 and all(type(v) is str and re.fullmatch(r"[a-f0-9]{64}", v) for v in hashes.values()), "CONSUMER_INVALID")
        if index == 1:
            _require(records[0]["body_complete"] and type(records[0]["http_status"]) is int
                     and 200 <= records[0]["http_status"] < 300, "CONSUMER_INVALID")


def _cli_run_args(argv: list[str]) -> dict:
    _require(len(argv) == 7 and argv[0] == "--approved-single-connection", "RUN_INPUTS_ARGUMENTS_INVALID")
    names = {"--run-inputs": "run_inputs", "--run-inputs-sha256": "run_inputs_sha256",
             "--approval-sha256": "approval_sha256"}
    args = {}
    for index in [1, 3, 5]:
        flag = argv[index]
        _require(flag in names and names[flag] not in args and bool(argv[index + 1]), "RUN_INPUTS_ARGUMENTS_INVALID")
        args[names[flag]] = argv[index + 1]
    _require(set(args) == set(names.values()), "RUN_INPUTS_ARGUMENTS_INVALID")
    _require(Path(args["run_inputs"]).is_absolute()
             and all(re.fullmatch(r"[a-f0-9]{64}", args[k]) for k in ["run_inputs_sha256", "approval_sha256"]),
             "RUN_INPUTS_ARGUMENTS_INVALID")
    return args


def _deadline_stopped(signum: int, frame: object) -> None:
    raise _OperatorError("DEADLINE_BUDGET")


def _stopped(signum: int, frame: object) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise _OperatorError("OPERATOR_STOPPED")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        run_args = _cli_run_args(sys.argv[1:])
    except Exception:
        print('{"status":"BLOCKED","errorCode":"RUN_INPUTS_ARGUMENTS_INVALID"}', flush=True)
        raise SystemExit(2) from None
    signal.signal(signal.SIGINT, _stopped)
    signal.signal(signal.SIGTERM, _stopped)
    try:
        receipt = _Operator(run_args).run()
    except BaseException:
        print('{"status":"BLOCKED","errorCode":"INTERNAL_ERROR"}', flush=True)
        raise SystemExit(1) from None
    print(json.dumps(receipt, sort_keys=True), flush=True)
    raise SystemExit(0 if receipt["status"] == "CONNECTION_TEST_ACKNOWLEDGED_REFRESH_REVOKED_ACCESS_SURVIVES" else 1)
