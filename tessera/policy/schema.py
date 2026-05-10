"""Pydantic models for Tessera policy YAML files."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    | NoneOf,
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
