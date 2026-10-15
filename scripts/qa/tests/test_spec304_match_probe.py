from __future__ import annotations

import importlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from test_repeat_mixed_4v4_probe import finished_receipt as finished_receipt


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(  # pyright: ignore[reportUnknownMemberType]
        str(Path("scripts/qa/probes").resolve())
    )
    return importlib.import_module("server_spec304_match_probe")


def scenario_receipt(finished: dict[str, Any], scenario: str) -> dict[str, Any]:
    value = deepcopy(finished)
    value["pending_media_starts_after_close"] = 0
    value["conference_host_fixture"] = {
        "status": "passed",
        "provider_voice": "longanwen_v3.1",
        "host_name": "龙风月瑶",
        "all_assets_frozen": True,
        "host_reserved_absent_from_agents": True,
        "normal_agent_voices": 8,
        "fixture_version": "spec304-conference-host-fixture-v2",
        "original_first_host_prompt_preserved": True,
        "original_first_host_prompt_present": True,
        "welcome_preface_ms": 6000,
        "welcome_whole_asset_decoded_ms": 12_000,
        "welcome_preface_measurement": {
            "actual_provider": True,
            "temporary_audio_removed": True,
        },
        "closing_decoded_ms": 6000,
    }
    proof: dict[str, Any] = {
        "scenario": scenario,
        "status": "passed",
        "media_observation": {
            "status": "passed",
            "violations": [],
            "overflow_count": 0,
            "synthesize_entries": 8,
            "actual_pcm_samples": 48000,
        },
    }
    if scenario == "timeout":
        proof.update(
            natural_time_limit_count=1,
            speech_id=str(uuid4()),
            continuous_pcm_until_authoritative_deadline=True,
        )
    if scenario == "recovery":
        proof.update(
            speech_reset_count=1,
            stage_reset_count=1,
            placeholder_count=1,
            placeholder_provider_succeeded=True,
            placeholder_tts_calls=0,
            tts_window={
                "exact_placeholder_tts_calls": 0,
                "late_metadata_explained": True,
                "global_count_delta_after_ack": 0,
            },
            reset_excluded_from_transcript=True,
            reset_excluded_from_agent_history=True,
            reset_excluded_from_replay=True,
            placeholder_excluded_from_judge_performance=True,
            placeholder_labelled_in_agent_history=True,
            fourth_speakers_spoken={"AFFIRMATIVE": 6000, "NEGATIVE": 6000},
            operations=[
                {
                    "action": action,
                    "operation_id": str(uuid4()),
                    "before_sequence": i * 10,
                    "after_sequence": i * 10 + 1,
                    "natural_resume_ms": 3000,
                    "target_seat_no": 3,
                }
                for i, action in enumerate(("reset_speech", "reset_stage", "skip_speech"), 1)
            ],
            reset_context_windows=[
                {
                    "action": action,
                    "speech_id": str(uuid4()),
                    "committed_at": datetime(2026, 10, 15, 12, i, tzinfo=UTC).isoformat(),
                    "event_sequence": i * 10,
                    "successful_calls_checked": 1,
                }
                for i, action in enumerate(("reset_speech", "reset_stage"), 1)
            ],
        )
    value["spec304_scenario"] = proof
    return value


@pytest.mark.parametrize("scenario", ["normal", "timeout", "recovery"])
def test_accepts_only_completed_and_clean_scenario(
    probe: Any, finished_receipt: Any, scenario: str
):
    value = scenario_receipt(finished_receipt, scenario)
    assert probe.strict_scenario(value, scenario)
    value["new_pending_tasks_after_cleanup"] = 1
    assert not probe.strict_scenario(value, scenario)


