"""Pydantic HTTP schemas for authentication and profile APIs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    real_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)
    platform_terms_version: str = Field(min_length=1, max_length=128)
    avatar_key: str | None = Field(default=None, pattern=r"^human-(0[1-9]|1[0-6])$")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)
    return_to: str | None = Field(default=None, max_length=2048)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    real_name: str = Field(min_length=1, max_length=128)


class AvatarPresetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    avatar_key: str = Field(pattern=r"^human-(0[1-9]|1[0-6])$")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    real_name: str
    role: str
    avatar_version: int
    default_avatar_key: str
    has_custom_avatar: bool
    must_change_password: bool


class AuthResponse(BaseModel):
    user: UserResponse


class LogoutResponse(BaseModel):
    status: str = "logged_out"


class TermsResponse(BaseModel):
    version: str
    title: str
    body: str


class TemporaryPasswordResponse(BaseModel):
    temporary_password: str
    must_change_password: bool = True


class CurrentMatchSummary(BaseModel):
    match_id: UUID | None
    room_id: UUID
    title: str
    status: str
    code: str


class RecentMatchSummary(BaseModel):
    id: UUID
    title: str
    status: str
    created_at: datetime
    side: str | None = None
    result: str | None = None


class UserSummaryResponse(BaseModel):
    current_match: CurrentMatchSummary | None
    matches: int
    finished_matches: int
    wins: int
    average_score: float
    leaderboard_rank: int | None
    recent_matches: list[RecentMatchSummary]
    latest_device_check: datetime | None


__all__ = [
    "AuthResponse",
    "AvatarPresetUpdateRequest",
    "ChangePasswordRequest",
    "LoginRequest",
    "LogoutResponse",
    "ProfileUpdateRequest",
    "RegisterRequest",
    "TermsResponse",
    "TemporaryPasswordResponse",
    "UserSummaryResponse",
    "UserResponse",
]
