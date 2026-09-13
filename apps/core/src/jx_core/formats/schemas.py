from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

KNOWN_PROMPT_VARIABLES = frozenset(
    {
        "TOPIC",
        "POSITION",
        "STANCE",
        "AFFIRMATIVE_STANCE",
        "NEGATIVE_STANCE",
        "DEBATER_SEAT",
        "DEBATE_HISTORY",
        "SIDE_REMAINING_MS",
        "OPPONENT_REMAINING_MS",
        "CURRENT_STAGE",
        "NEXT_STAGE",
    }
)


def _empty_dimensions() -> list[dict[str, object]]:
    return []


class FormatDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_key: str = Field(pattern=r"^[a-z][a-z0-9-]{2,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    rule_id: UUID


class FormatDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    rule_id: UUID
    change_note: str = Field(default="", max_length=2000)
    revision: int = Field(ge=1)


class FormatPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_note: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=1)


class FormatVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    format_key: str
    version: int
    name: str
    description: str
    status: str
    rule_id: UUID
    source_version_id: UUID | None
    change_note: str
    revision: int
    created_at: datetime
    updated_at: datetime


class PromptTemplatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage_key: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=64)
    template_text: str = Field(min_length=1, max_length=40_000)
    variables: list[str] = Field(default_factory=list, max_length=64)
    output_contract: str = Field(default="TEXT", max_length=64)

    @model_validator(mode="after")
    def validate_variables(self) -> PromptTemplatePayload:
        found = set(re.findall(r"{{\s*([A-Z][A-Z0-9_]*)\s*}}", self.template_text))
        unknown = sorted(found - KNOWN_PROMPT_VARIABLES)
        if unknown:
            raise ValueError(f"unknown prompt variables: {', '.join(unknown)}")
        if self.variables and set(self.variables) != found:
            raise ValueError("declared prompt variables do not match the template")
        required: set[str] = (
            {
                "TOPIC",
                "POSITION",
                "STANCE",
                "AFFIRMATIVE_STANCE",
                "NEGATIVE_STANCE",
                "DEBATER_SEAT",
                "DEBATE_HISTORY",
                "SIDE_REMAINING_MS",
                "OPPONENT_REMAINING_MS",
            }
            if self.stage_key == "FREE_DEBATE" and self.purpose in {"DECISION", "SPEECH"}
            else set()
        )
        missing = sorted(required - found)
        if missing:
            raise ValueError(f"missing required prompt variables: {', '.join(missing)}")
        self.variables = sorted(found)
        return self


class FormatAgentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    model_profile_id: UUID
    voice_profile_id: UUID
    generation_params: dict[str, float | int | bool | str] = Field(default_factory=dict)


class FormatModulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    config: dict[str, object] = Field(default_factory=dict)


class FormatJudgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_profile_id: UUID
    system_prompt: str = Field(min_length=1, max_length=20_000)
    judge_prompt: str = Field(min_length=1, max_length=30_000)
    generation_params: dict[str, float | int | bool | str] = Field(default_factory=dict)
    dimensions: list[dict[str, object]] = Field(default_factory=_empty_dimensions, max_length=32)
    output_contract: str = Field(default="JUDGE_RESULT_V1", max_length=64)


class FormatTopicPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: int = Field(ge=1, le=1000)
    topic_id: UUID | None = None
    private_snapshot: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_source(self) -> FormatTopicPayload:
        if (self.topic_id is None) == (self.private_snapshot is None):
            raise ValueError("choose exactly one topic source")
        if self.private_snapshot is not None:
            required = {"title", "affirmative_text", "negative_text"}
            if any(not self.private_snapshot.get(key, "").strip() for key in required):
                raise ValueError("private topic snapshot is incomplete")
        return self


class FormatWorkspaceResponse(FormatVersionResponse):
    agents: list[dict[str, object]]
    prompts: list[dict[str, object]]
    modules: list[dict[str, object]]
    judge: dict[str, object] | None
    topic_references: list[dict[str, object]]


__all__ = [
    "FormatDraftCreate",
    "FormatAgentPayload",
    "FormatJudgePayload",
    "FormatModulePayload",
    "FormatTopicPayload",
    "FormatDraftUpdate",
    "FormatPublishRequest",
    "FormatVersionResponse",
    "FormatWorkspaceResponse",
    "KNOWN_PROMPT_VARIABLES",
    "PromptTemplatePayload",
]
