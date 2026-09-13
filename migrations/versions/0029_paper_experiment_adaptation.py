"""Add isolated paper-experiment scheduling, runtime facts, and annotations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_paper_experiment_adaptation"
down_revision: str | None = "0028_connection_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "experiment_batches",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), server_default="DRAFT", nullable=False),
        sa.Column("schedule_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consent_version", sa.String(128), nullable=False),
        sa.Column("consent_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("consent_document", sa.Text(), server_default="", nullable=False),
        sa.Column("rule_id", _uuid(), nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'DISABLED')",
            name="ck_experiment_batches_status",
        ),
        sa.CheckConstraint("schedule_version >= 0", name="ck_experiment_batches_schedule_version"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_experiment_batches_code"),
    )
    op.create_index("ix_experiment_batches_status", "experiment_batches", ["status"])

    op.create_table(
        "experiment_teams",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("team_code", sa.String(16), nullable=False),
        sa.Column("agent_profile_id", _uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["experiment_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "team_code", name="uq_experiment_teams_batch_code"),
        sa.UniqueConstraint("batch_id", "agent_profile_id", name="uq_experiment_teams_batch_agent"),
    )
    op.create_table(
        "experiment_team_members",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("team_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("participant_code", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["experiment_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["team_id"], ["experiment_teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "user_id", name="uq_experiment_team_members_batch_user"),
        sa.UniqueConstraint(
            "batch_id", "participant_code", name="uq_experiment_team_members_batch_code"
        ),
        sa.UniqueConstraint("team_id", "user_id", name="uq_experiment_team_members_team_user"),
    )
    op.create_table(
        "experiment_experts",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("expert_code", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["experiment_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "user_id", name="uq_experiment_experts_batch_user"),
        sa.UniqueConstraint("batch_id", "expert_code", name="uq_experiment_experts_batch_code"),
    )

    op.create_table(
        "scheduled_matches",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("match_no", sa.Integer(), nullable=False),
        sa.Column("topic_id", _uuid(), nullable=False),
        sa.Column("affirmative_team_id", _uuid(), nullable=False),
        sa.Column("negative_team_id", _uuid(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("kind", sa.String(16), server_default="FORMAL", nullable=False),
        sa.Column("status", sa.String(16), server_default="DRAFT", nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("effective_attempt_id", _uuid()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("round_no > 0 AND match_no > 0", name="ck_scheduled_matches_position"),
        sa.CheckConstraint("kind IN ('TRAINING', 'FORMAL')", name="ck_scheduled_matches_kind"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SCHEDULED', 'RUNNING', 'PAUSED', 'COMPLETED', "
            "'CANCELLED', 'INCOMPLETE')",
            name="ck_scheduled_matches_status",
        ),
        sa.CheckConstraint(
            "affirmative_team_id <> negative_team_id", name="ck_scheduled_matches_distinct_teams"
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["experiment_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["affirmative_team_id"], ["experiment_teams.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["negative_team_id"], ["experiment_teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "round_no", "match_no", name="uq_scheduled_matches_slot"),
    )
    op.create_index(
        "ix_scheduled_matches_batch_status", "scheduled_matches", ["batch_id", "status"]
    )
    op.create_table(
        "scheduled_seats",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("scheduled_match_id", _uuid(), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("seat_no", sa.Integer(), nullable=False),
        sa.Column("occupant_kind", sa.String(16), nullable=False),
        sa.Column("user_id", _uuid()),
        sa.Column("agent_profile_id", _uuid()),
        sa.CheckConstraint("side IN ('AFFIRMATIVE', 'NEGATIVE')", name="ck_scheduled_seats_side"),
        sa.CheckConstraint("seat_no BETWEEN 1 AND 4", name="ck_scheduled_seats_seat_no"),
        sa.CheckConstraint(
            "occupant_kind IN ('HUMAN', 'AGENT')", name="ck_scheduled_seats_occupant_kind"
        ),
        sa.CheckConstraint(
            "(occupant_kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(occupant_kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_scheduled_seats_occupant_reference",
        ),
        sa.CheckConstraint(
            "NOT (seat_no = 1 AND occupant_kind = 'AGENT')",
            name="ck_scheduled_seats_agent_not_first",
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_match_id"], ["scheduled_matches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scheduled_match_id", "side", "seat_no", name="uq_scheduled_seats_position"
        ),
        sa.UniqueConstraint("scheduled_match_id", "user_id", name="uq_scheduled_seats_user"),
    )
    op.create_table(
        "experiment_match_attempts",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("scheduled_match_id", _uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("room_id", _uuid()),
        sa.Column("match_id", _uuid()),
        sa.Column("status", sa.String(16), server_default="CREATED", nullable=False),
        sa.Column("termination_reason", sa.Text()),
        sa.Column("public_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("attempt_no > 0", name="ck_experiment_match_attempts_attempt_no"),
        sa.CheckConstraint(
            "status IN ('CREATED', 'WAITING', 'RUNNING', 'PAUSED', 'COMPLETED', "
            "'TERMINATED', 'INCOMPLETE')",
            name="ck_experiment_match_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_match_id"], ["scheduled_matches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scheduled_match_id", "attempt_no", name="uq_experiment_match_attempts_number"
        ),
        sa.UniqueConstraint("room_id", name="uq_experiment_match_attempts_room"),
        sa.UniqueConstraint("match_id", name="uq_experiment_match_attempts_match"),
    )
    op.create_index(
        "ix_experiment_match_attempts_schedule_status",
        "experiment_match_attempts",
        ["scheduled_match_id", "status"],
    )
    op.create_index(
        "uq_experiment_match_attempts_active",
        "experiment_match_attempts",
        ["scheduled_match_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED', 'WAITING', 'RUNNING', 'PAUSED')"),
    )
    op.create_foreign_key(
        "fk_scheduled_matches_effective_attempt",
        "scheduled_matches",
        "experiment_match_attempts",
        ["effective_attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "experiment_consents",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("consent_version", sa.String(128), nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["experiment_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id", "user_id", "consent_version", name="uq_experiment_consents_version"
        ),
    )

    op.create_table(
        "free_debate_opportunities",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("match_id", _uuid(), nullable=False),
        sa.Column("experiment_attempt_id", _uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("source_speech_id", _uuid()),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("opportunity_generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("selection_phase", sa.String(32), server_default="COMPETING", nullable=False),
        sa.Column("decision_fact", sa.String(32), server_default="PENDING", nullable=False),
        sa.Column("allocation_fact", sa.String(32), server_default="PENDING", nullable=False),
        sa.Column("execution_fact", sa.String(32), server_default="PENDING", nullable=False),
        sa.Column(
            "frozen_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("source_ended_at", sa.DateTime(timezone=True)),
        sa.Column("selection_deadline_at", sa.DateTime(timezone=True)),
        sa.Column("human_wait_deadline_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_reason", sa.String(128)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("side IN ('AFFIRMATIVE', 'NEGATIVE')", name="ck_opportunities_side"),
        sa.CheckConstraint(
            "trigger_kind IN ('INITIAL_HOST', 'HUMAN_SPEECH', 'AGENT_SPEECH', "
            "'SAME_SIDE_CONTINUATION')",
            name="ck_opportunities_trigger_kind",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'INVALIDATED')", name="ck_opportunities_status"
        ),
        sa.CheckConstraint(
            "selection_phase IN ('COMPETING', 'HUMAN_ONLY_WAIT', 'ALLOCATED')",
            name="ck_opportunities_selection_phase",
        ),
        sa.CheckConstraint(
            "decision_fact IN ('PENDING', 'RAISE', 'SKIP', 'TECHNICAL_MISSING')",
            name="ck_opportunities_decision_fact",
        ),
        sa.CheckConstraint(
            "allocation_fact IN ('PENDING', 'AI_SELECTED', 'HUMAN_SELECTED', "
            "'WAITING_FOR_HUMAN', 'PAUSED_WITHOUT_SPEAKER')",
            name="ck_opportunities_allocation_fact",
        ),
        sa.CheckConstraint(
            "execution_fact IN ('PENDING', 'AI_SPOKE', 'HUMAN_SPOKE', 'NO_SPEECH')",
            name="ck_opportunities_execution_fact",
        ),
        sa.CheckConstraint("sequence_no > 0", name="ck_opportunities_sequence"),
        sa.CheckConstraint("opportunity_generation > 0", name="ck_opportunities_generation"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["experiment_attempt_id"], ["experiment_match_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_speech_id"], ["speeches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "sequence_no", name="uq_opportunities_match_sequence"),
    )
    op.create_index(
        "ix_opportunities_attempt_sequence",
        "free_debate_opportunities",
        ["experiment_attempt_id", "sequence_no"],
    )
    op.create_table(
        "human_hand_events",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("opportunity_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("server_sequence", sa.BigInteger(), nullable=False),
        sa.Column("connection_epoch", sa.BigInteger()),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("event_type IN ('RAISE', 'CANCEL')", name="ck_human_hand_events_type"),
        sa.CheckConstraint("server_sequence > 0", name="ck_human_hand_events_sequence"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["free_debate_opportunities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "opportunity_id", "server_sequence", name="uq_human_hand_events_sequence"
        ),
    )
    op.create_index(
        "ix_human_hand_events_opportunity_time",
        "human_hand_events",
        ["opportunity_id", "accepted_at"],
    )
    op.create_table(
        "speaker_allocations",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("opportunity_id", _uuid(), nullable=False),
        sa.Column("speaker_kind", sa.String(16), nullable=False),
        sa.Column("user_id", _uuid()),
        sa.Column("agent_profile_id", _uuid()),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("effective", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "allocated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "speaker_kind IN ('HUMAN', 'AGENT')", name="ck_speaker_allocations_kind"
        ),
        sa.CheckConstraint(
            "(speaker_kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(speaker_kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_speaker_allocations_reference",
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["free_debate_opportunities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_speaker_allocations_effective",
        "speaker_allocations",
        ["opportunity_id"],
        unique=True,
        postgresql_where=sa.text("effective = true"),
    )

    op.create_table(
        "participant_annotation_tasks",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("experiment_attempt_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("questionnaire_version", sa.String(64), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("late", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_participant_annotation_tasks_status",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_attempt_id"], ["experiment_match_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_attempt_id", "user_id", name="uq_participant_annotation_tasks_user"
        ),
    )
    op.create_index(
        "ix_participant_annotation_tasks_user_status",
        "participant_annotation_tasks",
        ["user_id", "status"],
    )
    op.create_table(
        "participant_annotation_items",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("opportunity_id", _uuid(), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("speech_id", _uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("stage1_locked_at", sa.DateTime(timezone=True)),
        sa.Column("revealed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "subject_kind IN ('HUMAN_SELF', 'TEAM_AI')",
            name="ck_participant_annotation_items_subject",
        ),
        sa.CheckConstraint("position > 0", name="ck_participant_annotation_items_position"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["participant_annotation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["free_debate_opportunities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["speech_id"], ["speeches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "position", name="uq_participant_annotation_items_position"),
        sa.UniqueConstraint(
            "task_id",
            "opportunity_id",
            "subject_kind",
            name="uq_participant_annotation_items_target",
        ),
    )
    op.create_table(
        "participant_annotation_answers",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("opportunity_id", _uuid(), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("question_key", sa.String(64), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_duration_ms", sa.Integer()),
        sa.Column("audio_play_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("client_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "saved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("stage IN (1, 2)", name="ck_participant_annotation_answers_stage"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["participant_annotation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["free_debate_opportunities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "opportunity_id",
            "subject_kind",
            "stage",
            "question_key",
            name="uq_participant_annotation_answers_question",
        ),
    )
    op.create_table(
        "match_questionnaires",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("q1", sa.Integer(), nullable=False),
        sa.Column("q2", sa.Integer(), nullable=False),
        sa.Column("q3", sa.Integer(), nullable=False),
        sa.Column("q4", sa.Integer(), nullable=False),
        sa.Column("q5", sa.Integer(), nullable=False),
        sa.Column("q6", sa.Text()),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "q1 BETWEEN 1 AND 5 AND q2 BETWEEN 1 AND 5 AND q3 BETWEEN 1 AND 5 AND "
            "q4 BETWEEN 1 AND 5 AND q5 BETWEEN 1 AND 5",
            name="ck_match_questionnaires_scales",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["participant_annotation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_match_questionnaires_task"),
    )
    op.create_table(
        "expert_annotation_tasks",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("expert_user_id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_expert_annotation_tasks_status",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["experiment_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["expert_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "expert_user_id", name="uq_expert_annotation_tasks_user"),
    )
    op.create_table(
        "expert_annotation_answers",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("opportunity_id", _uuid(), nullable=False),
        sa.Column("frozen_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("q1", sa.String(32)),
        sa.Column("q2", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("q3", sa.String(32)),
        sa.Column("client_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "saved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["task_id"], ["expert_annotation_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["free_debate_opportunities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "opportunity_id", name="uq_expert_annotation_answers_opportunity"
        ),
    )
    op.create_table(
        "experiment_result_overrides",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("experiment_attempt_id", _uuid(), nullable=False),
        sa.Column("published_by", _uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["experiment_attempt_id"], ["experiment_match_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_attempt_id", name="uq_experiment_result_overrides_attempt"),
    )

    op.add_column("agent_free_debate_decisions", sa.Column("opportunity_id", _uuid()))
    op.add_column("agent_free_debate_decisions", sa.Column("effective_status", sa.String(32)))
    op.add_column(
        "agent_free_debate_decisions", sa.Column("deadline_at", sa.DateTime(timezone=True))
    )
    op.add_column("agent_free_debate_decisions", sa.Column("trigger_kind", sa.String(32)))
    op.add_column(
        "agent_free_debate_decisions",
        sa.Column("late", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "agent_free_debate_decisions",
        sa.Column("stale", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("agent_free_debate_decisions", sa.Column("invalidated_reason", sa.String(128)))
    op.add_column(
        "agent_free_debate_decisions",
        sa.Column(
            "human_hand_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_agent_free_debate_decisions_opportunity",
        "agent_free_debate_decisions",
        "free_debate_opportunities",
        ["opportunity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("external_calls", sa.Column("opportunity_id", _uuid()))
    op.create_foreign_key(
        "fk_external_calls_opportunity",
        "external_calls",
        "free_debate_opportunities",
        ["opportunity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("speeches", sa.Column("opportunity_id", _uuid()))
    op.create_foreign_key(
        "fk_speeches_opportunity",
        "speeches",
        "free_debate_opportunities",
        ["opportunity_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP', 'MATCH_EXPORT', 'EXPERIMENT_POSTMATCH', "
        "'EXPERIMENT_RESULT_PUBLISH', 'EXPERIMENT_BATCH_EXPORT', 'EXPERIMENT_RETENTION')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP', 'MATCH_EXPORT')",
    )
    op.drop_constraint("fk_external_calls_opportunity", "external_calls", type_="foreignkey")
    op.drop_column("external_calls", "opportunity_id")
    op.drop_constraint("fk_speeches_opportunity", "speeches", type_="foreignkey")
    op.drop_column("speeches", "opportunity_id")
    op.drop_constraint(
        "fk_agent_free_debate_decisions_opportunity",
        "agent_free_debate_decisions",
        type_="foreignkey",
    )
    for column in (
        "human_hand_snapshot",
        "invalidated_reason",
        "stale",
        "late",
        "trigger_kind",
        "deadline_at",
        "effective_status",
        "opportunity_id",
    ):
        op.drop_column("agent_free_debate_decisions", column)

    op.drop_table("experiment_result_overrides")
    op.drop_table("expert_annotation_answers")
    op.drop_table("expert_annotation_tasks")
    op.drop_table("match_questionnaires")
    op.drop_table("participant_annotation_answers")
    op.drop_table("participant_annotation_items")
    op.drop_index(
        "ix_participant_annotation_tasks_user_status",
        table_name="participant_annotation_tasks",
    )
    op.drop_table("participant_annotation_tasks")
    op.drop_index("uq_speaker_allocations_effective", table_name="speaker_allocations")
    op.drop_table("speaker_allocations")
    op.drop_index("ix_human_hand_events_opportunity_time", table_name="human_hand_events")
    op.drop_table("human_hand_events")
    op.drop_index("ix_opportunities_attempt_sequence", table_name="free_debate_opportunities")
    op.drop_table("free_debate_opportunities")
    op.drop_table("experiment_consents")
    op.drop_constraint(
        "fk_scheduled_matches_effective_attempt", "scheduled_matches", type_="foreignkey"
    )
    op.drop_index("uq_experiment_match_attempts_active", table_name="experiment_match_attempts")
    op.drop_index(
        "ix_experiment_match_attempts_schedule_status",
        table_name="experiment_match_attempts",
    )
    op.drop_table("experiment_match_attempts")
    op.drop_table("scheduled_seats")
    op.drop_index("ix_scheduled_matches_batch_status", table_name="scheduled_matches")
    op.drop_table("scheduled_matches")
    op.drop_table("experiment_experts")
    op.drop_table("experiment_team_members")
    op.drop_table("experiment_teams")
    op.drop_index("ix_experiment_batches_status", table_name="experiment_batches")
    op.drop_table("experiment_batches")
