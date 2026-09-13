"""SQLAlchemy models for the first authentication business slice.

The models intentionally describe only durable identity and connection
ownership primitives.  Room, match, and media tables belong to later slices.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BIGINT,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata registry used by Alembic and database tests."""


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("char_length(username) BETWEEN 3 AND 32", name="ck_users_username_length"),
        CheckConstraint(
            "char_length(username_normalized) BETWEEN 3 AND 32",
            name="ck_users_username_normalized_length",
        ),
        CheckConstraint(
            "char_length(real_name) BETWEEN 2 AND 30",
            name="ck_users_real_name_length",
        ),
        CheckConstraint("role IN ('USER', 'ADMIN')", name="ck_users_role"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_users_status"),
        CheckConstraint("failed_login_count >= 0", name="ck_users_failed_login_count_nonnegative"),
        CheckConstraint("avatar_version >= 0", name="ck_users_avatar_version_nonnegative"),
        Index("ix_users_status", "status"),
        Index(
            "ux_users_single_admin", "role", unique=True, postgresql_where=text("role = 'ADMIN'")
        ),
        UniqueConstraint("username_normalized", name="uq_users_username_normalized"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(32), nullable=False)
    real_name: Mapped[str] = mapped_column(String(30), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="USER")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    must_change_password: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    avatar_path: Mapped[str | None] = mapped_column(String(512))
    avatar_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    default_avatar_key: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="human-01"
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("char_length(token_hash) = 64", name="ck_sessions_token_hash_length"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
        Index("ix_sessions_revoked_at", "revoked_at"),
        UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserConsent(Base):
    __tablename__ = "user_consents"
    __table_args__ = (
        CheckConstraint(
            "consent_type IN ('platform_terms', 'human_participation')",
            name="ck_user_consents_type",
        ),
        UniqueConstraint("user_id", "consent_type", "version", name="uq_user_consents_version"),
        Index("ix_user_consents_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RoomConnection(Base):
    __tablename__ = "room_connections"
    __table_args__ = (
        CheckConstraint("connection_epoch >= 1", name="ck_room_connections_epoch_positive"),
        UniqueConstraint("connection_id", name="uq_room_connections_connection_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    connection_epoch: Mapped[int] = mapped_column(BIGINT, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("result IN ('SUCCESS', 'FAILURE')", name="ck_audit_logs_result"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_target", "target_type", "target_id"),
        Index("ix_audit_logs_actor", "actor_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemSetting(Base):
    """Whitelisted global runtime controls; values never contain credentials."""

    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RoomConnectionLease(Base):
    __tablename__ = "room_connection_leases"
    __table_args__ = (
        CheckConstraint("connection_epoch >= 1", name="ck_room_connection_leases_epoch_positive"),
        UniqueConstraint("connection_id", name="uq_room_connection_leases_connection_id"),
        Index("ix_room_connection_leases_user_room", "user_id", "room_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    connection_epoch: Mapped[int] = mapped_column(BIGINT, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (
        CheckConstraint("kind IN ('HOST', 'AGENT')", name="ck_voice_profiles_kind"),
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_voice_profiles_status"),
        CheckConstraint(
            "calibration_status IN ('NOT_CALIBRATED', 'READY', 'STALE', 'FAILED')",
            name="ck_voice_profiles_calibration_status",
        ),
        CheckConstraint("rate BETWEEN 0.50 AND 2.00", name="ck_voice_profiles_rate"),
        CheckConstraint(
            "playback_gain BETWEEN 0.50 AND 2.00", name="ck_voice_profiles_playback_gain"
        ),
        CheckConstraint(
            "(kind = 'AGENT' AND avatar_key IS NOT NULL AND avatar_key IN "
            "('agent-01','agent-02','agent-03','agent-04','agent-05','agent-06',"
            "'agent-07','agent-08','agent-09','agent-10','agent-11','agent-12')) OR "
            "(kind = 'HOST' AND avatar_key IS NULL)",
            name="ck_voice_profiles_avatar_key_by_kind",
        ),
        UniqueConstraint("provider_voice", name="uq_voice_profiles_provider_voice"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_voice: Mapped[str] = mapped_column(String(128), nullable=False)
    rate: Mapped[float] = mapped_column(nullable=False, server_default="1.0")
    chars_per_second: Mapped[float | None] = mapped_column(nullable=True)
    playback_gain: Mapped[float] = mapped_column(nullable=False, server_default="1.0")
    avatar_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    calibration_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_CALIBRATED"
    )
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ENABLED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ModelProfile(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_model_profiles_status"),
        CheckConstraint(
            "max_concurrency BETWEEN 1 AND 50", name="ck_model_profiles_max_concurrency"
        ),
        CheckConstraint(
            "token_per_char > 0 AND token_per_char <= 10",
            name="ck_model_profiles_token_per_char",
        ),
        UniqueConstraint("name", name="uq_model_profiles_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    config_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    model_id: Mapped[str | None] = mapped_column(String(256))
    api_key_ciphertext: Mapped[bytes | None] = mapped_column(BYTEA)
    api_key_nonce: Mapped[bytes | None] = mapped_column(BYTEA)
    api_key_last4: Mapped[str | None] = mapped_column(String(4))
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default="50")
    token_per_char: Mapped[float] = mapped_column(nullable=False, server_default="1.0")
    generation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    capability_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            "jsonb_build_object("
            "'temperature', jsonb_build_object('minimum', 0.0, 'maximum', 2.0), "
            "'top_p', jsonb_build_object('minimum', 0.0, 'maximum', 1.0), "
            "'max_tokens', jsonb_build_object('minimum', 1, 'maximum', 32768)"
            ")"
        ),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ENABLED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentProfile(Base):
    __tablename__ = "agent_profiles"
    __table_args__ = (
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_agent_profiles_status"),
        UniqueConstraint("format_version_id", "name", name="uq_agent_profiles_format_name"),
        Index("ix_agent_profiles_rule_id", "rule_id"),
        Index(
            "ux_agent_profiles_rule_voice",
            "rule_id",
            "voice_profile_id",
            unique=True,
            postgresql_where=text("rule_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    format_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("format_versions.id", ondelete="RESTRICT")
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE")
    )
    model_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("model_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    voice_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    debater_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    generation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ENABLED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_topics_status"),
        UniqueConstraint("topic_key", "version", name="uq_topics_key_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    affirmative_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    negative_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    cedar_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ENABLED")
    created_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class FormatVersion(Base):
    __tablename__ = "format_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_format_versions_status",
        ),
        UniqueConstraint("format_key", "version", name="uq_format_versions_key_version"),
        Index(
            "ux_format_versions_one_draft",
            "format_key",
            unique=True,
            postgresql_where=text("status = 'DRAFT'"),
        ),
        Index(
            "ux_format_versions_one_published",
            "format_key",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    format_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    rule_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="RESTRICT"), nullable=False
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("format_versions.id", ondelete="RESTRICT")
    )
    change_note: Mapped[str] = mapped_column(String(2000), nullable=False, server_default="")
    published_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        Index(
            "ux_prompt_templates_default_slot",
            "format_version_id",
            "stage_key",
            "purpose",
            unique=True,
            postgresql_where=text("agent_profile_id IS NULL"),
        ),
        Index(
            "ux_prompt_templates_agent_slot",
            "format_version_id",
            "agent_profile_id",
            "stage_key",
            "purpose",
            unique=True,
            postgresql_where=text("agent_profile_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    format_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("format_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="CASCADE")
    )
    stage_key: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_contract: Mapped[str] = mapped_column(String(64), nullable=False, server_default="TEXT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class FormatModule(Base):
    __tablename__ = "format_modules"
    __table_args__ = (
        UniqueConstraint("format_version_id", "module_key", name="uq_format_modules_key"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    format_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("format_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class FormatJudgeProfile(Base):
    __tablename__ = "format_judge_profiles"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    format_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("format_versions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    model_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("model_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    judge_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_contract: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="JUDGE_RESULT_V1"
    )


class FormatTopicReference(Base):
    __tablename__ = "format_topic_references"
    __table_args__ = (
        CheckConstraint(
            "(topic_id IS NOT NULL AND private_snapshot IS NULL) OR "
            "(topic_id IS NULL AND private_snapshot IS NOT NULL)",
            name="ck_format_topic_references_source",
        ),
        UniqueConstraint("format_version_id", "position", name="uq_format_topic_refs_position"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    format_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("format_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="RESTRICT")
    )
    private_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'GENERATING_AUDIO', 'READY', 'ENABLED', 'DISABLED', "
            "'GENERATING_AUDIO_FAILED', 'ARCHIVED')",
            name="ck_rules_status",
        ),
        CheckConstraint("side_size BETWEEN 1 AND 5", name="ck_rules_side_size"),
        CheckConstraint(
            "topic_policy IN ('PRESET_ONLY', 'CUSTOM_ONLY', 'BOTH')",
            name="ck_rules_topic_policy",
        ),
        CheckConstraint("config_revision >= 1", name="ck_rules_config_revision_positive"),
        UniqueConstraint("rule_key", "version", name="uq_rules_key_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    rule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, server_default="")
    side_size: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DRAFT")
    host_voice_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("voice_profiles.id", ondelete="RESTRICT")
    )
    default_agent_model_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("model_profiles.id", ondelete="RESTRICT")
    )
    topic_policy: Mapped[str] = mapped_column(String(32), nullable=False, server_default="BOTH")
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    postmatch_questionnaire_enabled: Mapped[bool] = mapped_column(
        nullable=False, server_default="false"
    )
    historical_read_only: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    audio_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuleStage(Base):
    __tablename__ = "rule_stages"
    __table_args__ = (
        CheckConstraint(
            "stage_kind IN ('FIXED_SPEECH', 'FREE_DEBATE', 'PREPARATION', 'END')",
            name="ck_rule_stages_kind",
        ),
        UniqueConstraint("rule_id", "position", name="uq_rule_stages_position"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    rule_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    stage_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    start_host_text: Mapped[str] = mapped_column(String(2000), nullable=False, server_default="")
    end_host_text: Mapped[str] = mapped_column(String(2000), nullable=False, server_default="")
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class StageAction(Base):
    __tablename__ = "stage_actions"
    __table_args__ = (UniqueConstraint("stage_id", "position", name="uq_stage_actions_position"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    stage_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("rule_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="SPEECH")
    side: Mapped[str | None] = mapped_column(String(16))
    seat_no: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class StagePromptTemplate(Base):
    __tablename__ = "stage_prompt_templates"
    __table_args__ = (
        CheckConstraint("purpose IN ('SPEECH', 'DECISION')", name="ck_stage_prompts_purpose"),
        UniqueConstraint("stage_id", "purpose", name="uq_stage_prompts_slot"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    stage_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("rule_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_contract: Mapped[str] = mapped_column(String(32), nullable=False, server_default="TEXT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentPromptOverride(Base):
    __tablename__ = "agent_prompt_overrides"
    __table_args__ = (
        CheckConstraint("purpose IN ('SPEECH', 'DECISION')", name="ck_agent_prompts_purpose"),
        UniqueConstraint("agent_profile_id", "stage_id", "purpose", name="uq_agent_prompts_slot"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("rule_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_contract: Mapped[str] = mapped_column(String(32), nullable=False, server_default="TEXT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RuleJudgeConfig(Base):
    __tablename__ = "rule_judge_configs"
    __table_args__ = (
        CheckConstraint(
            "(enabled = false AND include_in_leaderboard = false) OR "
            "(enabled = true AND model_profile_id IS NOT NULL AND char_length(judge_prompt) > 0)",
            name="ck_rule_judge_enabled_config",
        ),
    )

    rule_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    model_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("model_profiles.id", ondelete="RESTRICT")
    )
    judge_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    include_in_leaderboard: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class HostAudioAsset(Base):
    __tablename__ = "host_audio_assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'READY', 'FAILED')", name="ck_host_audio_assets_status"
        ),
        UniqueConstraint("rule_id", "segment_key", name="uq_host_audio_assets_segment"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    rule_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    segment_key: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    storage_path: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    error_code: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint(
            "status IN ('WAITING', 'START_PENDING_RUNTIME', 'RUNNING', 'PAUSED', "
            "'FINISHED', 'TERMINATED')",
            name="ck_rooms_status",
        ),
        Index("ix_rooms_status", "status"),
        Index("ix_rooms_created_at", "created_at"),
        UniqueConstraint("code", name="uq_rooms_code"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    topic_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="RESTRICT")
    )
    topic_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rule_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="RESTRICT"), nullable=False
    )
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    format_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("format_versions.id", ondelete="RESTRICT")
    )
    format_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    organizer_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_all_agent: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    auto_fill_agents: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="WAITING")


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (
        CheckConstraint(
            "member_role IN ('ORGANIZER', 'DEBATER', 'SPECTATOR')",
            name="ck_room_members_role",
        ),
        Index("ix_room_members_room_active", "room_id", "left_at"),
        UniqueConstraint("room_id", "user_id", name="uq_room_members_user"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    member_role: Mapped[str] = mapped_column(String(16), nullable=False)
    online: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    ready: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (
        CheckConstraint("side IN ('AFFIRMATIVE', 'NEGATIVE')", name="ck_seats_side"),
        CheckConstraint(
            "occupant_type IN ('EMPTY', 'HUMAN', 'AGENT')", name="ck_seats_occupant_type"
        ),
        CheckConstraint("seat_no BETWEEN 1 AND 5", name="ck_seats_seat_no"),
        CheckConstraint(
            "(occupant_type = 'EMPTY' AND user_id IS NULL AND agent_profile_id IS NULL) OR "
            "(occupant_type = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(occupant_type = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_seats_occupant_reference",
        ),
        UniqueConstraint("room_id", "side", "seat_no", name="uq_seats_position"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    occupant_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="EMPTY")
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="RESTRICT")
    )
    configured_agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="RESTRICT")
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SeatSwapRequest(Base):
    __tablename__ = "seat_swap_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','ACCEPTED','REJECTED','CANCELLED')",
            name="ck_seat_swap_requests_status",
        ),
        CheckConstraint(
            "requester_user_id <> target_user_id", name="ck_seat_swap_requests_distinct_users"
        ),
        Index("ix_seat_swap_requests_room_status", "room_id", "status"),
        Index("ix_seat_swap_requests_requester", "requester_user_id", "created_at"),
        Index("ix_seat_swap_requests_target", "target_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    target_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requester_seat_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("seats.id", ondelete="CASCADE"), nullable=False
    )
    target_seat_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("seats.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceCheck(Base):
    __tablename__ = "device_checks"
    __table_args__ = (
        CheckConstraint("status IN ('PASS', 'WARN', 'FAIL')", name="ck_device_checks_status"),
        Index("ix_device_checks_latest", "room_id", "user_id", "check_version"),
        UniqueConstraint("room_id", "user_id", "check_version", name="uq_device_checks_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    check_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    warning_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrowserDeviceCheck(Base):
    __tablename__ = "browser_device_checks"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_browser_device_checks_token_hash"),
        Index("ix_browser_device_checks_user", "user_id"),
        Index("ix_browser_device_checks_valid_until", "valid_until"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapacityGuard(Base):
    __tablename__ = "capacity_guards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class BackgroundTask(Base):
    __tablename__ = "background_tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
            "'POSTMATCH_AUDIO', 'FILE_CLEANUP', 'MATCH_EXPORT', 'EXPERIMENT_POSTMATCH', "
            "'EXPERIMENT_RESULT_PUBLISH', 'EXPERIMENT_BATCH_EXPORT', 'EXPERIMENT_RETENTION', "
            "'RUNTIME_LOG_RETENTION')",
            name="ck_background_tasks_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_background_tasks_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_background_tasks_attempts"),
        CheckConstraint("max_attempts BETWEEN 1 AND 5", name="ck_background_tasks_max_attempts"),
        Index("ix_background_tasks_claim", "status", "available_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BulkJob(Base):
    __tablename__ = "bulk_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_bulk_jobs_status",
        ),
        CheckConstraint(
            "operation IN ('ENABLE', 'DISABLE', 'EXPORT', 'DELETE')", name="ck_bulk_jobs_operation"
        ),
        Index("ix_bulk_jobs_owner_created", "created_by_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resource: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="QUEUED")
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    succeeded_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BulkJobItem(Base):
    __tablename__ = "bulk_job_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_bulk_job_items_status",
        ),
        UniqueConstraint("job_id", "target_id", name="uq_bulk_job_items_target"),
        Index("ix_bulk_job_items_job_status", "job_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("bulk_jobs.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    error_code: Mapped[str | None] = mapped_column(String(128))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('START_PENDING_RUNTIME', 'START_COUNTDOWN', 'RUNNING', 'PAUSED', "
            "'FINISHED', "
            "'TERMINATED', 'SYSTEM_RECOVERY', 'ERROR')",
            name="ck_matches_status",
        ),
        Index("ix_matches_status", "status"),
        UniqueConstraint("room_id", name="uq_matches_room"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_stage_position: Mapped[int | None] = mapped_column(Integer)
    current_action_position: Mapped[int | None] = mapped_column(Integer)
    current_speech_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    match_seed: Mapped[int] = mapped_column(BIGINT, nullable=False, server_default="0")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    context_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    runtime_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MatchEvent(Base):
    __tablename__ = "match_events"
    __table_args__ = (
        UniqueConstraint("match_id", "sequence", name="uq_match_events_sequence"),
        Index("ix_match_events_match_sequence", "match_id", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Speech(Base):
    __tablename__ = "speeches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTED', 'FINALIZING', 'FINALIZED', 'FAILED', 'RESET')",
            name="ck_speeches_status",
        ),
        CheckConstraint("speaker_kind IN ('HUMAN', 'AGENT')", name="ck_speeches_speaker_kind"),
        CheckConstraint(
            "(speaker_kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(speaker_kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_speeches_speaker_reference",
        ),
        Index("ix_speeches_match", "match_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    opportunity_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="SET NULL", use_alter=True),
    )
    action_key: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    speaker_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="HUMAN")
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="RESTRICT")
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="STARTED")
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    asr_raw_final_text: Mapped[str | None] = mapped_column(Text)
    display_text: Mapped[str | None] = mapped_column(Text)
    first_interim_latency_ms: Mapped[int | None] = mapped_column(Integer)
    final_latency_ms: Mapped[int | None] = mapped_column(Integer)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer)
    asr_error_code: Mapped[str | None] = mapped_column(String(128))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    llm_draft_text: Mapped[str | None] = mapped_column(Text)
    audio_truncated: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    audio_storage_path: Mapped[str | None] = mapped_column(String(512))
    playback_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MatchParticipant(Base):
    __tablename__ = "match_participants"
    __table_args__ = (
        CheckConstraint("kind IN ('HUMAN', 'AGENT')", name="ck_match_participants_kind"),
        CheckConstraint(
            "(kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_match_participants_reference",
        ),
        UniqueConstraint("match_id", "side", "seat_no", name="uq_match_participants_seat"),
        Index("ix_match_participants_match", "match_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="RESTRICT")
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)


class TranscriptSubmission(Base):
    __tablename__ = "transcript_submissions"
    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="uq_transcript_submissions_user"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    auto_submitted: Mapped[bool] = mapped_column(nullable=False, server_default="false")


class JudgeProfile(Base):
    __tablename__ = "judge_profiles"
    __table_args__ = (
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_judge_profiles_status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("model_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    judge_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    generation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ENABLED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JudgeResult(Base):
    __tablename__ = "judge_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_judge_results_status",
        ),
        CheckConstraint(
            "capture_completeness IN ('LIMITED', 'COMPLETE')",
            name="ck_judge_results_capture_completeness",
        ),
        Index("ix_judge_results_match", "match_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    judge_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("judge_profiles.id", ondelete="RESTRICT")
    )
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(128))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    request_blob_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("call_content_blobs.id", ondelete="RESTRICT")
    )
    response_blob_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("call_content_blobs.id", ondelete="RESTRICT")
    )
    capture_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    capture_completeness: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="LIMITED"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"
    __table_args__ = (
        CheckConstraint("kind IN ('HUMAN', 'AGENT')", name="ck_leaderboard_snapshots_kind"),
        UniqueConstraint("batch_id", "kind", "rank", name="uq_leaderboard_snapshot_rank"),
        Index("ix_leaderboard_snapshots_latest", "generated_at", "kind"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    participant_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    matches: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    average_personal_score: Mapped[float] = mapped_column(nullable=False, server_default="0")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MatchFile(Base):
    __tablename__ = "match_files"
    __table_args__ = (
        CheckConstraint(
            "file_kind IN ('HUMAN_RAW', 'AGENT_RAW', 'MATCH_REPLAY', 'EXPORT_PACKAGE')",
            name="ck_match_files_kind",
        ),
        CheckConstraint(
            "status IN ('PROCESSING', 'READY', 'FAILED', 'EXPIRED')",
            name="ck_match_files_status",
        ),
        CheckConstraint("byte_count >= 0", name="ck_match_files_byte_count"),
        UniqueConstraint("match_id", "file_key", name="uq_match_files_key"),
        Index("ix_match_files_expiry", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    speech_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("speeches.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    file_key: Mapped[str] = mapped_column(String(128), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PROCESSING")
    storage_path: Mapped[str | None] = mapped_column(String(512))
    codec: Mapped[str | None] = mapped_column(String(32))
    byte_count: Mapped[int] = mapped_column(BIGINT, nullable=False, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permanent: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MatchExport(Base):
    __tablename__ = "match_exports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'EXPIRED')",
            name="ck_match_exports_status",
        ),
        Index("ix_match_exports_owner_created", "created_by_user_id", "created_at"),
        Index("ix_match_exports_status_expiry", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="QUEUED")
    include_audio: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    storage_path: Mapped[str | None] = mapped_column(String(512))
    byte_count: Mapped[int] = mapped_column(BIGINT, nullable=False, server_default="0")
    sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MatchExportItem(Base):
    __tablename__ = "match_export_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_match_export_items_status",
        ),
        UniqueConstraint("export_id", "match_id", name="uq_match_export_items_match"),
        Index("ix_match_export_items_export_status", "export_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    export_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("match_exports.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    match_status: Mapped[str] = mapped_column(String(32), nullable=False)
    cutoff_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AsrSegment(Base):
    __tablename__ = "asr_segments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTED', 'FINALIZED', 'FAILED', 'DISCARDED')",
            name="ck_asr_segments_status",
        ),
        UniqueConstraint("speech_id", "segment_no", name="uq_asr_segments_speech_no"),
        UniqueConstraint("task_id", name="uq_asr_segments_task_id"),
        Index("ix_asr_segments_speech", "speech_id", "segment_no"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    speech_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("speeches.id", ondelete="CASCADE"), nullable=False
    )
    segment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="STARTED")
    raw_final_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    first_interim_latency_ms: Mapped[int | None] = mapped_column(Integer)
    final_latency_ms: Mapped[int | None] = mapped_column(Integer)
    pcm_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentGeneration(Base):
    __tablename__ = "agent_generations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTED', 'LLM_READY', 'PLAYING', 'FINALIZED', 'FAILED', 'CANCELLED')",
            name="ck_agent_generations_status",
        ),
        CheckConstraint(
            "capture_completeness IN ('LIMITED', 'COMPLETE')",
            name="ck_agent_generations_capture_completeness",
        ),
        Index("ix_agent_generations_match", "match_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    action_key: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="STARTED")
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    call_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="LLM_SPEECH")
    provider: Mapped[str | None] = mapped_column(String(64))
    model_snapshot: Mapped[str | None] = mapped_column(String(128))
    generation_params_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    request_blob_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("call_content_blobs.id", ondelete="RESTRICT"),
    )
    response_blob_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("call_content_blobs.id", ondelete="RESTRICT"),
    )
    capture_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    capture_completeness: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="LIMITED"
    )
    llm_draft_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    first_token_latency_ms: Mapped[int | None] = mapped_column(Integer)
    completed_latency_ms: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentFreeDebateDecision(Base):
    __tablename__ = "agent_free_debate_decisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DECIDING', 'HAND', 'SKIP')",
            name="ck_agent_free_debate_decisions_status",
        ),
        CheckConstraint(
            "willingness IS NULL OR (willingness >= 0 AND willingness <= 1)",
            name="ck_agent_free_debate_decisions_willingness",
        ),
        UniqueConstraint(
            "match_id",
            "decision_round_id",
            "agent_profile_id",
            name="uq_agent_free_debate_decisions_round_agent",
        ),
        Index("ix_agent_free_debate_decisions_match", "match_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    action_key: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_round_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DECIDING")
    should_speak: Mapped[bool | None] = mapped_column()
    decision_reason: Mapped[str | None] = mapped_column(String(20))
    willingness: Mapped[float | None] = mapped_column()
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    result_order: Mapped[int | None] = mapped_column(Integer)
    final_queue_rank: Mapped[int | None] = mapped_column(Integer)
    human_hand_at_result: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    human_hand_at_lock: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    selected: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    fallback: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    opportunity_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="SET NULL"),
    )
    effective_status: Mapped[str | None] = mapped_column(String(32))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger_kind: Mapped[str | None] = mapped_column(String(32))
    late: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    stale: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    invalidated_reason: Mapped[str | None] = mapped_column(String(128))
    human_hand_snapshot: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CallContentBlob(Base):
    __tablename__ = "call_content_blobs"
    __table_args__ = (
        CheckConstraint(
            "content_kind IN ('REQUEST', 'RESPONSE')", name="ck_call_content_blobs_kind"
        ),
        CheckConstraint("compression = 'ZLIB'", name="ck_call_content_blobs_compression"),
        CheckConstraint(
            "uncompressed_bytes >= 0 AND compressed_bytes >= 0 AND chunk_count > 0",
            name="ck_call_content_blobs_sizes",
        ),
        UniqueConstraint(
            "sha256",
            "serialization_version",
            "content_kind",
            name="uq_call_content_blobs_address",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    serialization_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    compression: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ZLIB")
    uncompressed_bytes: Mapped[int] = mapped_column(BIGINT, nullable=False)
    compressed_bytes: Mapped[int] = mapped_column(BIGINT, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CallContentBlobChunk(Base):
    __tablename__ = "call_content_blob_chunks"
    __table_args__ = (CheckConstraint("chunk_no >= 0", name="ck_call_content_blob_chunks_no"),)

    blob_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("call_content_blobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    chunk_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[bytes] = mapped_column(BYTEA, nullable=False)


class ExternalCall(Base):
    __tablename__ = "external_calls"
    __table_args__ = (
        CheckConstraint(
            "call_kind IN ('LLM_SPEECH', 'LLM_DECISION', 'JUDGE', 'ASR', 'TTS')",
            name="ck_external_calls_kind",
        ),
        CheckConstraint(
            "status IN ('STARTED', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_external_calls_status",
        ),
        CheckConstraint("attempt_no > 0", name="ck_external_calls_attempt"),
        Index("ix_external_calls_match_started", "match_id", "started_at"),
        Index("ix_external_calls_kind_status", "call_kind", "status"),
        Index("ix_external_calls_speech_started", "speech_id", "started_at"),
        Index("ix_external_calls_decision_round", "decision_round_id", "started_at"),
        Index("ix_external_calls_capture_started", "capture_version", "started_at", "id"),
        Index("ix_external_calls_source_started", "source_kind", "started_at"),
        Index("ix_external_calls_logical_attempt", "logical_call_id", "attempt_no"),
        CheckConstraint(
            "source_kind IS NULL OR source_kind IN "
            "('MATCH', 'HOST_AUDIO', 'CONFIG_TEST', 'OPS_PROBE')",
            name="ck_external_calls_source_kind",
        ),
        CheckConstraint(
            "request_capture_status IS NULL OR request_capture_status IN "
            "('COMPLETE', 'TRUNCATED', 'REJECTED_SENSITIVE', 'UNAVAILABLE')",
            name="ck_external_calls_request_capture_status",
        ),
        CheckConstraint(
            "response_capture_status IS NULL OR response_capture_status IN "
            "('COMPLETE', 'TRUNCATED', 'REJECTED_SENSITIVE', 'UNAVAILABLE')",
            name="ck_external_calls_response_capture_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    call_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    voice: Mapped[str | None] = mapped_column(String(128))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="STARTED")
    match_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE")
    )
    speech_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("speeches.id", ondelete="CASCADE")
    )
    agent_generation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_generations.id", ondelete="CASCADE")
    )
    agent_decision_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_free_debate_decisions.id", ondelete="CASCADE"),
    )
    asr_segment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("asr_segments.id", ondelete="CASCADE")
    )
    judge_result_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("judge_results.id", ondelete="CASCADE")
    )
    request_blob_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("call_content_blobs.id", ondelete="RESTRICT")
    )
    response_blob_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("call_content_blobs.id", ondelete="RESTRICT")
    )
    request_id: Mapped[str | None] = mapped_column(String(128))
    generation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_round_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    opportunity_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="SET NULL"),
    )
    connection_epoch: Mapped[int | None] = mapped_column(BIGINT)
    context_version: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_result_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_result_latency_ms: Mapped[int | None] = mapped_column(Integer)
    completed_latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    audio_bytes: Mapped[int | None] = mapped_column(BIGINT)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    capture_version: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_kind: Mapped[str | None] = mapped_column(String(32))
    source_resource_id: Mapped[str | None] = mapped_column(String(128))
    logical_call_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    request_capture_status: Mapped[str | None] = mapped_column(String(32))
    response_capture_status: Mapped[str | None] = mapped_column(String(32))
    request_original_bytes: Mapped[int | None] = mapped_column(BIGINT)
    response_original_bytes: Mapped[int | None] = mapped_column(BIGINT)
    request_stored_bytes: Mapped[int | None] = mapped_column(BIGINT)
    response_stored_bytes: Mapped[int | None] = mapped_column(BIGINT)
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    capture_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemLogEvent(Base):
    __tablename__ = "system_log_events"
    __table_args__ = (
        CheckConstraint(
            "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_system_log_events_level",
        ),
        Index("ix_system_log_events_happened", "happened_at"),
        Index("ix_system_log_events_service_level", "service", "level", "happened_at"),
        Index("ix_system_log_events_match", "match_id", "happened_at"),
        Index("ix_system_log_events_format_version", "format_version_id", "happened_at"),
        Index("ix_system_log_events_request_trace", "request_id", "trace_id", "happened_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    logger_name: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    format_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("format_versions.id", ondelete="SET NULL")
    )
    match_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    speech_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    generation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_round_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    connection_epoch: Mapped[int | None] = mapped_column(BIGINT)
    incident_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemIncident(Base):
    __tablename__ = "system_incidents"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('WARNING', 'ERROR', 'CRITICAL')", name="ck_system_incidents_severity"
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name="ck_system_incidents_status"
        ),
        UniqueConstraint("fingerprint", name="uq_system_incidents_fingerprint"),
        Index("ix_system_incidents_status_seen", "status", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OPEN")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    affected_match_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    affected_user_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(String(2000))
    acknowledged_by_user_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentAudioAsset(Base):
    __tablename__ = "agent_audio_assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SPOOLING', 'READY', 'PLAYING', 'FINALIZED', 'FAILED', 'DISCARDED')",
            name="ck_agent_audio_assets_status",
        ),
        UniqueConstraint("generation_id", name="uq_agent_audio_assets_generation"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    generation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_generations.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    voice: Mapped[str] = mapped_column(String(128), nullable=False)
    rate: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SPOOLING")
    spool_path: Mapped[str | None] = mapped_column(String(512))
    storage_path: Mapped[str | None] = mapped_column(String(512))
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_audio_latency_ms: Mapped[int | None] = mapped_column(Integer)
    tts_completed_latency_ms: Mapped[int | None] = mapped_column(Integer)
    pcm_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentBatch(Base):
    __tablename__ = "experiment_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'DISABLED')",
            name="ck_experiment_batches_status",
        ),
        CheckConstraint("schedule_version >= 0", name="ck_experiment_batches_schedule_version"),
        CheckConstraint(
            "training_room_quota BETWEEN 1 AND 20",
            name="ck_experiment_batches_training_quota",
        ),
        UniqueConstraint("code", name="uq_experiment_batches_code"),
        Index("ix_experiment_batches_status", "status"),
        Index("ix_experiment_batches_format_version", "format_version_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    consent_version: Mapped[str] = mapped_column(String(128), nullable=False)
    consent_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    consent_document: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rule_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rules.id", ondelete="RESTRICT"), nullable=False
    )
    format_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("format_versions.id", ondelete="RESTRICT")
    )
    rule_config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    training_room_quota: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    created_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentTeam(Base):
    __tablename__ = "experiment_teams"
    __table_args__ = (
        UniqueConstraint("batch_id", "team_code", name="uq_experiment_teams_batch_code"),
        UniqueConstraint("batch_id", "agent_profile_id", name="uq_experiment_teams_batch_agent"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    team_code: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentTeamMember(Base):
    __tablename__ = "experiment_team_members"
    __table_args__ = (
        UniqueConstraint("batch_id", "user_id", name="uq_experiment_team_members_batch_user"),
        UniqueConstraint(
            "batch_id", "participant_code", name="uq_experiment_team_members_batch_code"
        ),
        UniqueConstraint("team_id", "user_id", name="uq_experiment_team_members_team_user"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    participant_code: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentExpert(Base):
    __tablename__ = "experiment_experts"
    __table_args__ = (
        UniqueConstraint("batch_id", "user_id", name="uq_experiment_experts_batch_user"),
        UniqueConstraint("batch_id", "expert_code", name="uq_experiment_experts_batch_code"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expert_code: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScheduledMatch(Base):
    __tablename__ = "scheduled_matches"
    __table_args__ = (
        CheckConstraint("round_no > 0 AND match_no > 0", name="ck_scheduled_matches_position"),
        CheckConstraint("kind IN ('TRAINING', 'FORMAL')", name="ck_scheduled_matches_kind"),
        CheckConstraint(
            "status IN ('DRAFT', 'SCHEDULED', 'RUNNING', 'PAUSED', 'COMPLETED', "
            "'CANCELLED', 'INCOMPLETE')",
            name="ck_scheduled_matches_status",
        ),
        CheckConstraint(
            "affirmative_team_id <> negative_team_id", name="ck_scheduled_matches_distinct_teams"
        ),
        UniqueConstraint("batch_id", "round_no", "match_no", name="uq_scheduled_matches_slot"),
        Index("ix_scheduled_matches_batch_status", "batch_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    match_no: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="RESTRICT"), nullable=False
    )
    affirmative_team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    negative_team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="FORMAL")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_attempt_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "experiment_match_attempts.id",
            name="fk_scheduled_matches_effective_attempt",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScheduledSeat(Base):
    __tablename__ = "scheduled_seats"
    __table_args__ = (
        CheckConstraint("side IN ('AFFIRMATIVE', 'NEGATIVE')", name="ck_scheduled_seats_side"),
        CheckConstraint("seat_no BETWEEN 1 AND 4", name="ck_scheduled_seats_seat_no"),
        CheckConstraint(
            "occupant_kind IN ('HUMAN', 'AGENT')", name="ck_scheduled_seats_occupant_kind"
        ),
        CheckConstraint(
            "(occupant_kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(occupant_kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_scheduled_seats_occupant_reference",
        ),
        UniqueConstraint(
            "scheduled_match_id", "side", "seat_no", name="uq_scheduled_seats_position"
        ),
        UniqueConstraint("scheduled_match_id", "user_id", name="uq_scheduled_seats_user"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    scheduled_match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("scheduled_matches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    occupant_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="RESTRICT")
    )


class ExperimentMatchAttempt(Base):
    __tablename__ = "experiment_match_attempts"
    __table_args__ = (
        CheckConstraint("attempt_no > 0", name="ck_experiment_match_attempts_attempt_no"),
        CheckConstraint(
            "status IN ('CREATED', 'WAITING', 'RUNNING', 'PAUSED', 'COMPLETED', "
            "'TERMINATED', 'INCOMPLETE')",
            name="ck_experiment_match_attempts_status",
        ),
        UniqueConstraint(
            "scheduled_match_id", "attempt_no", name="uq_experiment_match_attempts_number"
        ),
        UniqueConstraint("room_id", name="uq_experiment_match_attempts_room"),
        UniqueConstraint("match_id", name="uq_experiment_match_attempts_match"),
        Index("ix_experiment_match_attempts_schedule_status", "scheduled_match_id", "status"),
        Index(
            "uq_experiment_match_attempts_active",
            "scheduled_match_id",
            unique=True,
            postgresql_where=text("status IN ('CREATED', 'WAITING', 'RUNNING', 'PAUSED')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    scheduled_match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("scheduled_matches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    room_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("rooms.id", ondelete="RESTRICT")
    )
    match_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="CREATED")
    termination_reason: Mapped[str | None] = mapped_column(Text)
    public_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentConsent(Base):
    __tablename__ = "experiment_consents"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "user_id", "consent_version", name="uq_experiment_consents_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    consent_version: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FreeDebateOpportunity(Base):
    __tablename__ = "free_debate_opportunities"
    __table_args__ = (
        CheckConstraint("side IN ('AFFIRMATIVE', 'NEGATIVE')", name="ck_opportunities_side"),
        CheckConstraint(
            "trigger_kind IN ('INITIAL_HOST', 'HUMAN_SPEECH', 'AGENT_SPEECH', "
            "'SAME_SIDE_CONTINUATION')",
            name="ck_opportunities_trigger_kind",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'INVALIDATED')", name="ck_opportunities_status"
        ),
        CheckConstraint(
            "selection_phase IN ('COMPETING', 'HUMAN_ONLY_WAIT', 'ALLOCATED')",
            name="ck_opportunities_selection_phase",
        ),
        CheckConstraint(
            "decision_fact IN ('PENDING', 'RAISE', 'SKIP', 'TECHNICAL_MISSING')",
            name="ck_opportunities_decision_fact",
        ),
        CheckConstraint(
            "allocation_fact IN ('PENDING', 'AI_SELECTED', 'HUMAN_SELECTED', "
            "'WAITING_FOR_HUMAN', 'PAUSED_WITHOUT_SPEAKER')",
            name="ck_opportunities_allocation_fact",
        ),
        CheckConstraint(
            "execution_fact IN ('PENDING', 'AI_SPOKE', 'HUMAN_SPOKE', 'NO_SPEECH')",
            name="ck_opportunities_execution_fact",
        ),
        CheckConstraint("sequence_no > 0", name="ck_opportunities_sequence"),
        CheckConstraint("opportunity_generation > 0", name="ck_opportunities_generation"),
        UniqueConstraint("match_id", "sequence_no", name="uq_opportunities_match_sequence"),
        Index("ix_opportunities_attempt_sequence", "experiment_attempt_id", "sequence_no"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    experiment_attempt_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_match_attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_speech_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("speeches.id", ondelete="SET NULL")
    )
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    opportunity_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    selection_phase: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="COMPETING"
    )
    decision_fact: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PENDING")
    allocation_fact: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="PENDING"
    )
    execution_fact: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="PENDING"
    )
    frozen_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selection_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    human_wait_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_reason: Mapped[str | None] = mapped_column(String(128))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HumanHandEvent(Base):
    __tablename__ = "human_hand_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('RAISE', 'CANCEL')", name="ck_human_hand_events_type"),
        CheckConstraint("server_sequence > 0", name="ck_human_hand_events_sequence"),
        UniqueConstraint("opportunity_id", "server_sequence", name="uq_human_hand_events_sequence"),
        Index("ix_human_hand_events_opportunity_time", "opportunity_id", "accepted_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    opportunity_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    server_sequence: Mapped[int] = mapped_column(BIGINT, nullable=False)
    connection_epoch: Mapped[int | None] = mapped_column(BIGINT)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SpeakerAllocation(Base):
    __tablename__ = "speaker_allocations"
    __table_args__ = (
        CheckConstraint("speaker_kind IN ('HUMAN', 'AGENT')", name="ck_speaker_allocations_kind"),
        CheckConstraint(
            "(speaker_kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(speaker_kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_speaker_allocations_reference",
        ),
        Index(
            "uq_speaker_allocations_effective",
            "opportunity_id",
            unique=True,
            postgresql_where=text("effective = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    opportunity_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    speaker_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_profiles.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    effective: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ParticipantAnnotationTask(Base):
    __tablename__ = "participant_annotation_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_participant_annotation_tasks_status",
        ),
        UniqueConstraint(
            "experiment_attempt_id", "user_id", name="uq_participant_annotation_tasks_user"
        ),
        Index("ix_participant_annotation_tasks_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_attempt_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_match_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    questionnaire_version: Mapped[str] = mapped_column(String(64), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    late: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParticipantAnnotationItem(Base):
    __tablename__ = "participant_annotation_items"
    __table_args__ = (
        CheckConstraint(
            "subject_kind IN ('HUMAN_SELF', 'TEAM_AI')",
            name="ck_participant_annotation_items_subject",
        ),
        CheckConstraint("position > 0", name="ck_participant_annotation_items_position"),
        UniqueConstraint("task_id", "position", name="uq_participant_annotation_items_position"),
        UniqueConstraint(
            "task_id",
            "opportunity_id",
            "subject_kind",
            name="uq_participant_annotation_items_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("participant_annotation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    speech_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("speeches.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    stage1_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParticipantAnnotationAnswer(Base):
    __tablename__ = "participant_annotation_answers"
    __table_args__ = (
        CheckConstraint("stage IN (1, 2)", name="ck_participant_annotation_answers_stage"),
        UniqueConstraint(
            "task_id",
            "opportunity_id",
            "subject_kind",
            "stage",
            "question_key",
            name="uq_participant_annotation_answers_question",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("participant_annotation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    question_key: Mapped[str] = mapped_column(String(64), nullable=False)
    answer: Mapped[Any] = mapped_column(JSONB, nullable=False)
    response_duration_ms: Mapped[int | None] = mapped_column(Integer)
    audio_play_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    client_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MatchQuestionnaire(Base):
    __tablename__ = "match_questionnaires"
    __table_args__ = (
        CheckConstraint(
            "q1 BETWEEN 1 AND 5 AND q2 BETWEEN 1 AND 5 AND q3 BETWEEN 1 AND 5 AND "
            "q4 BETWEEN 1 AND 5 AND q5 BETWEEN 1 AND 5",
            name="ck_match_questionnaires_scales",
        ),
        UniqueConstraint("task_id", name="uq_match_questionnaires_task"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("participant_annotation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    q1: Mapped[int] = mapped_column(Integer, nullable=False)
    q2: Mapped[int] = mapped_column(Integer, nullable=False)
    q3: Mapped[int] = mapped_column(Integer, nullable=False)
    q4: Mapped[int] = mapped_column(Integer, nullable=False)
    q5: Mapped[int] = mapped_column(Integer, nullable=False)
    q6: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PostmatchSurveyTask(Base):
    __tablename__ = "postmatch_survey_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_postmatch_survey_tasks_status",
        ),
        UniqueConstraint("match_id", "user_id", name="uq_postmatch_survey_tasks_match_user"),
        Index("ix_postmatch_survey_tasks_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    questionnaire_version: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="postmatch-v1"
    )
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalAiSurveyVersion(Base):
    __tablename__ = "personal_ai_survey_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_personal_ai_survey_versions_status",
        ),
        UniqueConstraint("version", name="uq_personal_ai_survey_versions_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalAiSurveyResponse(Base):
    __tablename__ = "personal_ai_survey_responses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED')",
            name="ck_personal_ai_survey_responses_status",
        ),
        UniqueConstraint(
            "user_id", "version_id", name="uq_personal_ai_survey_responses_user_version"
        ),
        Index("ix_personal_ai_survey_responses_user", "user_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("personal_ai_survey_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalAiSurveyResponseRevision(Base):
    __tablename__ = "personal_ai_survey_response_revisions"
    __table_args__ = (Index("ix_personal_ai_survey_response_revisions_response", "response_id"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    response_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("personal_ai_survey_responses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("personal_ai_survey_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExpertAnnotationTask(Base):
    __tablename__ = "expert_annotation_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_expert_annotation_tasks_status",
        ),
        UniqueConstraint("batch_id", "expert_user_id", name="uq_expert_annotation_tasks_user"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    expert_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExpertAnnotationAnswer(Base):
    __tablename__ = "expert_annotation_answers"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "opportunity_id", name="uq_expert_annotation_answers_opportunity"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("expert_annotation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("free_debate_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    frozen_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    q1: Mapped[str | None] = mapped_column(String(32))
    q2: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    q3: Mapped[str | None] = mapped_column(String(32))
    client_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentResultOverride(Base):
    __tablename__ = "experiment_result_overrides"
    __table_args__ = (
        UniqueConstraint("experiment_attempt_id", name="uq_experiment_result_overrides_attempt"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_attempt_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiment_match_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    published_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "AgentProfile",
    "AgentAudioAsset",
    "AgentGeneration",
    "AgentFreeDebateDecision",
    "AsrSegment",
    "AuditLog",
    "BackgroundTask",
    "BulkJob",
    "BulkJobItem",
    "Base",
    "CapacityGuard",
    "DeviceCheck",
    "BrowserDeviceCheck",
    "ExperimentBatch",
    "ExperimentConsent",
    "ExperimentExpert",
    "ExperimentMatchAttempt",
    "ExperimentResultOverride",
    "ExperimentTeam",
    "ExperimentTeamMember",
    "ExpertAnnotationAnswer",
    "ExpertAnnotationTask",
    "FreeDebateOpportunity",
    "HostAudioAsset",
    "HumanHandEvent",
    "ModelProfile",
    "Match",
    "MatchFile",
    "MatchExport",
    "MatchExportItem",
    "MatchEvent",
    "MatchQuestionnaire",
    "ParticipantAnnotationAnswer",
    "ParticipantAnnotationItem",
    "ParticipantAnnotationTask",
    "Room",
    "RoomMember",
    "RoomConnection",
    "RoomConnectionLease",
    "Rule",
    "RuleStage",
    "Seat",
    "ScheduledMatch",
    "ScheduledSeat",
    "SpeakerAllocation",
    "StageAction",
    "Speech",
    "Topic",
    "User",
    "UserConsent",
    "UserSession",
    "VoiceProfile",
    "MatchParticipant",
    "TranscriptSubmission",
    "JudgeProfile",
    "JudgeResult",
    "LeaderboardSnapshot",
    "SystemLogEvent",
    "SystemIncident",
    "PostmatchSurveyTask",
    "PersonalAiSurveyVersion",
    "PersonalAiSurveyResponse",
    "PersonalAiSurveyResponseRevision",
]
