# @cloudmorph/common

Shared TypeScript lib for the CloudMorph MCP server and future TS SDK.

Mirrors `cloudmorph-common-py` so both languages compute identical hashes
over the same canonical JSON, share the same action → verb mapping, and
import the same generated contract types.

## Modules

| Module | Purpose |
|---|---|
| `@cloudmorph/common/audit` | `HashChain`, `canonicalJson`, `AuditEmitter`, sinks |
| `@cloudmorph/common/contracts` | Generated TS interfaces from `contracts/*.schema.json` |
| `@cloudmorph/common/action-verbs` | `ACTION_VERBS` map; `verbsFor(action)` |

## Install

```bash
npm install @cloudmorph/common
```

## Status

Pre-MVP. API stability not guaranteed until v1.0.

## Architecture

See [`status/cross/07_common_layer_audit.md`](../status/cross/07_common_layer_audit.md) and [`status/ARCHITECTURE.md §11`](../status/ARCHITECTURE.md).