@pytest.mark.parametrize(
    "key",
    [
        "speech_reset_count",
        "stage_reset_count",
        "placeholder_count",
        "placeholder_provider_succeeded",
        "placeholder_tts_calls",
        "reset_excluded_from_transcript",
        "reset_excluded_from_agent_history",
        "reset_excluded_from_replay",
        "placeholder_excluded_from_judge_performance",
        "placeholder_labelled_in_agent_history",
        "operations",
        "reset_context_windows",
        "fourth_speakers_spoken",
    ],
)
def test_missing_recovery_proof_cannot_pass(probe: Any, finished_receipt: Any, key: str):
    value = scenario_receipt(finished_receipt, "recovery")
    value["spec304_scenario"].pop(key)
    assert not probe.strict_scenario(value, "recovery")


@pytest.mark.parametrize(
    "key", ["natural_time_limit_count", "speech_id", "continuous_pcm_until_authoritative_deadline"]
)
def test_timeout_needs_actual_target_and_continuous_pcm(
    probe: Any, finished_receipt: Any, key: str
):
    value = scenario_receipt(finished_receipt, "timeout")
    value["spec304_scenario"].pop(key)
    assert not probe.strict_scenario(value, "timeout")


@pytest.mark.parametrize(
    "key",
    [
        "provider_voice",
        "host_name",
        "all_assets_frozen",
        "normal_agent_voices",
        "host_reserved_absent_from_agents",
        "fixture_version",
        "original_first_host_prompt_preserved",
        "original_first_host_prompt_present",
        "welcome_preface_ms",
        "welcome_whole_asset_decoded_ms",
        "welcome_preface_measurement",
        "closing_decoded_ms",
    ],
)
def test_host_frozen_identity_and_actual_boundaries_required(
    probe: Any, finished_receipt: Any, key: str
):
    value = scenario_receipt(finished_receipt, "normal")
    value["conference_host_fixture"].pop(key)
    assert not probe.strict_scenario(value, "normal")


@pytest.mark.parametrize(
    "mutation",
    ["early_resume", "early_speech_resume", "same_operation", "no_commit", "wrong_action"],
)
def test_operations_need_distinct_commits_and_natural_resumes(
    probe: Any, finished_receipt: Any, mutation: str
):
    value = scenario_receipt(finished_receipt, "recovery")
    rows = value["spec304_scenario"]["operations"]
    if mutation == "early_resume":
        rows[1]["natural_resume_ms"] = 2899
    elif mutation == "early_speech_resume":
        rows[0]["natural_resume_ms"] = 2899
    elif mutation == "same_operation":
        rows[1]["operation_id"] = rows[0]["operation_id"]
    elif mutation == "no_commit":
        rows[1]["after_sequence"] = rows[1]["before_sequence"]
    else:
        rows[1]["action"] = "reset_speech"
    assert not probe.strict_scenario(value, "recovery")


def test_bounded_capture_inspection_finds_actual_json_context(probe: Any):
    ctx = {"history": [{"speech_id": "owned-speech", "origin": "ADMIN_PLACEHOLDER"}]}
    raw = {"body": {"messages": [{"role": "user", "content": json.dumps(ctx)}]}}
    assert ctx in probe.nested_contexts(raw)
    assert probe.nested_contexts("{broken") == []
    assert probe.nested_contexts("{" + "x" * 256_000) == []


@pytest.mark.parametrize("when", [-1, 0, 1, 10, 11])
@pytest.mark.parametrize("present", ["old-first", "old-second", "unrelated"])
def test_each_request_excludes_only_speeches_already_reset(probe: Any, when: int, present: str):
    start = datetime(2026, 10, 15, 12, tzinfo=UTC)
    resets = [
        {"speech_id": "old-first", "committed_at": start},
        {"speech_id": "old-second", "committed_at": start + timedelta(seconds=10)},
    ]
    ctx = [{"history": [{"speech_id": present}]}]
    expected = not (
        (present == "old-first" and when >= 0) or (present == "old-second" and when >= 10)
    )
    assert probe.reset_history_valid(ctx, start + timedelta(seconds=when), resets) == expected


