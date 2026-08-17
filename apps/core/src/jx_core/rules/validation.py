"""Strict, finite validation for editable linear debate rules."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RuleStageKind = Literal["FIXED_SPEECH", "FREE_DEBATE", "PREPARATION", "END"]
RuleSide = Literal["AFFIRMATIVE", "NEGATIVE"]


class RuleValidationError(ValueError):
    """Raised when a rule cannot be compiled into the finite MVP form."""


def _empty_actions() -> list[StageActionDraft]:
    return []


class StageActionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_kind: Literal["SPEECH"] = "SPEECH"
    side: RuleSide | None = None
    seat_no: int | None = Field(default=None, ge=1, le=5)
    duration_seconds: int = Field(default=0, ge=0, le=180)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RuleStageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    stage_kind: RuleStageKind
    duration_seconds: int = Field(default=0, ge=0, le=180 * 5)
    start_host_text: str = Field(default="", max_length=2000)
    end_host_text: str = Field(default="", max_length=2000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    actions: list[StageActionDraft] = Field(default_factory=_empty_actions, max_length=50)


class RuleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    side_size: int = Field(ge=1, le=5)
    stages: list[RuleStageDraft] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_linear_order(self) -> RuleDraft:
        end_positions = [
            index for index, stage in enumerate(self.stages) if stage.stage_kind == "END"
        ]
        if len(end_positions) != 1 or end_positions[0] != len(self.stages) - 1:
            raise ValueError("规则必须以且只能以结束阶段结尾")
        for stage in self.stages:
            if stage.stage_kind == "FIXED_SPEECH":
                if not stage.actions:
                    raise ValueError("固定发言阶段至少需要一个发言动作")
                for action in stage.actions:
                    if (
                        action.side is None
                        or action.seat_no is None
                        or action.duration_seconds <= 0
                    ):
                        raise ValueError("固定发言动作必须指定阵营、席位和正数时长")
                    if action.seat_no > self.side_size:
                        raise ValueError("发言席位不能超过该规则的单方人数")
            elif stage.stage_kind == "FREE_DEBATE":
                if stage.duration_seconds <= 0:
                    raise ValueError("自由辩论阶段必须设置正数时长")
                if stage.actions:
                    raise ValueError("自由辩论阶段不接受固定发言动作")
                max_speech_seconds = int(stage.parameters.get("max_speech_seconds", 60))
                if not 1 <= max_speech_seconds <= 180:
                    raise ValueError("自由辩论单次发言时长必须在 1 到 180 秒之间")
                starting_side = stage.parameters.get("starting_side", "AFFIRMATIVE")
                if starting_side not in {"AFFIRMATIVE", "NEGATIVE"}:
                    raise ValueError("自由辩论起始方无效")
            elif stage.stage_kind == "PREPARATION":
                if stage.duration_seconds <= 0:
                    raise ValueError("准备阶段必须设置正数时长")
                if stage.actions:
                    raise ValueError("准备阶段不接受发言动作")
            elif stage.actions or stage.duration_seconds:
                raise ValueError("结束阶段不能包含动作或时长")
        return self


def validate_rule_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return the immutable JSON-safe rule snapshot."""

    try:
        draft = RuleDraft.model_validate(payload)
    except ValueError as error:
        raise RuleValidationError(str(error)) from error

    estimated_seconds = sum(
        stage.duration_seconds * 2
        if stage.stage_kind == "FREE_DEBATE"
        else stage.duration_seconds
        if stage.stage_kind == "PREPARATION"
        else sum(action.duration_seconds for action in stage.actions)
        for stage in draft.stages
    )
    if estimated_seconds > 90 * 60:
        raise RuleValidationError("规则估算时长不能超过 90 分钟")

    snapshot = draft.model_dump(mode="json")
    snapshot["estimated_seconds"] = estimated_seconds
    return snapshot


__all__ = [
    "RuleDraft",
    "RuleStageDraft",
    "StageActionDraft",
    "RuleValidationError",
    "validate_rule_draft",
]
