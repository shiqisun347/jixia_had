from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_spec304_match_probe import finished_receipt as finished_receipt
from test_spec304_match_probe import probe as probe
from test_spec304_match_probe import scenario_receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["normal", "paused_start", "raw_rejected", "late_pcm", "cleanup_pcm"]
)
async def test_actual_media_wrappers_forward_and_distinguish_arrival_from_playback(
    probe: Any, monkeypatch: Any, scenario: str
):
    match_id, generation_id = uuid4(), uuid4()
    state = SimpleNamespace(
        status="RUNNING",
        action_state="AGENT_PREPARING",
        emergency_operation={"id": "owned-operation"},
    )
    actor = SimpleNamespace(state=state)
    run = SimpleNamespace(
        match_id=match_id,
        generation_id=generation_id,
        action_key="1:1",
        media_fenced=False,
        played_samples=0,
    )
    acks: dict[str, float] = {}
    original_frame, original_audio = AsyncMock(), AsyncMock()
    source = SimpleNamespace(capture_frame=original_frame)
    frame = SimpleNamespace(samples_per_channel=480)

    async def audio(_: bytes) -> None:
        if scenario == "raw_rejected":
            raise asyncio.CancelledError
        await original_audio(_)

    async def synthesize(_: Any, chunks: Any, **values: Any) -> Any:
        assert chunks == "owned-chunks"
        if scenario == "raw_rejected":
            state.status, run.media_fenced = "PAUSED", True
            acks["owned-operation"] = 1.0
        await values["on_audio"](b"owned-synthetic-chunk")
        return "original-result"

    monkeypatch.setattr(probe.QwenTtsConnection, "synthesize", synthesize)

    async def connect(*_: Any, **__: Any) -> Any:
        return "original-room", source

    async def execute(owned_run: Any, _: Any, owned_generation: Any) -> None:
        assert owned_run is run and owned_generation == generation_id
        room, actual_source = await agents._connect_publisher(match_id)
        assert room == "original-room" and actual_source is source
        connection = SimpleNamespace()
        task = asyncio.create_task(
            probe.QwenTtsConnection.synthesize(connection, "owned-chunks", on_audio=audio),
            name=f"agent-tts-{generation_id}",
        )
        assert await task == "original-result"
        if scenario in {"late_pcm", "cleanup_pcm"}:
            state.status = "PAUSED"
            if scenario == "late_pcm":
                acks["owned-operation"] = 1.0
        await source.capture_frame(frame)

    agents = SimpleNamespace(
        _execute=execute, _play_host_audio=AsyncMock(), _connect_publisher=connect
    )
    manager = SimpleNamespace(_actors={match_id: actor})
    observer = probe.MediaObservation(manager, agents, acks)
    observer.install()
    if scenario == "paused_start":
        state.status = "PAUSED"
        acks["owned-operation"] = 1.0
    try:
        if scenario == "raw_rejected":
            with pytest.raises(asyncio.CancelledError):
                await agents._execute(run, "original-config", generation_id)
        else:
            await agents._execute(run, "original-config", generation_id)
        receipt = observer.receipt()
        count = receipt["audio_by_generation"][str(generation_id)]
        assert count["raw_chunks"] == 1 and count["raw_bytes"] == len(b"owned-synthetic-chunk")
        if scenario == "raw_rejected":
            assert count["rejected_chunks"] == count["raw_chunks_while_paused"] == 1
            assert count["accepted_bytes"] == count["pcm_samples"] == 0
            original_frame.assert_not_awaited()
        else:
            original_frame.assert_awaited_once_with(frame)
            original_audio.assert_awaited_once_with(b"owned-synthetic-chunk")
            assert count["pcm_samples"] == 480
        assert receipt["status"] == (
            "failed" if scenario in {"paused_start", "late_pcm"} else "passed"
        )
        if scenario == "cleanup_pcm":
            assert count["pcm_frames_during_cleanup"] == 1
        assert "owned-synthetic-chunk" not in str(receipt)
    finally:
        observer.restore()
    assert agents._execute is execute and agents._connect_publisher is connect
    assert source.capture_frame is original_frame
    assert probe.QwenTtsConnection.synthesize is synthesize


