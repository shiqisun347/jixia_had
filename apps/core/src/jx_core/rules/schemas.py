"""HTTP schemas for rule, topic, voice, model, and agent catalogs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from ..agent.voice_compatibility import QWEN_31_LEGACY_VOICE_CORRECTIONS
from .validation import RuleDraft, StageActionDraft


class VoiceProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern="^(HOST|AGENT)$")
    provider_voice: str = Field(min_length=1, max_length=128)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    chars_per_second: float | None = Field(default=None, gt=0, le=20)
    playback_gain: float = Field(default=1.0, ge=0.5, le=2.0)
    avatar_key: str | None = Field(
        default=None, pattern=r"^(agent-(0[1-9]|1[0-2])|human-(0[1-9]|1[0-6]))$"
    )

    @field_validator("provider_voice")
    @classmethod
    def reject_unusable_system_voice(cls, value: str) -> str:
        replacement = QWEN_31_LEGACY_VOICE_CORRECTIONS.get(value)
        if replacement is not None:
            raise ValueError(f"此历史音色不支持当前 TTS 模型，请使用 {replacement}")
        return value

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
    calibration_status: str
    calibrated_at: datetime | None
    status: str


class FloatParameterCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def validate_range(self) -> FloatParameterCapability:
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class IntegerParameterCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum: int
    maximum: int

    @model_validator(mode="after")
    def validate_range(self) -> IntegerParameterCapability:
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class ModelCapabilitySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: FloatParameterCapability | None = Field(
        default_factory=lambda: FloatParameterCapability(minimum=0.0, maximum=2.0)
    )
    top_p: FloatParameterCapability | None = Field(
        default_factory=lambda: FloatParameterCapability(minimum=0.0, maximum=1.0)
    )
    max_tokens: IntegerParameterCapability | None = Field(
        default_factory=lambda: IntegerParameterCapability(minimum=1, maximum=32768)
    )

    @model_validator(mode="after")
    def validate_provider_limits(self) -> ModelCapabilitySchema:
        if self.temperature is not None and not (
            0 <= self.temperature.minimum <= self.temperature.maximum <= 2
        ):
            raise ValueError("temperature capability must stay within 0..2")
        if self.top_p is not None and not (0 <= self.top_p.minimum <= self.top_p.maximum <= 1):
            raise ValueError("top_p capability must stay within 0..1")
        if self.max_tokens is not None and not (
            1 <= self.max_tokens.minimum <= self.max_tokens.maximum <= 32768
        ):
            raise ValueError("max_tokens capability must stay within 1..32768")
        return self


class ModelProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    config_ref: str = Field(min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    max_concurrency: int = Field(default=50, ge=1, le=50)
    token_per_char: float = Field(default=1.0, gt=0, le=10)
    generation_params: dict[str, float | int | bool | str] = Field(default_factory=dict)
    capability_schema: ModelCapabilitySchema = Field(default_factory=ModelCapabilitySchema)


class ModelProfileCreate(ModelProfileFields):
    api_key: SecretStr | None = None


class ModelProfileUpdate(ModelProfileFields):
    pass


class ModelApiKeyRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr


class ModelApiKeyRotateResponse(BaseModel):
    status: Literal["rotated"]
    api_key_last4: str


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
    capability_schema: ModelCapabilitySchema
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


class RuleAgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_profile_id: UUID
    generation_params: dict[str, float | int | bool | str] = Field(default_factory=dict)
    status: Literal["ENABLED", "DISABLED"]


class RulePromptUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_text: str = Field(min_length=1, max_length=20_000)


class RuleBasicUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    host_voice_profile_id: UUID
    default_agent_model_profile_id: UUID
    topic_policy: Literal["PRESET_ONLY", "CUSTOM_ONLY", "BOTH"] = "BOTH"
    postmatch_questionnaire_enabled: bool = False


def _empty_stage_actions() -> list[StageActionDraft]:
    return []


class RuleStageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    duration_seconds: int = Field(default=0, ge=0, le=180 * 5)
    start_host_text: str = Field(default="", max_length=2000)
    end_host_text: str = Field(default="", max_length=2000)
    parameters: dict[str, object] = Field(default_factory=dict)
    actions: list[StageActionDraft] = Field(default_factory=_empty_stage_actions, max_length=50)


class RuleStageCreate(RuleStageUpdate):
    stage_kind: Literal["FIXED_SPEECH", "FREE_DEBATE", "PREPARATION", "END"]
    position: int | None = Field(default=None, ge=1, le=50)


class RuleStageReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage_ids: list[UUID] = Field(min_length=1, max_length=50)


class RuleJudgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    model_profile_id: UUID | None = None
    judge_prompt: str = Field(default="", max_length=20_000)
    include_in_leaderboard: bool = False

    @model_validator(mode="after")
    def validate_enabled_config(self) -> RuleJudgeUpdate:
        self.judge_prompt = self.judge_prompt.strip()
        if self.enabled and (self.model_profile_id is None or not self.judge_prompt):
            raise ValueError("启用 AI 裁判时必须选择模型并填写 Prompt")
        if not self.enabled and self.include_in_leaderboard:
            raise ValueError("关闭 AI 裁判时不能计入排行榜")
        return self


class AgentProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    format_version_id: UUID | None
    rule_id: UUID | None
    name: str
    model_profile_id: UUID
    voice_profile_id: UUID
    system_prompt: str
    debater_prompt: str
    generation_params: dict[str, object]
    avatar_key: str
    status: str
    voice_status: str
    model_status: str
    available: bool
    unavailable_reason: Literal["RULE_DISABLED", "VOICE_DISABLED", "MODEL_DISABLED"] | None
    prompt_override_count: int = 0


class TopicCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    affirmative_text: str = Field(min_length=1, max_length=1000)
    negative_text: str = Field(min_length=1, max_length=1000)
    source_text: str | None = Field(default=None, max_length=10_000)
    cedar_id: str | None = Field(default=None, max_length=128)

    @field_validator("source_text", "cedar_id", mode="before")
    @classmethod
    def normalize_optional_provenance(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


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
    source_text: str | None
    cedar_id: str | None
    status: str


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_key: str | None = Field(default=None, min_length=1, max_length=128)
    host_voice_profile_id: UUID
    default_agent_model_profile_id: UUID | None = None
    topic_policy: Literal["PRESET_ONLY", "CUSTOM_ONLY", "BOTH"] = "BOTH"
    draft: RuleDraft

    @model_validator(mode="after")
    def require_default_model_for_4v4(self) -> RuleCreate:
        if self.draft.side_size == 4 and self.default_agent_model_profile_id is None:
            raise ValueError("4v4 规则必须选择默认 Agent 模型")
        return self


class RuleAudioAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    generation_id: UUID | None
    segment_key: str
    text: str
    voice_profile_id: UUID
    status: str
    error_code: str | None
    duration_ms: int | None
    byte_count: int | None
    checksum: str | None
    preview_url: str | None = None


def _empty_audio_assets() -> list[RuleAudioAssetResponse]:
    return []


class RuleAudioGenerationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rule_id: UUID
    config_revision: int
    manifest_hash: str
    status: str
    review_status: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    approved_at: datetime | None
    total_count: int
    ready_count: int
    failed_count: int
    last_error_code: str | None
    is_current: bool
    assets: list[RuleAudioAssetResponse] = Field(default_factory=_empty_audio_assets)


class RuleAudioApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_id: UUID
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RuleBlockerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    message: str
    target: str
    action: str


def _empty_blockers() -> list[RuleBlockerResponse]:
    return []


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
    lifecycle_status: str = "DISABLED"
    readiness: Literal["READY", "BLOCKED"] = "BLOCKED"
    availability: Literal["AVAILABLE", "BLOCKED", "ARCHIVED"] = "BLOCKED"
    blockers: list[RuleBlockerResponse] = Field(default_factory=_empty_blockers)
    pure_human_available: bool = False
    mixed_available: bool = False
    pure_agent_available: bool = False
    available_agent_count: int = 0
    stage_count: int = 0
    audio: RuleAudioGenerationResponse | None = None
    current_audio_generation_id: UUID | None = None
    audio_reviewed_at: datetime | None
    host_voice_profile_id: UUID | None
    default_agent_model_profile_id: UUID | None
    topic_policy: str
    config_revision: int
    historical_read_only: bool
    postmatch_questionnaire_enabled: bool


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
    "RuleAgentUpdate",
    "AgentProfileResponse",
    "CatalogResponse",
    "CatalogStatusUpdate",
    "ModelProfileCreate",
    "ModelProfileUpdate",
    "ModelProfileResponse",
    "ModelApiKeyRotate",
    "ModelApiKeyRotateResponse",
    "RuleCreate",
    "RuleJudgeUpdate",
    "RuleBasicUpdate",
    "RulePromptUpdate",
    "RuleStageUpdate",
    "RuleStageCreate",
    "RuleStageReorder",
    "RuleResponse",
    "RuleAudioAssetResponse",
    "RuleAudioGenerationResponse",
    "RuleAudioApprove",
    "TopicCreate",
    "TopicUpdate",
    "TopicResponse",
    "VoiceProfileCreate",
    "VoiceProfileUpdate",
    "VoiceProfileResponse",
]
