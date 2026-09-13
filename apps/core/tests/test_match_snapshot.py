from __future__ import annotations

from uuid import uuid4

from jx_core.matches.domain import MatchAction, MatchRuntimeState
from jx_core.matches.service import _state_from_snapshot, state_snapshot

# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false


def test_runtime_snapshot_round_trips_independent_offline_markers() -> None:
    first = uuid4()
    second = uuid4()
    state = MatchRuntimeState(
        match_id=uuid4(),
        status="RUNNING",
        action_state="FREE_SELECTING",
        actions=(),
        offline_user_id=second,
        offline_since_ms=((first, 1000), (second, 2000)),
        connection_epochs=((first, 3), (second, 4)),
        interim_text="正在发言中的字幕",
    )

    restored = _state_from_snapshot(state.match_id, state_snapshot(state))

    assert dict(restored.offline_since_ms) == {first: 1000, second: 2000}
    assert dict(restored.connection_epochs) == {first: 3, second: 4}
    assert restored.offline_user_id == second
    assert restored.interim_text == "正在发言中的字幕"


def test_runtime_snapshot_round_trips_timer_and_transient_state_matrix() -> None:
    speech_id = uuid4()
    speaker_id = uuid4()
    agent_id = uuid4()
    action = MatchAction(
        stage_position=2,
        action_position=3,
        action_kind="HUMAN_SPEECH",
        duration_seconds=45,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=speaker_id,
        host_audio_path="rules/intro.ogg",
        host_audio_duration_ms=8_500,
    )
    state = MatchRuntimeState(
        match_id=uuid4(),
        status="PAUSED",
        action_state="RECOVERY_REQUIRED",
        actions=(action,),
        current_action_index=0,
        current_speech_id=speech_id,
        current_speaker_user_id=speaker_id,
        current_agent_profile_id=agent_id,
        interim_text="恢复前的临时字幕",
        speech_remaining_ms=12_345,
        host_audio_remaining_ms=6_789,
        host_audio_deadline_ms=None,
        paused_from_status="RUNNING",
        paused_from_action_state="HOST_ANNOUNCING",
        connection_epochs=((speaker_id, 9),),
    )

    restored = _state_from_snapshot(state.match_id, state_snapshot(state))

    assert restored.current_action == action
    assert restored.current_speech_id == speech_id
    assert restored.interim_text == state.interim_text
    assert restored.speech_remaining_ms == state.speech_remaining_ms
    assert restored.host_audio_remaining_ms == state.host_audio_remaining_ms
    assert restored.host_audio_deadline_ms is None
    assert restored.paused_from_action_state == "HOST_ANNOUNCING"
    assert dict(restored.connection_epochs) == {speaker_id: 9}
