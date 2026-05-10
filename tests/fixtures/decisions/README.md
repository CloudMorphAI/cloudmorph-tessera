# Decision fixtures

`(input, expected_decision)` pairs that lock the v0.1 policy engine behavior.

Each fixture is a JSON file with shape `{name, description, input, expected}`.

## Input shape

```json
{
  "tool_call": {"name": "<tool_name>", "arguments": {}},
  "runtime": {"lockdown": false},
  "intent": null
}
```

- `tool_call.name` — the MCP tool name (replaces old `toolCall.action`)
- `runtime.lockdown` — kill switch; true blocks all traffic before policy evaluation (replaces old `tenantSettings.locked`)
- `intent` — optional intent dict with `verbs` list (replaces old `intent.structuredVerbs`)

## Expected shape

```json
{
  "outcome": "allow | block"
}
```

Note: `deny` (old format) is now `block`. `intentMatchScore` is dropped — intent
mismatch is now evaluated by policy conditions (`intent_class_in`), not automatically.

## Coverage

- 01: allow read tool (no lockdown, no policy block)
- 02: block unknown tool (no matching policy → default block)
- 03: block destructive tool (explicit block policy)
- 04: allow with lockdown=false (passthrough)
- 05: allow with intent present (intent-aware, lockdown=false, allow policy)
- 06: block with lockdown=true (kill switch overrides all policies)

## Adding a fixture

1. Pick the next sequence number (`07_`, `08_`, ...).
2. Create `<seq>_<name>.json` with the standard shape above.
3. `test_policy_decisions.py` picks it up automatically via parametrize.