@pytest.mark.asyncio
async def test_unknown_media_owner_and_observation_overflow_are_failed_evidence(
    probe: Any, monkeypatch: Any
):
    synthesize = AsyncMock(return_value="forwarded")
    monkeypatch.setattr(probe.QwenTtsConnection, "synthesize", synthesize)
    agents = SimpleNamespace(
        _execute=AsyncMock(), _play_host_audio=AsyncMock(), _connect_publisher=AsyncMock()
    )
    observer = probe.MediaObservation(SimpleNamespace(_actors={}), agents, {})
    observer.install()
    try:
        assert (
            await probe.QwenTtsConnection.synthesize(
                SimpleNamespace(), "chunks", on_audio=AsyncMock()
            )
            == "forwarded"
        )
        assert observer.receipt()["status"] == "failed"
        for _ in range(255):
            observer.record({"kind": "bounded-example"})
        with pytest.raises(RuntimeError, match="qa_media_observation_capacity_exhausted"):
            observer.record({"kind": "overflow"})
        assert observer.receipt()["overflow_count"] == 1
        assert observer.receipt()["observed_event_count"] == 256
    finally:
        observer.restore()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_stage_reset_can_follow_speech_reset_on_same_action_only_with_fresh_speech(
    probe: Any, replacement: bool
):
    original_id, fresh_id = uuid4(), uuid4()

    class Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def get(self, *_: Any) -> Any:
            return SimpleNamespace(status="RESET", display_text="owned synthetic draft")

    driver = probe.ScenarioDriver("recovery", Session, None, {})
    driver.phase, driver.reset_action_index, driver.reset_ids = 1, 4, [original_id]
    state = SimpleNamespace(
        current_action=SimpleNamespace(action_kind="AGENT_SPEECH", seat_no=3),
        current_action_index=4,
        action_state="AGENT_SPEAKING",
        current_speech_id=fresh_id if replacement else original_id,
        status="RUNNING",
        interim_text="",
    )
    actor = SimpleNamespace(state=state)

    async def reset(_: Any, action: str) -> Any:
        assert action == "reset_stage"
        state.current_speech_id, state.status = None, "PAUSED"
        return {}

    driver.admin = AsyncMock(side_effect=reset)
    driver.resume = AsyncMock()
    assert await driver.tick(actor, None, None) is replacement
    if replacement:
        driver.admin.assert_awaited_once_with(actor, "reset_stage")
        driver.resume.assert_awaited_once_with(actor)
        assert driver.reset_ids == [original_id, fresh_id]
    else:
        driver.admin.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_skip_refuses_fourth_speaker_before_database_or_provider_use(probe: Any):
    driver = probe.ScenarioDriver("recovery", None, None, {})
    driver.phase, driver.reset_action_index = 2, 4
    actor = SimpleNamespace(
        state=SimpleNamespace(
            current_action=SimpleNamespace(action_kind="AGENT_SPEECH", seat_no=4),
            current_action_index=7,
            action_state="AGENT_PREPARING",
        )
    )
    driver.admin = AsyncMock()
    with pytest.raises(AssertionError, match="qa_skip_must_preserve_both_fourth_speakers"):
        await driver.tick(actor, None, None)
    driver.admin.assert_not_awaited()


@pytest.mark.parametrize(
    "mutation", ["none", "unknown_generation", "new_started", "new_synthesis", "paused_synthesis"]
)
def test_global_tts_growth_needs_exact_retired_metadata_and_zero_new_synthesis(
    probe: Any, mutation: str
):
    now = datetime.now(UTC)
    generation_id = str(uuid4())
    rows = [
        {"generation_id": generation_id, "started_at": (now - timedelta(seconds=1)).isoformat()}
    ]
    retired = [{"generation_id": generation_id}]
    entries: list[dict[str, Any]] = []
    if mutation == "unknown_generation":
        retired = [{"generation_id": str(uuid4())}]
    elif mutation == "new_started":
        rows[0]["started_at"] = (now + timedelta(seconds=1)).isoformat()
    elif mutation in {"new_synthesis", "paused_synthesis"}:
        entries = [
            {
                "kind": "synthesize",
                "generation_id": generation_id,
                "at_mono": 101 if mutation == "new_synthesis" else 99,
                "actor_status": "RUNNING" if mutation == "new_synthesis" else "PAUSED",
            }
        ]
    assert probe.late_tts_metadata_only(rows, retired, entries, now, 100) == (mutation == "none")


@pytest.mark.parametrize(
    "mutation",
    [
        "media_failed",
        "unseen_synthesis",
        "no_pcm",
        "pending_start",
        "unexplained_delta",
        "placeholder_tts",
        "missing_delta",
    ],
)
def test_complete_match_cannot_hide_missing_or_failed_media_evidence(
    probe: Any, finished_receipt: Any, mutation: str
):
    value = scenario_receipt(finished_receipt, "recovery")
    media = value["spec304_scenario"]["media_observation"]
    window = value["spec304_scenario"]["tts_window"]
    if mutation == "media_failed":
        media["status"] = "failed"
    elif mutation == "unseen_synthesis":
        media["synthesize_entries"] = 0
    elif mutation == "no_pcm":
        media["actual_pcm_samples"] = 0
    elif mutation == "pending_start":
        value["pending_media_starts_after_close"] = 1
    elif mutation == "unexplained_delta":
        window["global_count_delta_after_ack"] = 1
        window["late_metadata_explained"] = False
    elif mutation == "placeholder_tts":
        window["exact_placeholder_tts_calls"] = 1
    else:
        window.pop("global_count_delta_after_ack")
    assert not probe.strict_scenario(value, "recovery")
