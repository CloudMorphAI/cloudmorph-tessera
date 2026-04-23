# 07 — Common Layer Audit

_865 LoC of byte-identical duplication across 5 executors. The single highest-leverage refactor in the codebase — does not need invention, just discipline._

---

## 1.1 Confirmed duplication

Verified by `diff` (this rescan):

| File family | Per-cloud LoC | Copies | Total dup LoC | Verified identical |
|---|---:|---:|---:|---|
| `controlcenter_client.py` | 173 | 5 | **865** | yes (`diff aws/.../controlcenter_client.py snowflake/.../controlcenter_client.py` → empty) |
| `storage_pointers.py` (base) | 9 | 4 | **36** | yes (aws ≡ azure ≡ databricks ≡ snowflake) |
| `storage_pointers.py` (gcp) | 19 | 1 | — | gcp adds `build_gcs_pointer` helper; base 9-LoC `build_pointer` matches the others |
| `main.py` lifecycle | 403–470 | 5 | ~1500 LoC of structural near-duplicate (~70% overlap on claim/heartbeat/complete/sigterm/JSON-log/redaction patterns) | partial — verified by structural comparison this pass |

**Pure copy-paste:** **901 LoC** (865 + 36).
**Structural near-duplicate:** ~1,500 LoC across the 5 `main.py` files.

After extraction, the executors should be ~30 LoC entry points + cloud-specific handlers.

---

## 1.2 The proposed packages

### `cloudmorph-common-py/`

```
cloudmorph-common-py/
├── pyproject.toml
├── cloudmorph_common/
│   ├── __init__.py
│   ├── client.py                      # ControlCenterClient (extracted from current dup)
│   ├── errors.py                      # ControlCenterError + new structured errors
│   ├── base_executor.py               # BaseExecutor: claim/run/heartbeat/complete loop
│   ├── lifecycle/
│   │   ├── claim.py                   # claim_job + retry/backoff
│   │   ├── heartbeat.py               # threading-based heartbeat loop
│   │   ├── shutdown.py                # SIGTERM/SIGINT graceful shutdown
│   ├── log.py                         # JSON-line logger with severity map (replaces _log in each main.py)
│   ├── redact.py                      # _redact + _SAFE_PAYLOAD_KEYS pattern
│   ├── env.py                         # _require_env, _float_env helpers
│   ├── schema.py                      # _load_job_schema + _validate_job
│   ├── action_verbs.py                # action → verb-set mapping (cross-cuts with intent system)
│   ├── audit/
│   │   ├── emitter.py                 # AuditEmitter — emits AuditEvent to sinks (mirrors MCP TS impl)
│   │   ├── chain.py                   # hash chain bookkeeping
│   │   └── sinks/
│   │       ├── stdout.py
│   │       ├── s3.py
│   │       └── buffered.py            # disk-backed bounded queue for sink failure
│   ├── artifacts/
│   │   ├── base.py                    # ArtifactWriter interface
│   │   ├── s3.py                      # S3ArtifactWriter
│   │   ├── gcs.py                     # GcsArtifactWriter
│   │   └── blob.py                    # BlobArtifactWriter
│   ├── settings.py                    # Pydantic Settings; per-tenant config
│   ├── storage_pointers.py            # base build_pointer (replaces 4 dup files)
│   └── contracts/                     # generated from /contracts/*.schema.json
│       ├── __init__.py
│       ├── audit_event.py
│       ├── intent_declaration.py
│       ├── policy_decision.py
│       ├── runtime_context.py
│       ├── tool_call_request.py
│       ├── job.py
│       ├── request.py
│       ├── approval.py
│       ├── session.py
│       └── policy_bundle.py
└── tests/
    ├── test_base_executor.py
    ├── test_client.py
    ├── test_audit_emitter.py
    ├── test_audit_chain.py
    ├── test_artifact_writers.py
    └── test_action_verbs_complete.py
```

### `cloudmorph-common-ts/`

Mirror for the MCP server (and future TS SDK):

```
cloudmorph-common-ts/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts
│   ├── contracts/                    # generated from /contracts/*.schema.json
│   │   └── *.ts
│   ├── audit/
│   │   ├── emitter.ts
│   │   ├── chain.ts
│   │   └── sinks/{stdout,s3,buffered}.ts
│   ├── action-verbs.ts                # mirror of action_verbs.py
│   └── canonical-json.ts              # RFC 8785 JCS for hash chain
└── tests/
    └── ...
```

