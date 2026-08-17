from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from jx_core.models import Base


def test_metadata_contains_approved_003_and_004_catalog_tables() -> None:
    assert set(Base.metadata.tables) == {
        "agent_profiles",
        "agent_audio_assets",
        "agent_generations",
        "agent_free_debate_decisions",
        "call_content_blobs",
        "call_content_blob_chunks",
        "external_calls",
        "system_log_events",
        "asr_segments",
        "background_tasks",
        "capacity_guards",
        "device_checks",
        "host_audio_assets",
        "judge_profiles",
        "judge_results",
        "leaderboard_snapshots",
        "match_participants",
        "model_profiles",
        "match_events",
        "match_files",
        "matches",
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
        "stage_actions",
        "speeches",
        "rules",
        "topics",
        "transcript_submissions",
        "voice_profiles",
        "seat_swap_requests",
        "match_exports",
        "match_export_items",
        "system_incidents",
        "bulk_jobs",
        "bulk_job_items",
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
