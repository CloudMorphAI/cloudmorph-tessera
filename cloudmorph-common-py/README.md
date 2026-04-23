# cloudmorph-common

Shared Python library for CloudMorph executors and the SDK.

Provides:
- `cloudmorph_common.client.ControlCenterClient` — the executor lifecycle protocol (claim/heartbeat/complete) — extracted from 5 byte-identical copies
- `cloudmorph_common.base_executor.BaseExecutor` — common claim/run/heartbeat/complete loop with signal handling
- `cloudmorph_common.audit.{emitter,chain,canonical_json}` — tamper-evident audit chain with pluggable sinks
- `cloudmorph_common.artifacts.{S3,Gcs,Blob}ArtifactWriter` — per-cloud artifact upload abstraction
- `cloudmorph_common.contracts.*` — generated Pydantic models from `contracts/*.schema.json`
- `cloudmorph_common.settings.ExecutorSettings` — typed env config via Pydantic
- `cloudmorph_common.action_verbs.ACTION_VERBS` — action → verb-set mapping (cross-cuts with intent system)

## Install

```bash
pip install cloudmorph-common
# or with cloud-specific extras:
pip install cloudmorph-common[aws]
pip install cloudmorph-common[all]
```

## Status

Pre-MVP. API stability not guaranteed until v1.0.

## Architecture

See [`status/cross/07_common_layer_audit.md`](../status/cross/07_common_layer_audit.md) and [`status/ARCHITECTURE.md §11`](../status/ARCHITECTURE.md).
