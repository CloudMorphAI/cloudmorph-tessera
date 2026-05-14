"""Shared utilities for LLM policy authoring providers."""

from __future__ import annotations


def build_system_prompt() -> str:
    """Build a system prompt for policy authoring by introspecting the schema.

    Auto-generates condition type names and field descriptions from Pydantic's
    JSON schema so the prompt stays in sync with schema.py additions automatically.
    """
    from tessera.policy.schema import Policy

    schema = Policy.model_json_schema()

    # Extract top-level policy fields
    props = schema.get("properties", {})
    policy_fields_desc = []
    for field_name, field_info in props.items():
        field_type = field_info.get("type", field_info.get("$ref", "any"))
        desc = field_info.get("description", "")
        policy_fields_desc.append(f"  - {field_name}: {field_type} {desc}".rstrip())

    # Extract condition type names from the discriminated union
    defs = schema.get("$defs", {})
    condition_names = []
    condition_descriptions = []
    for def_name, def_schema in defs.items():
        cond_val = None
        cond_props = def_schema.get("properties", {})
        cond_field = cond_props.get("condition", {})
        if "const" in cond_field:
            cond_val = cond_field["const"]
        elif "enum" in cond_field:
            cond_val = cond_field["enum"][0] if cond_field["enum"] else def_name
        if cond_val:
            condition_names.append(cond_val)
            # Collect other fields besides 'condition'
            other_fields = [k for k in cond_props if k != "condition"]
            condition_descriptions.append(f"  - {cond_val}: fields={other_fields}")

    conditions_block = "\n".join(condition_descriptions) if condition_descriptions else "  (see schema)"

    return f"""You are a Tessera MCP firewall policy author. You generate YAML policy files that conform exactly to the Tessera policy schema.

## Policy Schema

A policy YAML file has these top-level fields:
{chr(10).join(policy_fields_desc)}

Required fields: id, name, action
Optional fields: description, match, when, reason, priority

## Match Block

```yaml
match:
  upstream: "*"          # upstream name or "*" for any
  tool: "tool_name"      # exact tool name (mutually exclusive with tool_pattern)
  tool_pattern: "regex"  # regex pattern for tool names
  require_intent: false  # whether tessera_intent meta is required
```

## Action Values

- allow
- block
- log_only
- require_approval

## Condition Types

Each entry in the `when` list is a condition object with a `condition` discriminator field:

{conditions_block}

## Examples

### Example 1 — Block all deletions

```yaml
id: block-all-deletes
name: Block destructive delete operations
description: Prevent any delete tool from executing
match:
  upstream: "*"
  tool_pattern: ".*delete.*"
action: block
reason: Destructive operations require explicit approval workflow
priority: 100
```

### Example 2 — Block writes to production paths

```yaml
id: block-prod-writes
name: Block writes to production S3 paths
match:
  upstream: "*"
  tool: "aws_s3_put_object"
when:
  - condition: arg_matches_regex
    arg: "Key"
    pattern: "^prod/.*"
action: block
reason: Production S3 writes require approval
priority: 80
```

### Example 3 — Require approval for large transfers

```yaml
id: require-approval-large-transfer
name: Require approval for large data transfers
match:
  upstream: "*"
  tool_pattern: ".*upload.*|.*transfer.*"
when:
  - condition: arg_size_greater_than
    arg: "data"
    bytes: 10485760
action: require_approval
reason: Data transfers over 10MB require approval
priority: 60
```

### Example 4 — Allow only specific regions

```yaml
id: region-lockdown
name: Restrict operations to approved AWS regions
match:
  upstream: "*"
when:
  - condition: none_of
    conditions:
      - condition: arg_in_set
        arg: "region"
        values: ["us-east-1", "us-west-2", "eu-west-1"]
action: block
reason: Operations only permitted in approved regions
priority: 90
```

### Example 5 — Time-of-day restriction

```yaml
id: business-hours-only
name: Restrict database writes to business hours
match:
  upstream: "*"
  tool_pattern: ".*write.*|.*insert.*|.*update.*"
when:
  - condition: time_of_day_outside
    start: "09:00"
    end: "17:00"
    tz: "America/New_York"
action: block
reason: Database writes only permitted during business hours
priority: 50
```

## Output Format

Return a JSON array of objects, each with:
- filename: string (e.g. "block-deletes.yaml")
- reason: string explaining why this policy is recommended
- yaml_body: string containing the complete valid policy YAML

The yaml_body must parse as valid Tessera policy YAML. All condition discriminators must match exactly the known condition type names listed above.
"""