@pytest.mark.parametrize("missing", ["committed_at", "speech_id"])
def test_reset_history_needs_persisted_target_and_timestamp(probe: Any, missing: str):
    now = datetime.now(UTC)
    reset = {"speech_id": "old", "committed_at": now}
    reset.pop(missing)
    assert not probe.reset_history_valid([{"history": []}], now, [reset])


@pytest.mark.parametrize(
    "mutation", ["missing", "wrong_order", "first_unchecked", "last_unchecked"]
)
def test_both_reset_windows_need_successful_context_evidence(
    probe: Any, finished_receipt: Any, mutation: str
):
    value = scenario_receipt(finished_receipt, "recovery")
    rows = value["spec304_scenario"]["reset_context_windows"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "wrong_order":
        rows.reverse()
    else:
        rows[0 if mutation == "first_unchecked" else 1]["successful_calls_checked"] = 0
    assert not probe.strict_scenario(value, "recovery")


@pytest.mark.asyncio
@pytest.mark.parametrize("migrated_admin", [False, True])
async def test_owned_prepare_reuses_unique_admin_without_changing_roles(
    probe: Any, migrated_admin: bool
):
    url = "postgresql+psycopg://jx@127.0.0.1:54321/jx_debate_qa_agent_faults"
    current: list[Any] = [uuid4()] if migrated_admin else []
    inserted: list[Any] = []

    class Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def scalar(self, query: Any) -> Any:
            assert "users.role" in str(query) and "ADMIN" in query.compile().params.values()
            return current[0] if current else None

        def add(self, row: Any) -> None:
            assert not current and row.role == "ADMIN"
            inserted.append(row)

        async def commit(self) -> None:
            assert not current and len(inserted) == 1
            current.append(inserted[0].id)

    class Factory:
        kw = {"bind": SimpleNamespace(url=make_url(url))}

        def __call__(self) -> Session:
            return Session()

    factory, owned = Factory(), SimpleNamespace(port=54321)
    first, source = await probe.owned_admin(factory, owned, url)
    repeated, repeated_source = await probe.owned_admin(factory, owned, url)
    assert first == repeated == current[0]
    assert source == ("existing_admin" if migrated_admin else "created_synthetic_admin")
    assert repeated_source == "existing_admin"
    assert len(inserted) == (0 if migrated_admin else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["port", "database", "host", "factory"])
async def test_admin_fixture_cannot_open_any_unowned_database(probe: Any, mismatch: str):
    url = "postgresql+psycopg://jx@127.0.0.1:54321/jx_debate_qa_agent_faults"
    binding = make_url(url)
    if mismatch == "port":
        candidate = binding.set(port=54322)
    elif mismatch == "database":
        candidate = binding.set(database="production")
    elif mismatch == "host":
        candidate = binding.set(host="remote.invalid")
    else:
        candidate = binding

    class Factory:
        kw = {"bind": SimpleNamespace(url=make_url(url).set(database="wrong"))}

        def __call__(self) -> None:
            raise AssertionError("must reject before opening any database session")

    factory = Factory()
    if mismatch != "factory":
        factory.kw = {"bind": SimpleNamespace(url=binding)}
    with pytest.raises(RuntimeError, match="qa_admin_database_not_owned"):
        await probe.owned_admin(factory, SimpleNamespace(port=54321), str(candidate))


@pytest.mark.parametrize(
    "mutation",
    [
        "success",
        "unrelated_failure",
        "unknown_generation",
        "wrong_match",
        "missing_blob",
        "missing_raw_capture",
        "unavailable_for_capacity",
        "complete_raw",
        "raw_url",
        "raw_body",
        "raw_transport",
        "observed_request",
        "no_capture_owner",
        "pcm",
        "speech_started",
        "no_completed_at",
        "first_result",
        "first_latency",
        "wrong_operation",
        "wrong_generation_binding",
        "no_observer",
        "no_operation_id",
    ],
)
def test_presend_exception_requires_exact_observed_retirement_and_raw_receipt(
    probe: Any, mutation: str
):
    match_id, generation_id = uuid4(), uuid4()
    call = SimpleNamespace(
        call_kind="LLM_SPEECH",
        operation="chat.completions.stream",
        status="FAILED",
        error_code="agent_cancelled",
        completed_at=datetime.now(UTC),
        first_result_at=None,
        first_result_latency_ms=None,
        generation_id=generation_id,
        agent_generation_id=generation_id,
        match_id=match_id,
    )
    projection: dict[str, Any] = {
        "request_blob_id": uuid4(),
        "request_capture_status": "UNAVAILABLE",
    }
    raw: dict[str, Any] = {
        "capture": {"status": "UNAVAILABLE", "error_code": "provider_request_unavailable"},
        "body": None,
        "url": None,
        "transport": None,
    }
    rows: list[dict[str, Any]] = [
        {
            "match_id": str(match_id),
            "generation_id": str(generation_id),
            "capture_present": True,
            "request_captured": False,
            "speech_id": None,
            "played_samples": 0,
            "source": "actual_cancel_run_entry",
            "operation_id": str(uuid4()),
        }
    ]
    assert probe.explicit_presend_retirement(call, projection, raw, rows)
    if mutation == "success":
        call.status = "SUCCEEDED"
    elif mutation == "unrelated_failure":
        call.error_code = "llm_first_token_timeout"
    elif mutation == "unknown_generation":
        rows[0]["generation_id"] = str(uuid4())
    elif mutation == "wrong_match":
        rows[0]["match_id"] = str(uuid4())
    elif mutation == "missing_blob":
        projection["request_blob_id"] = None
    elif mutation == "missing_raw_capture":
        projection["request_capture_status"] = "capture_not_persisted"
    elif mutation == "unavailable_for_capacity":
        raw["capture"]["error_code"] = "capture_capacity_exhausted"
    elif mutation == "complete_raw":
        raw["capture"]["status"] = "COMPLETE"
    elif mutation == "raw_url":
        raw["url"] = "https://synthetic.invalid/v1/chat/completions"
    elif mutation == "raw_body":
        raw["body"] = {"messages": []}
    elif mutation == "raw_transport":
        raw["transport"] = "SSE"
    elif mutation == "observed_request":
        rows[0]["request_captured"] = True
    elif mutation == "no_capture_owner":
        rows[0]["capture_present"] = False
    elif mutation == "pcm":
        rows[0]["played_samples"] = 480
    elif mutation == "speech_started":
        rows[0]["speech_id"] = str(uuid4())
    elif mutation == "no_completed_at":
        call.completed_at = None
    elif mutation == "first_result":
        call.first_result_at = datetime.now(UTC)
    elif mutation == "first_latency":
        call.first_result_latency_ms = 0
    elif mutation == "wrong_operation":
        call.operation = "admin.speech.placeholder"
    elif mutation == "wrong_generation_binding":
        call.agent_generation_id = uuid4()
    elif mutation == "no_observer":
        rows = []
    elif mutation == "no_operation_id":
        rows[0]["operation_id"] = None
    assert not probe.explicit_presend_retirement(call, projection, raw, rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_operation", [False, True])
async def test_cancel_observer_preserves_original_call_and_only_records_exact_operation(
    probe: Any, monkeypatch: Any, current_operation: bool
):
    operation_id, match_id, generation_id, agent_id = (uuid4() for _ in range(4))
    original = AsyncMock()
    agents = SimpleNamespace(
        _cancel_run=original,
        _execute=AsyncMock(),
        _play_host_audio=AsyncMock(),
        _connect_publisher=AsyncMock(),
    )
    operation = {
        "id": str(operation_id),
        "kind": "speech.skip",
        "action_key": "1:1",
        "agent_profile_id": str(agent_id),
    }
    actor = SimpleNamespace(state=SimpleNamespace(emergency_operation=operation))

    def set_terminal_callback(_: Any) -> None:
        pass

    manager = SimpleNamespace(
        _actors={match_id: actor}, set_terminal_callback=set_terminal_callback
    )
    close = AsyncMock()

    def coordinator(**_: Any) -> Any:
        return SimpleNamespace(cancel=AsyncMock(), close=close)

    monkeypatch.setattr(
        probe,
        "MatchEmergencyCoordinator",
        coordinator,
    )
    driver = probe.ScenarioDriver("recovery", None, None, {})
    await driver.bind(manager=manager, agents=agents, limiter=None, writer=None)
    driver.active_operation_id = str(operation_id if current_operation else uuid4())
    run = SimpleNamespace(
        match_id=match_id,
        generation_id=generation_id,
        agent_profile_id=agent_id,
        action_key="1:1",
        speech_id=None,
        played_samples=0,
        llm_capture=SimpleNamespace(request=None),
    )
    await agents._cancel_run(run)
    original.assert_awaited_once_with(run)
    assert len(driver.retirements) == int(current_operation)
    if current_operation:
        assert driver.retirements[0]["generation_id"] == str(generation_id)
        assert driver.retirements[0]["capture_present"] is True
        assert driver.retirements[0]["request_captured"] is False
    await driver.close()
    driver.after_close()
    assert agents._cancel_run is original
    close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("natural", [False, True])
async def test_microphone_action_uses_finish_only_when_requested(probe: Any, natural: bool):
    actor = SimpleNamespace(
        state=SimpleNamespace(
            current_speaker_user_id=uuid4(),
            current_action=SimpleNamespace(action_key="1:1"),
            current_speech_id=None,
            action_state="HUMAN_READY_TO_START",
            status="RUNNING",
        ),
        view=lambda: SimpleNamespace(speech_remaining_ms=1000),
    )
    commands: list[str] = []

    async def submit(_: Any, command: Any):
        commands.append(command.type)
        actor.state.current_speech_id = uuid4() if command.type == "speech.start" else None
        actor.state.action_state = (
            "HUMAN_SPEAKING" if command.type == "speech.start" else "SPEECH_FINALIZING"
        )

    actor.state.match_id = uuid4()
    source = SimpleNamespace(clear_queue=lambda: None, wait_for_playout=AsyncMock())

    async def capture(_: Any):
        if natural:
            # Only a synthetic control for this tool test. The real probe
            # never invokes product timer or provider callbacks itself.
            actor.state.current_speech_id = None
            actor.state.action_state = "SPEECH_FINALIZING"

    source.capture_frame = capture
    await probe.full.speak(
        SimpleNamespace(submit=submit), actor, source, bytes(3200), finish_when_done=not natural
    )
    assert commands == (["speech.start"] if natural else ["speech.start", "speech.finish"])


def test_candidate_migration_is_scoped_to_owned_database(
    probe: Any, tmp_path: Path, monkeypatch: Any
):
    owned = probe.full.OwnedPostgres()
    owned.port = 54321
    (tmp_path / "alembic.ini").write_text("[alembic]\n")
    calls: list[tuple[Any, Any, Any]] = []

    def run(args: Any, **kwargs: Any):
        calls.append((args, kwargs["cwd"], kwargs["env"]["DATABASE_URL"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(probe.full.subprocess, "run", run)
    url = "postgresql+psycopg://jx_qa@127.0.0.1:54321/jx_debate_qa_agent_faults"
    try:
        owned.migrate(url, repo_root=tmp_path)
        assert calls[0][0][4] == str(tmp_path / "alembic.ini")
        assert calls[0][1] == owned.root and calls[0][2] == url
        with pytest.raises(RuntimeError, match="qa_migration_database_not_owned"):
            owned.migrate("postgresql+psycopg://synthetic.invalid/production", repo_root=tmp_path)
        assert len(calls) == 1
    finally:
        owned.close()
