# opa test for the readonly demo bundle.
# Run: opa test cloudmorph-mcp/test-fixtures/bundles/readonly/rules/

package cm.decision_test

import data.cm.decision

# ── Allow path ────────────────────────────────────────────────

test_allow_aws_s3_list_buckets if {
    decision.outcome == "allow" with input as {
        "toolCall": {"action": "aws.s3.list_buckets", "arguments": {}},
        "tenantSettings": {"locked": false},
    }
}

test_allow_databricks_list_clusters if {
    decision.outcome == "allow" with input as {
        "toolCall": {"action": "databricks.workspace.list_clusters", "arguments": {}},
        "tenantSettings": {"locked": false},
    }
}

test_allow_snowflake_list_databases if {
    decision.outcome == "allow" with input as {
        "toolCall": {"action": "snowflake.account.list_databases", "arguments": {}},
        "tenantSettings": {"locked": false},
    }
}

# ── Deny path ─────────────────────────────────────────────────

test_deny_unknown_action if {
    result := decision with input as {
        "toolCall": {"action": "unknown.action", "arguments": {}},
        "tenantSettings": {"locked": false},
    }
    result.outcome == "deny"
    result.reason == "no_matching_rule"
}

test_deny_destructive_aws_s3_delete_bucket if {
    result := decision with input as {
        "toolCall": {"action": "aws.s3.delete_bucket", "arguments": {"bucket": "test"}},
        "tenantSettings": {"locked": false},
    }
    result.outcome == "deny"
    result.reason == "destructive_action_denied"
}

test_deny_aws_iam_delete_user if {
    result := decision with input as {
        "toolCall": {"action": "aws.iam.delete_user", "arguments": {"userName": "test"}},
        "tenantSettings": {"locked": false},
    }
    result.outcome == "deny"
}

# ── Tenant lockdown ───────────────────────────────────────────

test_deny_tenant_locked_overrides_allow if {
    result := decision with input as {
        "toolCall": {"action": "aws.s3.list_buckets", "arguments": {}},
        "tenantSettings": {"locked": true},
    }
    result.outcome == "deny"
    result.reason == "tenant_locked"
}

# ── Intent mismatch ───────────────────────────────────────────

test_deny_intent_mismatch if {
    result := decision with input as {
        "toolCall": {"action": "aws.s3.list_buckets", "arguments": {}},
        "tenantSettings": {"locked": false},
        "intent": {"structuredVerbs": ["write.delete"]},
        "intentMatchScore": {"verdict": "mismatch"},
    }
    result.outcome == "deny"
    result.reason == "intent_mismatch"
}

test_allow_intent_match if {
    result := decision with input as {
        "toolCall": {"action": "aws.s3.list_buckets", "arguments": {}},
        "tenantSettings": {"locked": false},
        "intent": {"structuredVerbs": ["read.list"]},
        "intentMatchScore": {"verdict": "match"},
    }
    result.outcome == "allow"
}

# ── Matched rules emission ────────────────────────────────────

test_allow_emits_matched_rule if {
    result := decision with input as {
        "toolCall": {"action": "aws.s3.list_buckets", "arguments": {}},
        "tenantSettings": {"locked": false},
    }
    count(result.matchedRules) == 1
    result.matchedRules[0].ruleId == "allow_read_first"
}
