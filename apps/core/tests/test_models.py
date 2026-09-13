from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from jx_core.models import Base


def test_metadata_contains_approved_003_and_004_catalog_tables() -> None:
    assert set(Base.metadata.tables) == {
        "agent_profiles",
        "agent_prompt_overrides",
        "agent_audio_assets",
        "agent_generations",
        "agent_free_debate_decisions",
        "call_content_blobs",
        "call_content_blob_chunks",
        "external_calls",
        "system_log_events",
        "asr_segments",
            "background_tasks",
            "browser_device_checks",
            "capacity_guards",
        "device_checks",
        "experiment_batches",
        "experiment_consents",
        "experiment_experts",
        "experiment_match_attempts",
        "experiment_result_overrides",
        "experiment_team_members",
        "experiment_teams",
        "expert_annotation_answers",
        "expert_annotation_tasks",
        "free_debate_opportunities",
        "format_versions",
        "format_modules",
        "format_judge_profiles",
        "format_topic_references",
        "host_audio_assets",
        "human_hand_events",
        "judge_profiles",
        "judge_results",
        "leaderboard_snapshots",
        "match_participants",
        "model_profiles",
        "match_events",
        "match_files",
        "match_questionnaires",
        "matches",
        "participant_annotation_answers",
        "participant_annotation_items",
        "participant_annotation_tasks",
        "prompt_templates",
        "users",
        "sessions",
        "user_consents",
        "room_connections",
        "room_connection_leases",
        "audit_logs",
        "rooms",
        "room_members",
        "seats",
        "rule_stages",
        "rule_judge_configs",
        "stage_actions",
        "stage_prompt_templates",
        "speeches",
        "rules",
        "scheduled_matches",
        "scheduled_seats",
        "topics",
        "transcript_submissions",
        "voice_profiles",
        "seat_swap_requests",
        "match_exports",
        "match_export_items",
        "system_incidents",
        "system_settings",
        "bulk_jobs",
        "bulk_job_items",
        "speaker_allocations",
        "postmatch_survey_tasks",
        "personal_ai_survey_versions",
        "personal_ai_survey_responses",
        "personal_ai_survey_response_revisions",
    }


def test_data_capture_constraints_are_present() -> None:
    blobs = Base.metadata.tables["call_content_blobs"]
    calls = Base.metadata.tables["external_calls"]
    logs = Base.metadata.tables["system_log_events"]
    generations = Base.metadata.tables["agent_generations"]
    judges = Base.metadata.tables["judge_results"]

    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"sha256", "serialization_version", "content_kind"}
        for constraint in blobs.constraints
    )
    assert "ck_external_calls_kind" in {item.name for item in calls.constraints}
    assert "ck_external_calls_status" in {item.name for item in calls.constraints}
    assert "ck_system_log_events_level" in {item.name for item in logs.constraints}
    assert {"request_blob_id", "response_blob_id", "capture_version"}.issubset(
        generations.columns.keys()
    )
    assert {"request_blob_id", "response_blob_id", "capture_version"}.issubset(
        judges.columns.keys()
    )


def test_users_constraints_protect_identity_and_state() -> None:
    table = Base.metadata.tables["users"]
    constraints = {constraint.name for constraint in table.constraints}

    assert "uq_users_username_normalized" in constraints
    assert {
        "ck_users_username_length",
        "ck_users_username_normalized_length",
        "ck_users_real_name_length",
        "ck_users_role",
        "ck_users_status",
        "ck_users_failed_login_count_nonnegative",
        "ck_users_avatar_version_nonnegative",
    }.issubset(constraints)


def test_voice_profiles_track_calibration_state() -> None:
    table = Base.metadata.tables["voice_profiles"]
    constraints = {constraint.name for constraint in table.constraints}
    assert "ck_voice_profiles_calibration_status" in constraints
    assert {"calibration_status", "calibrated_at"}.issubset(table.columns.keys())


def test_format_workspace_uses_partial_uniqueness_and_room_snapshots() -> None:
    versions = Base.metadata.tables["format_versions"]
    prompts = Base.metadata.tables["prompt_templates"]
    rooms = Base.metadata.tables["rooms"]
    assert {"ux_format_versions_one_draft", "ux_format_versions_one_published"}.issubset(
        {index.name for index in versions.indexes}
    )
    assert {"ux_prompt_templates_default_slot", "ux_prompt_templates_agent_slot"}.issubset(
        {index.name for index in prompts.indexes}
    )
    assert {"format_version_id", "format_snapshot"}.issubset(rooms.columns.keys())


def test_rule_owned_agent_pool_tables_are_registered() -> None:
    rules = Base.metadata.tables["rules"]
    agents = Base.metadata.tables["agent_profiles"]
    assert {
        "host_voice_profile_id",
        "default_agent_model_profile_id",
        "topic_policy",
        "config_revision",
        "historical_read_only",
    }.issubset(rules.columns.keys())
    assert "rule_id" in agents.columns
    assert {"ix_agent_profiles_rule_id", "ux_agent_profiles_rule_voice"}.issubset(
        {index.name for index in agents.indexes}
    )


def test_session_and_connection_uniqueness_constraints_are_present() -> None:
    sessions = Base.metadata.tables["sessions"]
    connections = Base.metadata.tables["room_connections"]

    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"token_hash"}
        for constraint in sessions.constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"connection_id"}
        for constraint in connections.constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_room_connections_epoch_positive"
        for constraint in connections.constraints
    )


def test_room_and_rule_constraints_are_present() -> None:
    rules = Base.metadata.tables["rules"]
    rooms = Base.metadata.tables["rooms"]
    seats = Base.metadata.tables["seats"]
    assert any(constraint.name == "ck_rules_side_size" for constraint in rules.constraints)
    assert any(constraint.name == "ck_rules_status" for constraint in rules.constraints)
    assert any(constraint.name == "uq_rooms_code" for constraint in rooms.constraints)
    assert any(constraint.name == "ck_seats_occupant_reference" for constraint in seats.constraints)
    assert "auto_fill_agents" in rooms.columns
    assert "configured_agent_profile_id" in seats.columns
    assert {"source_text", "cedar_id"}.issubset(Base.metadata.tables["topics"].columns.keys())


def test_experiment_constraints_protect_schedule_and_runtime_facts() -> None:
    attempts = Base.metadata.tables["experiment_match_attempts"]
    scheduled_seats = Base.metadata.tables["scheduled_seats"]
    opportunities = Base.metadata.tables["free_debate_opportunities"]
    allocations = Base.metadata.tables["speaker_allocations"]

    attempt_index = next(
        item for item in attempts.indexes if item.name == "uq_experiment_match_attempts_active"
    )
    allocation_index = next(
        item for item in allocations.indexes if item.name == "uq_speaker_allocations_effective"
    )
    assert attempt_index.unique is True
    assert attempt_index.dialect_options["postgresql"]["where"] is not None
    assert allocation_index.unique is True
    assert "ck_scheduled_seats_occupant_reference" in {
        item.name for item in scheduled_seats.constraints
    }
    assert "ck_scheduled_seats_agent_not_first" not in {
        item.name for item in scheduled_seats.constraints
    }
    assert {
        "ck_opportunities_decision_fact",
        "ck_opportunities_allocation_fact",
        "ck_opportunities_execution_fact",
    }.issubset({item.name for item in opportunities.constraints})