---

## 1.3 BaseExecutor design

```python
# cloudmorph_common/base_executor.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cloudmorph_common.client import ControlCenterClient
from cloudmorph_common.audit.emitter import AuditEmitter
from cloudmorph_common.artifacts.base import ArtifactWriter
from cloudmorph_common.contracts.job import Job

@dataclass(frozen=True)
class ExecutorConfig:
    base_url: str
    install_token: str
    tenant_id: str
    account_id: str
    capabilities: list[str]
    executor_id: str
    heartbeat_seconds: float = 20.0
    poll_base_seconds: float = 2.0
    poll_max_seconds: float = 15.0
    one_shot_job_id: Optional[str] = None
    one_shot_job_token: Optional[str] = None

class BaseExecutor(ABC):
    """Common executor lifecycle. Subclass for per-cloud action handlers."""
    
    def __init__(
        self,
        config: ExecutorConfig,
        client: ControlCenterClient,
        artifact_writer: ArtifactWriter,
        audit_emitter: AuditEmitter,
    ):
        self.config = config
        self.client = client
        self.artifact_writer = artifact_writer
        self.audit = audit_emitter
        self._shutdown = threading.Event()

    def run(self) -> None:
        """Main loop. Called from each cloud's main.py entrypoint."""
        if self.config.one_shot_job_id:
            return self._run_once()
        return self._run_daemon()

    def _run_daemon(self) -> None:
        """Long-running claim/run/complete loop."""
        signal.signal(signal.SIGTERM, lambda *_: self._shutdown.set())
        signal.signal(signal.SIGINT, lambda *_: self._shutdown.set())
        backoff = self.config.poll_base_seconds
        while not self._shutdown.is_set():
            try:
                claim = self.client.claim_job(...)
            except ControlCenterError as exc:
                self._sleep_backoff(backoff); backoff = min(self.config.poll_max_seconds, backoff*2); continue
            if not claim:
                self._sleep_backoff(backoff); backoff = min(self.config.poll_max_seconds, backoff*2); continue
            backoff = self.config.poll_base_seconds
            self._execute_job(claim)

    def _execute_job(self, claim: Dict[str, Any]) -> None:
        job = self._validate_job(claim["job"])
        self.audit.emit("executor.job.claimed", payload={"jobId": claim["jobId"]})
        with self._heartbeat(claim):
            try:
                result = self.run_action(job)
                artifacts = self.artifact_writer.write(claim["jobId"], result)
                self.client.post_complete(...)
                self.audit.emit("executor.job.completed", ...)
            except Exception as exc:
                self.client.post_complete(... status="failed", reason=str(exc))
                self.audit.emit("executor.job.failed", payload={"error": str(exc)})

    @abstractmethod
    def run_action(self, job: Job) -> Dict[str, Any]:
        """Per-cloud action dispatcher. Implemented by AwsExecutor, AzureExecutor, etc."""
        ...
```

### Per-cloud subclass

```python
# aws/executor/src/main.py (post-refactor)

from cloudmorph_common.base_executor import BaseExecutor, ExecutorConfig
from cloudmorph_common.client import ControlCenterClient
from cloudmorph_common.audit.emitter import AuditEmitter
from cloudmorph_common.artifacts.s3 import S3ArtifactWriter
from cloudmorph_common.contracts.job import Job

from actions import ACTIONS  # the registry from aws/executor/src/actions/__init__.py

class AwsExecutor(BaseExecutor):
    def run_action(self, job: Job) -> dict:
        action = job.action
        handler = ACTIONS.get(action)
        if not handler:
            return {"status": "failed", "reason": f"unsupported_action:{action}"}
        return handler(job.payload)

if __name__ == "__main__":
    cfg = ExecutorConfig.from_env()  # Pydantic Settings
    client = ControlCenterClient(cfg.base_url, cfg.install_token)
    artifact = S3ArtifactWriter.from_env()
    audit = AuditEmitter.from_env()
    AwsExecutor(cfg, client, artifact, audit).run()
```

**Per-cloud `main.py` shrinks from 400-470 LoC to ~30 LoC.**

---

## 1.4 Migration plan

Phased; each phase keeps the executors green:

### Phase 1 — Extract & publish (Block C, day 2-3)

