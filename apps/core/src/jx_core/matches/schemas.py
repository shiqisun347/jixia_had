"""Transport schemas for the authoritative match runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MatchActionResponse(BaseModel):
    stage_position: int
    action_position: int
    action_kind: str
    duration_seconds: int
    side: str | None = None
    seat_no: int | None = None
    speaker_user_id: UUID | None = None
    speaker_kind: str = "HUMAN"
    agent_profile_id: UUID | None = None
    host_audio_path: str | None = None


class AgentDecisionResponse(BaseModel):
    agent_profile_id: UUID
    side: str
    seat_no: int
    status: Literal["DECIDING", "HAND", "SKIP"]
    queue_rank: int | None = None


class FreeDebateHandEntryResponse(BaseModel):
    speaker_kind: Literal["HUMAN", "AGENT"]
    user_id: UUID | None = None
    agent_profile_id: UUID | None = None
    side: str
    seat_no: int
    rank: int


class MatchSnapshotResponse(BaseModel):
    match_id: UUID
    room_id: UUID
    status: str
    action_state: str
    sequence: int
    current_action_index: int
    current_action: MatchActionResponse | None
    current_speech_id: UUID | None
    current_speaker_user_id: UUID | None
    current_agent_profile_id: UUID | None
    speech_remaining_ms: int | None
    countdown_remaining_ms: int | None
    current_speaker_side: str | None
    current_speaker_seat_no: int | None
    free_holder_side: str | None
    free_affirmative_remaining_ms: int | None
    free_negative_remaining_ms: int | None
    hand_queue: list[UUID]
    agent_hand_queue: list[UUID]
    agent_selection_mode: Literal["VOLUNTEER", "FALLBACK"] | None
    agent_decisions: list[AgentDecisionResponse]
    team_hand_queue: list[FreeDebateHandEntryResponse]
    hand_window_open: bool
    error_code: str | None
    offline_user_id: UUID | None
    pause_initiator_user_id: UUID | None
    resume_reasons: list[str] = Field(default_factory=list)


class MatchCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "host.finished",
        "speech.start",
        "speech.finish",
        "speech.reset",
        "hand.raise",
        "hand.cancel",
        "match.pause",
        "match.resume",
        "match.terminate",
    ]
    message_id: str = Field(min_length=1, max_length=128)
    expected_sequence: int = Field(ge=0)
    connection_epoch: int = Field(ge=1)
    reasons: list[str] = Field(default_factory=list, max_length=10)


class MatchEventResponse(BaseModel):
    type: str
    match_id: UUID
    sequence: int
    server_time_ms: int
    payload: dict[str, Any]


class MatchLiveKitTokenResponse(BaseModel):
    server_url: str
    participant_token: str
    room_name: str
    expires_in_seconds: int


class SpeechTranscriptResponse(BaseModel):
    id: UUID
    match_id: UUID
    action_key: str
    user_id: UUID | None
    speaker_kind: str
    agent_profile_id: UUID | None
    generation_id: UUID | None
    side: str
    seat_no: int
    status: str
    asr_raw_final_text: str | None
    display_text: str | None
    audio_duration_ms: int | None
    finalized_at: datetime | None
    audio_truncated: bool


class TranscriptResponse(BaseModel):
    match_id: UUID
    context_version: int
    speeches: list[SpeechTranscriptResponse]


class SpeechTextUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_text: str = Field(max_length=20_000)


__all__ = [
    "MatchActionResponse",
    "MatchCommandRequest",
    "MatchEventResponse",
    "MatchLiveKitTokenResponse",
    "MatchSnapshotResponse",
    "SpeechTextUpdateRequest",
    "SpeechTranscriptResponse",
    "TranscriptResponse",
]
