# Readonly demo bundle

Smoke-test policy bundle for the OPA WASM engine. Default deny + read-first allowlist + intent-mismatch detection + destructive denylist.

## Build

```bash
cd cloudmorph-mcp/test-fixtures/bundles/readonly
mkdir -p opa
opa build -t wasm -e cm/decision -o opa/policy.wasm rules/
tar czf bundle.tar.gz manifest.json rules/ opa/policy.wasm
# Sign:
HMAC=$(openssl dgst -sha256 -hmac "$BUNDLE_HMAC_KEY" bundle.tar.gz | awk '{print $2}')
echo -n "$HMAC" > bundle.sig
```

## Test

```bash
opa test rules/
# Expected: 10 tests passed
```

## Coverage

| Rule | Tests |
|---|---|
| allow_read_first | test_allow_aws_s3_list_buckets, test_allow_databricks_list_clusters, test_allow_snowflake_list_databases, test_allow_intent_match |
| default deny | test_deny_unknown_action |
| destructive denylist | test_deny_destructive_aws_s3_delete_bucket, test_deny_aws_iam_delete_user |
| tenant lockdown | test_deny_tenant_locked_overrides_allow |
| intent mismatch | test_deny_intent_mismatch |
| matchedRules emission | test_allow_emits_matched_rule |