1. Create `cloudmorph-common-py/` directory in repo root (sibling to `cloudmorph-mcp/`, `aws/`, etc.).
2. Author `cloudmorph_common.client.ControlCenterClient` from one of the byte-identical files (pick aws as canonical).
3. Author `cloudmorph_common.storage_pointers` (the 9-LoC `build_pointer`).
4. Author `cloudmorph_common.base_executor.BaseExecutor` from `aws/executor/src/main.py`'s lifecycle.
5. Author `cloudmorph_common.audit.emitter.AuditEmitter` (new — the audit emission was missing per-executor).
6. Author `cloudmorph_common.artifacts.{s3,gcs,blob}.*ArtifactWriter` (extracted from each cloud's `_upload_artifacts`).
7. Set up `pyproject.toml` with `name = "cloudmorph-common"` (no PyPI publish yet — local pip install for now).
8. Tests in `cloudmorph-common-py/tests/` — minimum 80% coverage.

### Phase 2 — Migrate executors (Block C, day 3)

For each of 5 executors:
1. Update Dockerfile to `pip install /app/cloudmorph-common-py` (local install).
2. Replace `from controlcenter_client import ControlCenterClient` with `from cloudmorph_common.client import ControlCenterClient`.
3. Replace `from storage_pointers import build_pointer` with `from cloudmorph_common.storage_pointers import build_pointer`.
4. Refactor `main.py` to use `BaseExecutor` (~400 LoC → ~30 LoC).
5. Move `_upload_artifacts` logic into the cloud-appropriate `ArtifactWriter` subclass.
6. Delete the now-orphaned `controlcenter_client.py` and `storage_pointers.py`.
7. Run existing tests; fix what breaks.

### Phase 3 — Common-ts (Block C, day 3 in parallel)

1. Create `cloudmorph-common-ts/` directory.
2. Generate types from `contracts/*.schema.json`.
3. Mirror `audit/`, `action-verbs.ts`, `canonical-json.ts`.
4. The MCP server imports from `cloudmorph-common-ts` instead of having its own embedded types.

### Phase 4 — Verify dedup (Block C end)

```bash
# Should print zero
find . -name 'controlcenter_client.py' -not -path './cloudmorph-common-py/*'
find . -name 'storage_pointers.py' -not -path './cloudmorph-common-py/*' -not -path './gcp/*'   # gcp keeps its add-on helper file
wc -l aws/executor/src/main.py    # should be ~30, was 456
```

---

## 1.5 Per-tenant config (Pydantic Settings)

Today: each executor reads env vars ad hoc (`os.getenv("CONTROL_CENTER_API_URL")`, etc.). Replace with typed settings:

```python
# cloudmorph_common/settings.py
from pydantic_settings import BaseSettings

class ExecutorSettings(BaseSettings):
    control_center_api_url: str
    control_center_executor_token: str
    control_center_tenant_id: str
    control_center_account_id: str
    control_center_capabilities: str = "agent.run"
    
    executor_id: Optional[str] = None
    heartbeat_seconds: float = 20.0
    poll_base_seconds: float = 2.0
    poll_max_seconds: float = 15.0
    
    one_shot_job_id: Optional[str] = None
    one_shot_job_token: Optional[str] = None
    
    storage_provider: str = "aws"   # aws | azure | gcp | local
    storage_bucket: Optional[str] = None
    storage_region: Optional[str] = None
    storage_prefix: str = ""
    artifact_base_prefix: Optional[str] = None
    
    audit_sink: str = "stdout"      # stdout | s3 | s3-customer-owned | none
    audit_buffer_path: str = "/var/lib/cloudmorph/audit-buffer"
    audit_retention_days: int = 30
    
    fail_mode: str = "closed"        # closed | open
    
    class Config:
        env_file = ".env"
        env_prefix = ""

# Usage in main.py
settings = ExecutorSettings()
```

Override with TOML for self-hosted: `cloudmorph load-config /etc/cloudmorph/executor.toml` (post-MVP).

Per-tenant overrides via JWT claims (post-MVP).

---

## 1.6 Secrets management

Today: every executor expects raw tokens in env (`CONTROL_CENTER_EXECUTOR_TOKEN`, `DATABRICKS_TOKEN`, `SNOWFLAKE_PASSWORD`). Plain text; bad for compliance buyers.

Add a secrets resolver layer:

```python
# cloudmorph_common/secrets.py
class SecretResolver(ABC):
    @abstractmethod
    def get(self, key: str) -> str: ...

class EnvSecretResolver(SecretResolver):
    def get(self, key): return os.environ[key]

class AwsSecretsManagerResolver(SecretResolver):
    def __init__(self, region): self._client = boto3.client("secretsmanager", region_name=region)
    def get(self, key):
        return self._client.get_secret_value(SecretId=key)["SecretString"]

class GcpSecretManagerResolver(SecretResolver): ...
class AzureKeyVaultResolver(SecretResolver): ...

def get_resolver() -> SecretResolver:
    backend = os.getenv("CLOUDMORPH_SECRETS_BACKEND", "env")
    if backend == "env": return EnvSecretResolver()
    if backend == "aws-secrets-manager": return AwsSecretsManagerResolver(os.environ["AWS_REGION"])
    ...
```

Settings then resolve via `resolver.get("control_center_executor_token")` instead of raw env.

**Effort:** 6h. Block C.

---

## 1.7 Why a single common-py and not one per executor

Alternatives considered:

- **One `cloudmorph-aws-executor` package etc.** Rejected — duplicates the duplication problem; everyone re-implements ControlCenterClient.
- **Just use git submodules.** Rejected — submodules are a usability disaster compared to pip-installable packages.
- **Bring the executors into one mono-package `cloudmorph-executors`.** Considered. Reasonable. **Decision: keep them as 5 directories under the umbrella repo, but factor common.** Mono-package would be cleaner but the "5 separate Dockerfiles" deployment story needs 5 entry points either way.

---

## 1.8 Why a separate common-ts package

The MCP server needs the same contracts and audit shapes as the executors. Options:

- **Manually keep TS types in sync with Python types.** Rejected — the duplication we're trying to fix.
- **Generate both from contracts/*.schema.json with separate per-language packages.** ✓
- **Use protobuf for both languages.** Heavier; not necessary at MVP scale.

The codegen pipeline (contracts/02 §2.5) writes into both `cloudmorph-common-py/.../contracts/` and `cloudmorph-common-ts/src/contracts/`. CI gate verifies clean diff.

---

## 1.9 Severity table

| Item | Severity | Effort |
|---|---|---:|
| Create `cloudmorph-common-py/` package | P0 | 4h |
| Extract `ControlCenterClient` | P0 | 2h |
| Extract `storage_pointers.build_pointer` | P0 | 1h |
| Author `BaseExecutor` (lifecycle) | P0 | 8h |
| Author `AuditEmitter` (chain + sinks) | P0 | 12h |
| Author `ArtifactWriter` (S3/GCS/Blob) | P0 | 6h |
| Author `Settings` (Pydantic) per executor | P0 | 4h |
| Author `SecretResolver` layer | P1 | 6h |
| Generate Pydantic models from schemas | P0 | 4h |
| Migrate 5 executors to common-py | P0 | 10h |
| Verify dedup (post-migration grep) | P0 | 1h |
| Tests for common-py (≥ 80% coverage) | P0 | 14h |
| Create `cloudmorph-common-ts/` mirror | P0 | 6h |
| Generate TS interfaces from schemas | P0 | 3h |
| Migrate MCP server to common-ts | P0 | 4h |
| Tests for common-ts | P1 | 8h |

**Total: ~93h.** Block C is 2-3 days; this is the largest single block of refactoring outside the MCP server itself. **Critical path** — everything downstream (MCP server, executors with governance hooks, SDK) depends on the contracts being in their generated form.

---

## 1.10 Out of scope

- Publishing `cloudmorph-common` to PyPI. MVP keeps it local-pip-install. PyPI publish post-MVP after API stabilizes.
- Versioning common-py independently of the umbrella repo. MVP locks to the same commit.
- Replacing `setuptools` with `hatch` or `pdm`. setuptools works.

---

## 1.11 Source links

- [aws/executor/src/controlcenter_client.py](../../aws/executor/src/controlcenter_client.py) — canonical extraction source
- [aws/executor/src/main.py](../../aws/executor/src/main.py) — canonical lifecycle source
- [aws/executor/src/storage_pointers.py](../../aws/executor/src/storage_pointers.py) — canonical pointer source
- [contracts/](../../contracts/) — codegen input
- [BUILD_PLAN.md Block C](../BUILD_PLAN.md) for sequencing
