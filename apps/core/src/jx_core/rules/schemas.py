"""HTTP schemas for rule, topic, voice, model, and agent catalogs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .validation import RuleDraft


class VoiceProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern="^(HOST|AGENT)$")
    provider_voice: str = Field(min_length=1, max_length=128)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    chars_per_second: float | None = Field(default=None, gt=0, le=20)
    playback_gain: float = Field(default=1.0, ge=0.5, le=2.0)
    avatar_key: str | None = Field(default=None, pattern=r"^agent-(0[1-9]|1[0-2])$")

    @model_validator(mode="after")
    def validate_avatar_for_kind(self) -> VoiceProfileCreate:
        if self.kind == "AGENT" and self.avatar_key is None:
            raise ValueError("Agent 音色必须配置头像")
        if self.kind == "HOST" and self.avatar_key is not None:
            raise ValueError("主持音色不能配置 Agent 头像")
        return self


class VoiceProfileUpdate(VoiceProfileCreate):
    pass


class VoiceProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    kind: str
    provider_voice: str
    rate: float
    chars_per_second: float | None
    playback_gain: float
    avatar_key: str | None
    status: str


class ModelProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    config_ref: str = Field(min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    api_key: SecretStr | None = None
    max_concurrency: int = Field(default=50, ge=1, le=50)
    token_per_char: float = Field(default=1.0, gt=0, le=10)
    generation_params: dict[str, float | int | bool | str] = Field(default_factory=dict)


class ModelProfileUpdate(ModelProfileCreate):
    pass


class ModelProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    config_ref: str
    base_url: str | None
    model_id: str | None
    api_key_last4: str | None
    max_concurrency: int
    token_per_char: float
    generation_params: dict[str, object]
    status: str


class AgentProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    model_profile_id: UUID
    voice_profile_id: UUID
    system_prompt: str = Field(default="", max_length=20_000)
    debater_prompt: str = Field(default="", max_length=20_000)
    generation_params: dict[str, float | int | bool | str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class AgentProfileUpdate(AgentProfileCreate):
    pass


class AgentProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    model_profile_id: UUID
    voice_profile_id: UUID
    system_prompt: str
    debater_prompt: str
    generation_params: dict[str, object]
    avatar_key: str
    status: str


class TopicCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    affirmative_text: str = Field(min_length=1, max_length=1000)
    negative_text: str = Field(min_length=1, max_length=1000)


class TopicUpdate(TopicCreate):
    pass


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    topic_key: str
    version: int
    title: str
    affirmative_text: str
    negative_text: str
    status: str


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_key: str | None = Field(default=None, min_length=1, max_length=128)
    host_voice_profile_id: UUID
    draft: RuleDraft


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rule_key: str
    version: int
    name: str
    description: str
    side_size: int
    estimated_seconds: int
    status: str
    audio_reviewed_at: datetime | None


class CatalogResponse(BaseModel):
    voices: list[VoiceProfileResponse]
    models: list[ModelProfileResponse]
    agents: list[AgentProfileResponse]
    topics: list[TopicResponse]
    rules: list[RuleResponse]


class CatalogStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ENABLED", "DISABLED"]


__all__ = [
    "AgentProfileCreate",
    "AgentProfileUpdate",
    "AgentProfileResponse",
    "CatalogResponse",
    "CatalogStatusUpdate",
    "ModelProfileCreate",
    "ModelProfileUpdate",
    "ModelProfileResponse",
    "RuleCreate",
    "RuleResponse",
    "TopicCreate",
    "TopicUpdate",
    "TopicResponse",
    "VoiceProfileCreate",
    "VoiceProfileUpdate",
    "VoiceProfileResponse",
]
