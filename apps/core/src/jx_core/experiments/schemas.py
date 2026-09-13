"""HTTP schemas for the isolated paper-experiment surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExperimentCapabilitiesResponse(BaseModel):
    creation_enabled: bool
    history_readable: bool = True
    target_version: str = "2.1.0"


class ExperimentBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    rule_id: UUID
    training_room_quota: int = Field(default=3, ge=1, le=20)

    @field_validator("code", "title", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("内容不能为空")
        return normalized


class ExperimentBatchUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    rule_id: UUID
    training_room_quota: int = Field(default=3, ge=1, le=20)

    @field_validator("code", "title", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("内容不能为空")
        return normalized


class ExperimentBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    title: str
    status: Literal["DRAFT", "PUBLISHED", "DISABLED"]
    schedule_version: int
    rule_id: UUID
    format_version_id: UUID | None = None
    training_room_quota: int = 3
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    disabled_at: datetime | None


class ExperimentContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    batch_status: str
    scheduled_match_id: UUID
    scheduled_match_kind: str
    scheduled_match_status: str
    schedule_version: int
    attempt_id: UUID
    attempt_no: int
    attempt_status: str
    room_id: UUID | None
    match_id: UUID | None


class ExperimentMemberBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    participant_code: str = Field(min_length=2, max_length=16, pattern=r"^P\d{2}$")


class ExperimentTeamBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_code: str = Field(min_length=2, max_length=16, pattern=r"^T\d{2}$")
    agent_profile_id: UUID
    members: list[ExperimentMemberBinding] = Field(min_length=3, max_length=3)


class ExperimentExpertBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    expert_code: str = Field(min_length=2, max_length=16, pattern=r"^E\d{2}$")


class ExperimentRosterPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teams: list[ExperimentTeamBinding] = Field(min_length=6, max_length=6)
    experts: list[ExperimentExpertBinding] = Field(min_length=3, max_length=3)


class ExperimentRosterResponse(BaseModel):
    team_count: int
    participant_count: int
    expert_count: int


class ExperimentRosterDetailResponse(BaseModel):
    teams: list[ExperimentTeamBinding]
    experts: list[ExperimentExpertBinding]


class ExperimentGeneratedAccountResponse(BaseModel):
    code: str
    user_id: UUID
    username: str
    temporary_password: str | None
    created: bool


class ExperimentAccountGenerationResponse(BaseModel):
    accounts: list[ExperimentGeneratedAccountResponse]
    created_count: int
    existing_count: int
    default_password: str


class ExperimentScheduleGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_ids: list[UUID] = Field(min_length=6, max_length=6)
    training_topic_id: UUID


class ExperimentBatchDisableResponse(BaseModel):
    id: UUID
    status: Literal["DISABLED"]
    disabled_at: datetime


class ExperimentBatchDeleteResponse(BaseModel):
    id: UUID
    status: Literal["DELETED"] = "DELETED"


class ExperimentScheduleCsvImportResponse(BaseModel):
    batch_id: UUID
    schedule_version: int
    imported_match_count: int
    training_match_count: int


class ExperimentScheduleCsvImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv_text: str = Field(min_length=1, max_length=2_000_000)


class ExperimentSeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    side: Literal["AFFIRMATIVE", "NEGATIVE"]
    seat_no: int
    occupant_kind: Literal["HUMAN", "AGENT"]
    user_id: UUID | None
    agent_profile_id: UUID | None


class ExperimentScheduledMatchResponse(BaseModel):
    id: UUID
    round_no: int
    match_no: int
    topic_id: UUID
    affirmative_team_id: UUID
    negative_team_id: UUID
    kind: Literal["FORMAL", "TRAINING"]
    status: str
    schedule_version: int
    seats: list[ExperimentSeatResponse]


class ExperimentScheduleResponse(BaseModel):
    batch_id: UUID
    batch_status: str
    schedule_version: int
    matches: list[ExperimentScheduledMatchResponse]


class ExperimentPublishResponse(BaseModel):
    batch_id: UUID
    status: Literal["PUBLISHED"]
    schedule_version: int
    formal_match_count: int


class ExperimentMatchProgressResponse(BaseModel):
    scheduled_match_id: UUID
    round_no: int
    match_no: int
    kind: Literal["FORMAL", "TRAINING"]
    topic_title: str
    affirmative_team_code: str
    negative_team_code: str
    match_status: str
    attempt_id: UUID | None
    attempt_no: int | None
    attempt_status: str | None
    match_id: UUID | None
    completed_annotations: int
    total_annotations: int
    public_at: datetime | None


class ExperimentBatchProgressResponse(BaseModel):
    batch_id: UUID
    formal_completed: int
    formal_total: int
    matches: list[ExperimentMatchProgressResponse]


class ExperimentPromptTemplatesResponse(BaseModel):
    version: str
    decision_prompt: str
    speech_prompt: str


class ExperimentResultOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("原因不能为空")
        return normalized


class ExperimentResultVisibilityResponse(BaseModel):
    attempt_id: UUID
    public_at: datetime


class ExperimentJobResponse(BaseModel):
    id: UUID
    task_type: Literal["EXPERIMENT_BATCH_EXPORT", "EXPERIMENT_RETENTION"]
    status: str
    created_at: datetime
    error_code: str | None = None
    artifact_ready: bool = False
    report: dict[str, Any] | None = None


class ExperimentRetentionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: Literal[True] = True


class ExperimentAppointmentResponse(BaseModel):
    scheduled_match_id: UUID
    batch_id: UUID
    batch_title: str
    round_no: int
    match_no: int
    topic_id: UUID
    scheduled_at: datetime | None
    kind: Literal["FORMAL", "TRAINING"]
    status: str
    side: Literal["AFFIRMATIVE", "NEGATIVE"]
    seat_no: int
    room_id: UUID | None
    attempt_id: UUID | None


class ExperimentEnterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_participation_terms_version: str = Field(min_length=1, max_length=128)


class ExperimentEnterResponse(BaseModel):
    scheduled_match_id: UUID
    attempt_id: UUID
    attempt_no: int
    room_id: UUID
    room_code: str
    member_role: Literal["DEBATER", "SPECTATOR"]


class ParticipantAnnotationAnswerSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal[1, 2]
    answers: dict[str, Any]
    client_version: int = Field(ge=1)
    response_duration_ms: int | None = Field(default=None, ge=0)
    audio_play_count: int = Field(default=0, ge=0)
    lock_stage: bool = False


class ParticipantQuestionnaireSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q1: int = Field(ge=1, le=5)
    q2: int = Field(ge=1, le=5)
    q3: int = Field(ge=1, le=5)
    q4: int = Field(ge=1, le=5)
    q5: int = Field(ge=1, le=5)
    q6: str | None = Field(default=None, max_length=4_000)


class ParticipantAnnotationItemResponse(BaseModel):
    id: UUID
    opportunity_id: UUID
    subject_kind: Literal["HUMAN_SELF", "TEAM_AI"]
    speech_id: UUID
    position: int
    stage1_locked: bool
    revealed: bool
    frozen_context: dict[str, Any]
    speech_text: str | None
    answers: dict[str, Any]
    answer_versions: dict[str, int]


class ParticipantQuestionnaireResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    q1: int
    q2: int
    q3: int
    q4: int
    q5: int
    q6: str | None
    submitted_at: datetime


class ParticipantAnnotationTaskResponse(BaseModel):
    id: UUID
    experiment_attempt_id: UUID
    questionnaire_version: str
    status: Literal["PENDING", "IN_PROGRESS", "SUBMITTED"]
    due_at: datetime
    late: bool
    submitted_at: datetime | None
    scheduled_match_kind: Literal["FORMAL", "TRAINING"]
    items: list[ParticipantAnnotationItemResponse]
    questionnaire: ParticipantQuestionnaireResponse | None


class ExpertAnnotationSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q1: Literal["无明显需求", "有事项但无明确优先", "较明确优先", "明显且紧迫", "无法判断"]
    q2: dict[str, Literal["不合适", "可接受但非优先", "较有必要", "应优先尽快", "无法判断"]]
    q3: Literal["更应让给人类", "AI 或人类都合理", "更应 AI 介入", "无法判断"]
    client_version: int = Field(ge=1)
    submit: bool = False


class ExpertAnnotationItemResponse(BaseModel):
    opportunity_id: UUID
    frozen_payload: dict[str, Any]
    q1: str | None
    q2: dict[str, Any] | None
    q3: str | None
    client_version: int
    saved_at: datetime
    submitted_at: datetime | None


class ExpertAnnotationTaskResponse(BaseModel):
    id: UUID
    batch_id: UUID
    status: Literal["PENDING", "IN_PROGRESS", "SUBMITTED"]
    submitted_at: datetime | None
    items: list[ExpertAnnotationItemResponse]


__all__ = [
    "ExperimentBatchCreateRequest",
    "ExperimentBatchUpdateRequest",
    "ExperimentBatchResponse",
    "ExperimentBatchProgressResponse",
    "ExperimentBatchDisableResponse",
    "ExperimentBatchDeleteResponse",
    "ExperimentCapabilitiesResponse",
    "ExperimentAppointmentResponse",
    "ExperimentAccountGenerationResponse",
    "ExperimentContextResponse",
    "ExperimentEnterRequest",
    "ExperimentEnterResponse",
    "ExperimentExpertBinding",
    "ExperimentGeneratedAccountResponse",
    "ExperimentMemberBinding",
    "ExperimentPublishResponse",
    "ExperimentPromptTemplatesResponse",
    "ExperimentResultOverrideRequest",
    "ExperimentResultVisibilityResponse",
    "ExperimentJobResponse",
    "ExperimentRetentionRequest",
    "ExperimentRosterPutRequest",
    "ExperimentRosterResponse",
    "ExperimentRosterDetailResponse",
    "ExperimentScheduleGenerateRequest",
    "ExperimentScheduleCsvImportResponse",
    "ExperimentScheduleCsvImportRequest",
    "ExperimentScheduleResponse",
    "ExperimentScheduledMatchResponse",
    "ExperimentSeatResponse",
    "ExperimentTeamBinding",
    "ExpertAnnotationItemResponse",
    "ExpertAnnotationSaveRequest",
    "ExpertAnnotationTaskResponse",
    "ParticipantAnnotationAnswerSaveRequest",
    "ParticipantAnnotationItemResponse",
    "ParticipantAnnotationTaskResponse",
    "ParticipantQuestionnaireResponse",
    "ParticipantQuestionnaireSubmitRequest",
]
