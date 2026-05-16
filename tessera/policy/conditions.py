"""Condition evaluators — one function per condition type.

Dispatch is done via a module-level _DISPATCH dict (PF-2 refactor) populated at
import time. evaluate_condition() looks up type(cond) and delegates; unknown types
return False (fail-closed).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import regex

# Optional dep: boto3 (in [aws] extras). Module-level reference enables
# `unittest.mock.patch("tessera.policy.conditions.boto3", ...)` in tests AND
# avoids per-call import overhead in the hot path for data_volume evaluator.
try:
    import boto3
except ImportError:
    boto3 = None

# Optional dep: cachetools (for the cross-request DataVolume LRU below). Falls
# back to an unbounded dict when missing — fine for tests; production should
# install cachetools.
try:
    from cachetools import TTLCache
except ImportError:
    class TTLCache(dict):  # type: ignore[no-redef, type-arg]
        """Trivial fallback when cachetools is missing — unbounded dict, no TTL."""

        def __init__(self, maxsize: int = 1000, ttl: float = 300.0) -> None:
            super().__init__()

from tessera.policy.action_verbs import verbs_for
from tessera.policy.schema import (
    ActionClassIn,
    AffectedResourceCount,
    AnyOf,
    ArgContainsPattern,
    ArgEquals,
    ArgGreaterThan,
    ArgInSet,
    ArgLessThan,
    ArgMatchesRegex,
    ArgSizeGreaterThan,
    BlastRadius,
    ConditionType,
    CumulativeSpendToday,
    DataVolume,
    IntentClassIn,
    IntentPurposeMatches,
    MetaFieldEquals,
    NoneOf,
    PredictedCost,
    RegionIn,
    TimeOfDayOutside,
    ToolNameIn,
)

# ── Cross-request DataVolume cache (P0-15) ────────────────────────────────────
# Module-level TTL cache shared across requests. Two concurrent requests against
# the same S3 (bucket, key) or RDS (cluster, statement-hash) tuple should hit
# this cache rather than each spawn its own HeadObject / EXPLAIN.
_DATA_VOL_LRU = TTLCache(maxsize=1000, ttl=300.0)
_DATA_VOL_LRU_LOCK = threading.Lock()

# Patchable in tests: replace with a lambda returning a fixed datetime
_now_fn = datetime.now

_REGEX_TIMEOUT = 0.1  # 100ms

# Thread-local storage for decision errors (regex timeout side-channel)
_decision_ctx = threading.local()

# Band confidence multipliers for predicted_cost
_BAND_MULTIPLIER = {
    "high": 1.0,
    "medium": 1.5,
    "ceiling": 3.0,
}


def get_decision_errors() -> list[str]:
    """Return accumulated decision errors from the current thread."""
    return getattr(_decision_ctx, "errors", [])


def clear_decision_errors() -> None:
    """Clear decision errors for current thread."""
    _decision_ctx.errors = []


def _add_error(error: str) -> None:
    if not hasattr(_decision_ctx, "errors"):
        _decision_ctx.errors = []
    _decision_ctx.errors.append(error)


def _match_regex(
    pattern: str,
    text: str,
    policy_id: str | None = None,
    compiled: Any = None,
) -> bool:
    """Match regex with 100ms timeout. On timeout: return False and tag error.

    When *compiled* is provided (a pre-compiled regex.Pattern from the loader),
    it is used directly to avoid re-compiling on every request evaluation.
    Falls back to runtime compile when *compiled* is None (tests / direct callers).
    """
    try:
        pat = compiled if compiled is not None else regex.compile(pattern, regex.VERSION1)
        return pat.search(text, timeout=_REGEX_TIMEOUT) is not None
    except TimeoutError:
        _add_error(f"regex_timeout:{policy_id or 'unknown'}")
        return False
    except Exception as e:
        # regex lib sometimes raises generic Exception with "timeout" in the message
        # rather than a TimeoutError subclass; treat those as timeouts too.
        if "timeout" in str(e).lower():
            _add_error(f"regex_timeout:{policy_id or 'unknown'}")
        return False


def _get_arg(arguments: dict[str, Any], arg: str) -> tuple[bool, Any]:
    """Retrieve arg value from arguments dict. Returns (found, value)."""
    if arg not in arguments:
        return False, None
    return True, arguments[arg]


def _dot_path_get(obj: Any, path: str) -> tuple[bool, Any]:
    """Walk a dot-separated path in a nested dict. Returns (found, value)."""
    parts = path.split(".")
    current = obj
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


# ── Individual evaluators ─────────────────────────────────────────────────────


def _evaluate_arg_equals(cond: ArgEquals, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    if cond.arg == "*":
        return any(v == cond.value for v in arguments.values())
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    return bool(val == cond.value)


def _evaluate_arg_greater_than(cond: ArgGreaterThan, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    if cond.arg == "*":
        for v in arguments.values():
            try:
                if float(v) > float(cond.value):
                    return True
            except (TypeError, ValueError):
                pass
        return False
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    try:
        return float(val) > float(cond.value)
    except (TypeError, ValueError):
        return False


def _evaluate_arg_less_than(cond: ArgLessThan, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    if cond.arg == "*":
        for v in arguments.values():
            try:
                if float(v) < float(cond.value):
                    return True
            except (TypeError, ValueError):
                pass
        return False
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    try:
        return float(val) < float(cond.value)
    except (TypeError, ValueError):
        return False


def _evaluate_arg_matches_regex(cond: ArgMatchesRegex, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    policy_id = context.get("policy_id")
    pre = getattr(cond, "compiled_regex", None)
    if cond.arg == "*":
        return any(_match_regex(cond.pattern, str(v), policy_id, compiled=pre) for v in arguments.values())
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    return _match_regex(cond.pattern, str(val), policy_id, compiled=pre)


def _evaluate_arg_in_set(cond: ArgInSet, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    if cond.arg == "*":
        return any(v in cond.values for v in arguments.values())
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    return val in cond.values


def _evaluate_arg_contains_pattern(cond: ArgContainsPattern, context: dict[str, Any]) -> bool:
    # Alias of arg_matches_regex
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    policy_id = context.get("policy_id")
    pre = getattr(cond, "compiled_regex", None)
    if cond.arg == "*":
        return any(_match_regex(cond.pattern, str(v), policy_id, compiled=pre) for v in arguments.values())
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    return _match_regex(cond.pattern, str(val), policy_id, compiled=pre)


def _evaluate_arg_size_greater_than(cond: ArgSizeGreaterThan, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    if cond.arg == "*":
        return any(len(json.dumps(v)) > cond.bytes for v in arguments.values())
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    return len(json.dumps(val)) > cond.bytes


def _evaluate_tool_name_in(cond: ToolNameIn, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    tool_name = tool_call.get("name", "")
    if tool_name in cond.values:
        return True
    # When call_aws was reverse-resolved by the engine, also check the
    # canonical name so policies authored against aws_*_* still fire.
    effective = context.get("_effective_tool_name")
    return effective is not None and effective != tool_name and effective in cond.values


def _evaluate_action_class_in(cond: ActionClassIn, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    tool_name = tool_call.get("name", "")
    tool_verbs = verbs_for(tool_name)
    return bool(tool_verbs & set(cond.values))


def _evaluate_intent_class_in(cond: IntentClassIn, context: dict[str, Any]) -> bool:
    intent = context.get("intent")
    if intent is None:
        return False
    intent_verbs = set(intent.get("verbs", []))
    return bool(intent_verbs & set(cond.values))


def _evaluate_intent_purpose_matches(cond: IntentPurposeMatches, context: dict[str, Any]) -> bool:
    intent = context.get("intent")
    policy_id = context.get("policy_id")
    if intent is None:
        return False
    purpose = intent.get("purpose")
    if purpose is None:
        return False
    pre = getattr(cond, "compiled_regex", None)
    return _match_regex(cond.pattern, str(purpose), policy_id, compiled=pre)


def _evaluate_region_in(cond: RegionIn, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    if cond.arg == "*":
        return any(any(str(v).startswith(r) for r in cond.regions) for v in arguments.values())
    found, val = _get_arg(arguments, cond.arg)
    if not found:
        return False
    return any(str(val).startswith(r) for r in cond.regions)


def _evaluate_time_of_day_outside(cond: TimeOfDayOutside, context: dict[str, Any]) -> bool:
    try:
        tz = ZoneInfo(cond.tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    now = _now_fn(tz).time().replace(second=0, microsecond=0)
    start_parts = cond.start.split(":")
    end_parts = cond.end.split(":")
    start = time(int(start_parts[0]), int(start_parts[1]))
    end = time(int(end_parts[0]), int(end_parts[1]))
    if start <= end:
        # Normal range: outside means before start OR after end
        return not (start <= now <= end)
    # Wraps midnight: inside means >= start OR <= end
    return not (now >= start or now <= end)


def _evaluate_meta_field_equals(cond: MetaFieldEquals, context: dict[str, Any]) -> bool:
    tool_call = context.get("tool_call", {})
    meta = tool_call.get("_meta")
    if meta is None:
        return False
    found, val = _dot_path_get(meta, cond.key)
    if not found:
        return False
    return bool(val == cond.value)


def _evaluate_any_of(cond: AnyOf, context: dict[str, Any]) -> bool:
    return any(evaluate_condition(c, context) for c in cond.conditions)


def _evaluate_none_of(cond: NoneOf, context: dict[str, Any]) -> bool:
    return not any(evaluate_condition(c, context) for c in cond.conditions)


# ── v0.2.0 semantic condition evaluators ─────────────────────────────────────


def _evaluate_predicted_cost(cond: PredictedCost, context: dict[str, Any]) -> bool:
    """Evaluate predicted_cost condition.

    Reads pre-fetched CostResult from context["cost_cache"] keyed on tool_name.
    The proxy pre-populates this cache before invoking the engine so the
    synchronous condition evaluator never has to bridge into async I/O.

    Fail-closed (False = don't block) when:
    - cost_cache is missing from context (callers must pass it explicitly)
    - no entry for tool_name
    - source == "miss" (no price data available — uncertainty must not block)
    - price_usd is None
    """
    assert "cost_cache" in context, (
        "_evaluate_predicted_cost: context['cost_cache'] is required but missing. "
        "Pass cost_cache={} in test contexts or ensure the proxy populates it before evaluation."
    )

    tool_call = context.get("tool_call", {})
    tool_name: str = tool_call.get("name", "")

    from tessera.cost.types import CostResult  # noqa: PLC0415

    cost_cache: dict[str, CostResult] = context.get("cost_cache") or {}
    cost_result: CostResult | None = cost_cache.get(tool_name)

    if cost_result is None or cost_result.source == "miss" or cost_result.price_usd is None:
        return False

    raw_usd = cost_result.price_usd

    # Apply band multiplier (ceiling = highest uncertainty)
    multiplier = _BAND_MULTIPLIER.get(cond.band, 1.0)
    adjusted_usd = raw_usd * multiplier

    if cond.operator == "greater_than":
        return adjusted_usd > cond.usd_threshold
    if cond.operator == "less_than":
        return adjusted_usd < cond.usd_threshold
    if cond.operator == "between" and cond.usd_threshold_upper is not None:
        return cond.usd_threshold <= adjusted_usd <= cond.usd_threshold_upper
    return False


def _evaluate_blast_radius(cond: BlastRadius, context: dict[str, Any]) -> bool:
    """Evaluate blast_radius condition.

    Fail-closed (True = block) when blast_radius_backend is absent or raises —
    uncertainty defaults to block for blast-radius.
    Returns False when tool_name is not in cond.resource_types (policy doesn't apply).
    """
    tool_call = context.get("tool_call", {})
    tool_name: str = tool_call.get("name", "")
    args: dict[str, Any] = tool_call.get("arguments", {})

    # If resource_types is non-empty, check that this tool is relevant
    if cond.resource_types and tool_name not in cond.resource_types:
        return False

    # Test/fixture hook: blast_radius_cache[tool_name] = pre-computed principal count.
    # Lets OSS users and fixture tests exercise the condition without a live boto3 backend.
    blast_radius_cache: dict[str, int] = context.get("blast_radius_cache") or {}
    if tool_name in blast_radius_cache:
        count = blast_radius_cache[tool_name]
    else:
        blast_radius_backend = context.get("blast_radius_backend")
        if blast_radius_backend is None:
            return True  # fail-closed: block on uncertainty

        try:
            count = blast_radius_backend.compute(tool_name, args)
        except Exception:  # noqa: BLE001
            return True  # fail-closed

    if cond.operator == "greater_than":
        return count > cond.principal_count_threshold
    # less_than
    return count < cond.principal_count_threshold


def _evaluate_affected_resource_count(cond: AffectedResourceCount, context: dict[str, Any]) -> bool:
    """Evaluate affected_resource_count condition using jmespath on tool args."""
    import jmespath

    tool_call = context.get("tool_call", {})
    args: dict[str, Any] = tool_call.get("arguments", {})

    try:
        result = jmespath.search(cond.arg, args)
    except Exception:  # noqa: BLE001
        return False

    if result is None:
        items: list[Any] = []
    elif isinstance(result, list):
        items = result
    else:
        # Scalar — wrap so len() makes sense
        items = [result]

    count = len(items)
    if cond.operator == "greater_than":
        return count > cond.count_threshold
    # less_than
    return count < cond.count_threshold


def _evaluate_data_volume(cond: DataVolume, context: dict[str, Any]) -> bool:
    """Evaluate data_volume condition.

    Estimators:
      static_arg_size     — len(json.dumps(args).encode("utf-8"))
      s3_get_byte_estimate — boto3 s3.head_object() ContentLength
      rds_query_result_estimate — EXPLAIN attempt (best-effort, falls back to static)
    """
    tool_call = context.get("tool_call", {})
    args: dict[str, Any] = tool_call.get("arguments", {})

    if cond.estimator == "s3_get_byte_estimate":
        byte_count = _estimate_s3_object_size(args, context)
    elif cond.estimator == "rds_query_result_estimate":
        byte_count = _estimate_rds_query_size(args, context)
    else:
        # static_arg_size
        byte_count = len(json.dumps(args).encode("utf-8"))

    if cond.operator == "greater_than":
        return byte_count > cond.bytes_threshold
    return byte_count < cond.bytes_threshold


def _s3_head_cache_key(args: dict[str, Any]) -> tuple[str, str]:
    """Return (bucket, key) for the S3 args, or ("", "") if missing."""
    bucket = str(args.get("Bucket", args.get("bucket", "")) or "")
    key = str(args.get("Key", args.get("key", "")) or "")
    return bucket, key


def _rds_explain_cache_key(args: dict[str, Any]) -> tuple[str, str, str]:
    """Return (cluster_arn, secret_arn, stmt_hash) for the RDS args."""
    statement = str(args.get("Statement", args.get("statement", "")) or "")
    cluster_arn = str(args.get("resourceArn", args.get("ResourceArn", "")) or "")
    secret_arn = str(args.get("secretArn", args.get("SecretArn", "")) or "")
    if not statement or not cluster_arn or not secret_arn:
        return "", "", ""
    stmt_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:16]
    return cluster_arn, secret_arn, stmt_hash


def s3_head_size_sync(args: dict[str, Any]) -> tuple[str, int | None]:
    """Synchronous body of S3 head_object lookup (P0-15).

    Returns (cache_key, size_or_None). Designed to be wrapped in
    `asyncio.to_thread` from the proxy hot path. Populates the cross-request
    `_DATA_VOL_LRU`. Returns None on missing args or boto3 failure so the
    caller can fall back to the static estimator.
    """
    bucket, key = _s3_head_cache_key(args)
    if not bucket or not key:
        return "", None
    cache_key = f"s3_head:{bucket}/{key}"
    with _DATA_VOL_LRU_LOCK:
        cached = _DATA_VOL_LRU.get(cache_key)
    if cached is not None:
        return cache_key, int(cached)
    if boto3 is None:
        return cache_key, None
    try:
        s3 = boto3.client("s3")
        head = s3.head_object(Bucket=bucket, Key=key)
        size = int(head.get("ContentLength", 0))
    except Exception:  # noqa: BLE001
        return cache_key, None
    with _DATA_VOL_LRU_LOCK:
        _DATA_VOL_LRU[cache_key] = size
    return cache_key, size


def rds_explain_size_sync(args: dict[str, Any]) -> tuple[str, int | None]:
    """Synchronous body of RDS Data-API EXPLAIN lookup (P0-15)."""
    cluster_arn, secret_arn, stmt_hash = _rds_explain_cache_key(args)
    if not cluster_arn or not secret_arn or not stmt_hash:
        return "", None
    cache_key = f"rds_explain:{cluster_arn}:{stmt_hash}"
    with _DATA_VOL_LRU_LOCK:
        cached = _DATA_VOL_LRU.get(cache_key)
    if cached is not None:
        return cache_key, int(cached)
    if boto3 is None:
        return cache_key, None
    statement = str(args.get("Statement", args.get("statement", "")) or "")
    database = str(args.get("database", args.get("Database", "")) or "")
    try:
        rds_data = boto3.client("rds-data")
        explain_stmt = f"EXPLAIN {statement}"
        resp = rds_data.execute_statement(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            database=database,
            sql=explain_stmt,
        )
        rows = resp.get("records", [])
        total_bytes = sum(
            len(str(field.get("stringValue", "")))
            for row in rows
            for field in row
        )
    except Exception:  # noqa: BLE001
        return cache_key, None
    size = total_bytes if total_bytes > 0 else None
    if size is not None:
        with _DATA_VOL_LRU_LOCK:
            _DATA_VOL_LRU[cache_key] = size
    return cache_key, size


def _estimate_s3_object_size(args: dict[str, Any], context: dict[str, Any]) -> int:
    """Use boto3 s3.head_object() to get the ContentLength of the target object.

    Consults the per-request cache (populated by the proxy P0-15 prefetch),
    then the cross-request _DATA_VOL_LRU, before falling back to a live
    boto3 call. The boto3 fallback only fires when prefetch is bypassed (e.g.,
    in unit tests that drive the evaluator directly).
    """
    bucket, key = _s3_head_cache_key(args)
    if not bucket or not key:
        return len(json.dumps(args).encode("utf-8"))

    cache_key = f"s3_head:{bucket}/{key}"
    size_cache: dict[str, int] = context.setdefault("_data_vol_cache", {})
    if cache_key in size_cache:
        return size_cache[cache_key]
    with _DATA_VOL_LRU_LOCK:
        cached = _DATA_VOL_LRU.get(cache_key)
    if cached is not None:
        size_cache[cache_key] = int(cached)
        return int(cached)

    if boto3 is None:
        return len(json.dumps(args).encode("utf-8"))
    try:
        s3 = boto3.client("s3")
        head = s3.head_object(Bucket=bucket, Key=key)
        size = int(head.get("ContentLength", 0))
    except Exception:  # noqa: BLE001
        size = len(json.dumps(args).encode("utf-8"))

    size_cache[cache_key] = size
    with _DATA_VOL_LRU_LOCK:
        _DATA_VOL_LRU[cache_key] = size
    return size


def _estimate_rds_query_size(args: dict[str, Any], context: dict[str, Any]) -> int:
    """Attempt an RDS EXPLAIN to estimate result row bytes. Falls back to static.

    Consults the per-request cache (proxy prefetch) and the cross-request LRU
    before falling back to a live boto3 call.
    """
    cluster_arn, secret_arn, stmt_hash = _rds_explain_cache_key(args)
    if not cluster_arn or not secret_arn or not stmt_hash:
        return len(json.dumps(args).encode("utf-8"))

    cache_key = f"rds_explain:{cluster_arn}:{stmt_hash}"
    size_cache: dict[str, int] = context.setdefault("_data_vol_cache", {})
    if cache_key in size_cache:
        return size_cache[cache_key]
    with _DATA_VOL_LRU_LOCK:
        cached = _DATA_VOL_LRU.get(cache_key)
    if cached is not None:
        size_cache[cache_key] = int(cached)
        return int(cached)

    if boto3 is None:
        return len(json.dumps(args).encode("utf-8"))
    statement = str(args.get("Statement", args.get("statement", "")) or "")
    database = str(args.get("database", args.get("Database", "")) or "")
    try:
        rds_data = boto3.client("rds-data")
        explain_stmt = f"EXPLAIN {statement}"
        resp = rds_data.execute_statement(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            database=database,
            sql=explain_stmt,
        )
        # Rough estimate: sum of row field lengths from EXPLAIN output
        rows = resp.get("records", [])
        total_bytes = sum(
            len(str(field.get("stringValue", "")))
            for row in rows
            for field in row
        )
        size = total_bytes if total_bytes > 0 else len(json.dumps(args).encode("utf-8"))
    except Exception:  # noqa: BLE001
        return len(json.dumps(args).encode("utf-8"))

    size_cache[cache_key] = size
    with _DATA_VOL_LRU_LOCK:
        _DATA_VOL_LRU[cache_key] = size
    return size


def _evaluate_cumulative_spend_today(cond: CumulativeSpendToday, context: dict[str, Any]) -> bool:
    """Evaluate cumulative_spend_today condition against DailySpendState.

    Fail-closed (False = don't block) when state_backend is missing.
    scope is taken from context["scope"] (set by proxy.py from auth_ctx.scope).
    """
    # Test/fixture hook: cumulative_spend_today_usd in context for direct injection.
    # Lets OSS users and fixture tests exercise the condition without a live state backend.
    cached_usd = context.get("cumulative_spend_today_usd")
    if cached_usd is not None:
        today_usd = float(cached_usd)
    else:
        state_backend = context.get("state_backend")
        if state_backend is None:
            return False  # fail-closed

        scope: str = context.get("scope", "default")
        try:
            today_usd = state_backend.get_today_spend(scope)
        except Exception:  # noqa: BLE001
            return False

    if cond.operator == "greater_than":
        return today_usd > cond.usd_threshold
    return today_usd < cond.usd_threshold


# ── Dispatch table (PF-2 refactor) ───────────────────────────────────────────

_DISPATCH: dict[type, Callable[..., bool]] = {
    ArgEquals: _evaluate_arg_equals,
    ArgGreaterThan: _evaluate_arg_greater_than,
    ArgLessThan: _evaluate_arg_less_than,
    ArgMatchesRegex: _evaluate_arg_matches_regex,
    ArgInSet: _evaluate_arg_in_set,
    ArgContainsPattern: _evaluate_arg_contains_pattern,
    ArgSizeGreaterThan: _evaluate_arg_size_greater_than,
    ToolNameIn: _evaluate_tool_name_in,
    ActionClassIn: _evaluate_action_class_in,
    IntentClassIn: _evaluate_intent_class_in,
    IntentPurposeMatches: _evaluate_intent_purpose_matches,
    RegionIn: _evaluate_region_in,
    TimeOfDayOutside: _evaluate_time_of_day_outside,
    MetaFieldEquals: _evaluate_meta_field_equals,
    AnyOf: _evaluate_any_of,
    NoneOf: _evaluate_none_of,
    PredictedCost: _evaluate_predicted_cost,
    BlastRadius: _evaluate_blast_radius,
    AffectedResourceCount: _evaluate_affected_resource_count,
    DataVolume: _evaluate_data_volume,
    CumulativeSpendToday: _evaluate_cumulative_spend_today,
}


def evaluate_condition(cond: ConditionType, context: dict[str, Any]) -> bool:
    """Evaluate a single condition against the request context.

    context shape:
    {
        "tool_call": {"name": str, "arguments": dict, "_meta": dict | None},
        "intent": dict | None,  # extracted intent or None
        "upstream": str,
        "runtime": {"lockdown": bool},
        "policy_id": str | None,  # for error tagging
        # v0.2.0 optional backends:
        "cost_backend": InfracostClient | None,
        "cost_cache": dict[str, float] | None,
        "aws_mapping": module | None,
        "blast_radius_backend": BlastRadiusBackend | None,
        "state_backend": DailySpendState | None,
        "scope": str,  # auth scope for cumulative_spend
    }

    Returns True/False. Missing args fail-closed (return False).
    arg="*" iterates all top-level argument values.
    """
    fn = _DISPATCH.get(type(cond))
    if fn is None:
        return False  # unknown type, fail-closed
    return fn(cond, context)


def evaluate_conditions(conds: list[ConditionType], context: dict[str, Any]) -> bool:
    """Evaluate all conditions (AND'd). Short-circuit on first False."""
    return all(evaluate_condition(c, context) for c in conds)
