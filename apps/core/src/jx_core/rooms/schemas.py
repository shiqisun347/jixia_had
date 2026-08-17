"""HTTP schemas for public room creation, seating, checks, and snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentSeatAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    side: Literal["AFFIRMATIVE", "NEGATIVE"]
    seat_no: int = Field(ge=1, le=5)
    agent_profile_id: UUID


def _empty_agent_assignments() -> list[AgentSeatAssignment]:
    return []


class RoomCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=32)
    rule_id: UUID
    topic_id: UUID | None = None
    custom_topic_title: str | None = Field(default=None, max_length=500)
    affirmative_text: str | None = Field(default=None, max_length=1000)
    negative_text: str | None = Field(default=None, max_length=1000)
    is_all_agent: bool = False
    human_participation_terms_version: str | None = Field(default=None, max_length=128)
    agent_assignments: list[AgentSeatAssignment] = Field(
        default_factory=_empty_agent_assignments, max_length=10
    )

    @model_validator(mode="after")
    def validate_topic_source(self) -> RoomCreateRequest:
        custom_complete = all(
            value and value.strip()
            for value in (self.custom_topic_title, self.affirmative_text, self.negative_text)
        )
        if (self.topic_id is None) == (not custom_complete):
            raise ValueError("必须且只能选择题库辩题或填写完整自定义辩题")
        return self


class RoomJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_role: Literal["DEBATER", "SPECTATOR"]
    human_participation_terms_version: str | None = Field(default=None, max_length=128)


class RoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_role: Literal["DEBATER", "SPECTATOR"]
    human_participation_terms_version: str | None = Field(default=None, max_length=128)


class SeatSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    side: Literal["AFFIRMATIVE", "NEGATIVE"]
    seat_no: int = Field(ge=1, le=5)
    human_participation_terms_version: str = Field(min_length=1, max_length=128)


class SeatSwapCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: UUID


class SeatSwapRespondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ACCEPT", "REJECT"]


class SeatSwapResponse(BaseModel):
    id: UUID
    room_id: UUID
    requester_user_id: UUID
    target_user_id: UUID
    requester_seat_id: UUID
    target_seat_id: UUID
    requester_name: str
    target_name: str
    status: Literal["PENDING", "ACCEPTED", "REJECTED", "CANCELLED"]
    created_at: datetime


class DeviceCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Kept optional for one rolling-deploy window. The server owns version
    # allocation; older clients may still send this field, but it is ignored.
    check_version: int | None = Field(default=None, ge=1)
    status: Literal["PASS", "WARN", "FAIL"]
    details: dict[str, Any]
    warning_confirmed: bool = False


class ReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_version: int = Field(ge=1)


class SeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    side: str
    seat_no: int
    occupant_type: str
    user_id: UUID | None
    agent_profile_id: UUID | None
    occupant_name: str | None = None
    occupant_avatar_key: str | None = None
    occupant_avatar_version: int | None = None
    occupant_has_custom_avatar: bool = False


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: UUID
    member_role: str
    online: bool
    ready: bool
    joined_at: datetime
    real_name: str
    default_avatar_key: str
    avatar_version: int
    has_custom_avatar: bool


class DeviceCheckSummaryResponse(BaseModel):
    check_version: int
    status: Literal["PASS", "WARN", "FAIL"]
    checked_at: datetime
    valid_until: datetime
    is_valid: bool


class RoomSnapshotResponse(BaseModel):
    id: UUID
    code: str
    title: str
    label: str
    status: str
    organizer_user_id: UUID
    is_all_agent: bool
    auto_fill_agents: bool
    sequence: int
    topic: dict[str, Any]
    rule: dict[str, Any]
    members: list[MemberResponse]
    seats: list[SeatResponse]
    match_id: UUID | None
    viewer_membership_state: Literal["NONE", "ACTIVE", "LEFT"]
    viewer_member_role: str | None
    viewer_ready: bool
    latest_device_check: DeviceCheckSummaryResponse | None


class LobbyRoomResponse(BaseModel):
    id: UUID
    code: str
    title: str
    label: str
    status: str
    auto_fill_agents: bool
    topic_title: str
    rule_name: str
    side_size: int
    occupied_seats: int
    spectator_count: int
    spectator_remaining: int
    spectator_capacity_full: bool
    match_id: UUID | None
    viewer_membership_state: Literal["NONE", "ACTIVE", "LEFT"]
    viewer_member_role: str | None
    viewer_ready: bool


class RoomCodeLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: UUID
    code: str
    status: str


__all__ = [
    "AgentSeatAssignment",
    "DeviceCheckRequest",
    "DeviceCheckSummaryResponse",
    "LobbyRoomResponse",
    "MemberResponse",
    "RoomCreateRequest",
    "RoomJoinRequest",
    "RoleChangeRequest",
    "RoomSnapshotResponse",
    "RoomCodeLookupResponse",
    "ReadyRequest",
    "SeatResponse",
    "SeatSelectRequest",
    "SeatSwapCreateRequest",
    "SeatSwapRespondRequest",
    "SeatSwapResponse",
]
