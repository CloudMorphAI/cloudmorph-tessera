"""Pydantic models for Tessera policy YAML files."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Alias used by new condition models — keeps them consistent with existing ones.
_ConditionBase = BaseModel


class Action(str, Enum):
    allow = "allow"
    block = "block"
    log_only = "log_only"
    require_approval = "require_approval"


class MatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstream: str = "*"
    tool: str | None = None
    tool_pattern: str | None = None
    require_intent: bool = False

    @model_validator(mode="after")
    def tool_and_tool_pattern_exclusive(self) -> MatchSpec:
        if self.tool is not None and self.tool_pattern is not None:
            raise ValueError("match.tool and match.tool_pattern are mutually exclusive")
        return self


# ── Individual condition models ──────────────────────────────────────────────


class BaseCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArgEquals(BaseCondition):
    condition: Literal["arg_equals"]
    arg: str
    value: Any


class ArgGreaterThan(BaseCondition):
    condition: Literal["arg_greater_than"]
    arg: str
    value: Any


class ArgLessThan(BaseCondition):
    condition: Literal["arg_less_than"]
    arg: str
    value: Any


class ArgMatchesRegex(BaseCondition):
    condition: Literal["arg_matches_regex"]
    arg: str
    pattern: str


class ArgInSet(BaseCondition):
    condition: Literal["arg_in_set"]
    arg: str
    values: list[Any]


class ArgContainsPattern(BaseCondition):
    condition: Literal["arg_contains_pattern"]
    arg: str
    pattern: str


class ArgSizeGreaterThan(BaseCondition):
    condition: Literal["arg_size_greater_than"]
    arg: str
    bytes: int


class ToolNameIn(BaseCondition):
    condition: Literal["tool_name_in"]
    values: list[str]


class ActionClassIn(BaseCondition):
    condition: Literal["action_class_in"]
    values: list[str]


class IntentClassIn(BaseCondition):
    condition: Literal["intent_class_in"]
    values: list[str]


class IntentPurposeMatches(BaseCondition):
    condition: Literal["intent_purpose_matches"]
    pattern: str


class RegionIn(BaseCondition):
    condition: Literal["region_in"]
    arg: str
    regions: list[str]


class TimeOfDayOutside(BaseCondition):
    condition: Literal["time_of_day_outside"]
    start: str
    end: str
    tz: str


class MetaFieldEquals(BaseCondition):
    condition: Literal["meta_field_equals"]
    key: str
    value: Any


class AnyOf(BaseCondition):
    condition: Literal["any_of"]
    conditions: list[ConditionType]


class NoneOf(BaseCondition):
    condition: Literal["none_of"]
    conditions: list[ConditionType]


# ── v0.2.0 semantic conditions ───────────────────────────────────────────────


class PredictedCost(BaseCondition):
    """Condition: estimated cost of the call exceeds (or is within) a USD threshold.

    Requires a cost_backend and aws_mapping in the eval context.
    Fail-closed on missing mapping or backend timeout (returns False = don't block).
    """

    condition: Literal["predicted_cost"] = "predicted_cost"
    usd_threshold: float
    band: Literal["high", "medium", "ceiling"] = "high"
    operator: Literal["greater_than", "less_than", "between"] = "greater_than"
    usd_threshold_upper: float | None = None  # for "between"

    @model_validator(mode="after")
    def _validate_between(self) -> PredictedCost:
        if self.operator == "between" and self.usd_threshold_upper is None:
            raise ValueError("predicted_cost with operator=between requires usd_threshold_upper")
        return self


class BlastRadius(BaseCondition):
    """Condition: number of principals affected by an IAM/S3/KMS policy change.

    Requires a blast_radius_backend in the eval context.
    Fail-closed when backend is missing or raises (returns True = block on uncertainty).
    """

    condition: Literal["blast_radius"] = "blast_radius"
    principal_count_threshold: int
    account_scope: Literal["same_account", "cross_account", "any"] = "any"
    resource_types: list[str] = Field(default_factory=list)
    operator: Literal["greater_than", "less_than"] = "greater_than"


class AffectedResourceCount(BaseCondition):
    """Condition: count of items at a JMESPath within args exceeds a threshold.

    Uses the jmespath library to navigate nested args structures.
    """

    condition: Literal["affected_resource_count"] = "affected_resource_count"
    arg: str  # JMESPath expression applied to the tool call arguments
    count_threshold: int
    operator: Literal["greater_than", "less_than"] = "greater_than"


class DataVolume(BaseCondition):
    """Condition: estimated byte volume of the operation exceeds a threshold."""

    condition: Literal["data_volume"] = "data_volume"
    bytes_threshold: int
    operator: Literal["greater_than", "less_than"] = "greater_than"
    estimator: Literal["s3_get_byte_estimate", "rds_query_result_estimate", "static_arg_size"] = (
        "static_arg_size"
    )


class CumulativeSpendToday(BaseCondition):
    """Condition: cumulative USD spend for the calling scope today exceeds a threshold.

    Requires a state_backend (DailySpendState) in the eval context.
    Fail-closed on missing backend (returns False = don't block).
    """

    condition: Literal["cumulative_spend_today"] = "cumulative_spend_today"
    usd_threshold: float
    operator: Literal["greater_than", "less_than"] = "greater_than"


# ── Discriminated union ──────────────────────────────────────────────────────

ConditionType = Annotated[
    ArgEquals
    | ArgGreaterThan
    | ArgLessThan
    | ArgMatchesRegex
    | ArgInSet
    | ArgContainsPattern
    | ArgSizeGreaterThan
    | ToolNameIn
    | ActionClassIn
    | IntentClassIn
    | IntentPurposeMatches
    | RegionIn
    | TimeOfDayOutside
    | MetaFieldEquals
    | AnyOf
    | NoneOf
    | PredictedCost
    | BlastRadius
    | AffectedResourceCount
    | DataVolume
    | CumulativeSpendToday,
    Field(discriminator="condition"),
]

# Rebuild forward references for recursive conditions (AnyOf, NoneOf)
AnyOf.model_rebuild()
NoneOf.model_rebuild()


# ── Top-level Policy model ───────────────────────────────────────────────────


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    match: MatchSpec = Field(default_factory=MatchSpec)
    when: list[ConditionType] = Field(default_factory=list)
    action: Action
    reason: str = ""
    priority: int = 0

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        import re

        if not re.match(r"^[a-z0-9-]{1,64}$", v):
            raise ValueError(f"policy id must match [a-z0-9-]{{1,64}}: {v!r}")
        return v


# ── Decision dataclass ───────────────────────────────────────────────────────


@dataclass
class Decision:
    action: Action
    reason: str
    policy_id: str | None
    decision_error: str | None = field(default=None)
