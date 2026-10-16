"""Spec304 real Actor/Manager/PG boundaries; provider and media are explicit stubs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from jx_core.matches.domain import DebateParticipant, MatchAction, MatchCommand, MatchDomainError
from jx_core.models import Match, MatchEvent, MatchParticipant, Speech
from test_agent_runtime_fault_integration import (
    AgentFaultProbe,
)
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import (
    agent_fault_probe as agent_fault_probe,
)
from test_match_emergency_domain import operator_payload

pytestmark = pytest.mark.integration


async def prepare(probe: AgentFaultProbe, *, fixed: bool = False) -> tuple[Any, Any, Any, Any]:
    async with probe.factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(MatchParticipant).where(
                        MatchParticipant.match_id == probe.actor.state.match_id
                    )
                )
            ).all()
        )
    users = {(row.side, row.seat_no): row.user_id for row in rows if row.kind == "HUMAN"}
    first, second = users[("AFFIRMATIVE", 1)], users[("NEGATIVE", 1)]
    if fixed:
        actions = tuple(
            MatchAction(
                stage_position=index,
                action_position=1,
                action_kind="HUMAN_SPEECH",
                duration_seconds=10,
                side=side,
                seat_no=1,
                speaker_user_id=user,
            )
            for index, (side, user) in enumerate((("AFFIRMATIVE", first), ("NEGATIVE", second)), 1)
        )
    else:
        actions = (
            MatchAction(
                stage_position=7,
                action_position=0,
                action_kind="FREE_DEBATE",
                duration_seconds=60,
                free_max_speech_seconds=10,
                participants=tuple(
                    DebateParticipant(
                        side=row.side,
                        seat_no=row.seat_no,
                        user_id=row.user_id,
                        agent_profile_id=row.agent_profile_id,
                    )
                    for row in rows
                ),
            ),
        )
    now = [100.0]
    actor = probe.actor
    actor._clock = lambda: now[0]
    actor._sleep = lambda _: asyncio.sleep(60)
    actor.state = replace(
        actor.state,
        status="START_PENDING_RUNTIME",
        action_state="NOT_STARTED",
        actions=actions,
        current_speech_id=None,
        current_agent_profile_id=None,
        current_speaker_user_id=None,
        current_speaker_side=None,
        current_speaker_seat_no=None,
    )
    media = AsyncMock()
    envelopes = {}

    async def start(_match_id, speech_id, _user_id, envelope, **_kwargs):
        envelopes[speech_id] = envelope

    media.start_speech.side_effect = start
    media.speech_callback_envelope = lambda _match_id, speech_id: envelopes.get(speech_id)
    media.recovery_source_envelope = lambda _match_id, speech_id: envelopes.get(speech_id)
    media.recovery_source_available = lambda _match_id, speech_id: speech_id in envelopes
    media.commit_speech_start = lambda _match_id, _speech_id, **_kwargs: None
    media.fence_speech_input = lambda _match_id, _speech_id: None
    media.finalization_callback_is_current = lambda *, envelope: (
        envelopes.get(envelope.speech_id) is envelope
    )
    probe.manager.set_speech_runtime(media)
    probe.manager.set_agent_runtime(media)
    for kind in ("runtime.start", "countdown.elapsed"):
        await probe.manager.submit(
            actor.state.match_id, MatchCommand(type=kind, message_id=str(uuid4()))
        )
    return now, first, second, envelopes


async def start_human(probe: AgentFaultProbe, user: UUID, *, free: bool = True) -> UUID | None:
    actor, manager = probe.actor, probe.manager
    if free:
        await manager.submit(
            actor.state.match_id,
            MatchCommand(type="hand.raise", message_id=str(uuid4()), actor_user_id=user),
        )
        await manager.submit(
            actor.state.match_id, MatchCommand(type="hand.window_closed", message_id=str(uuid4()))
        )
    await manager.submit(
        actor.state.match_id,
        MatchCommand(type="speech.start", message_id=str(uuid4()), actor_user_id=user),
    )
    return actor.state.current_speech_id


async def finalize(manager, envelope):
    return await manager.finalize_asr_speech(
        envelope=envelope,
        final_text="受控真人正式记录",
        first_interim_latency_ms=1,
        final_latency_ms=2,
        audio_duration_ms=1000,
        audio_storage_path=None,
        audio_recording_error=None,
    )


async def admin_auth(probe):
    from jx_core.auth.session import AuthContext
    from jx_core.models import User

    async with probe.factory() as session:
        async with session.begin():
            admin_id = await session.scalar(select(User.id).where(User.role == "ADMIN"))
            if admin_id is None:
                admin_id = uuid4()
                session.add(
                    User(
                        id=admin_id,
                        username=f"recovery-admin-{admin_id.hex[:8]}",
                        username_normalized=f"recovery-admin-{admin_id.hex[:8]}",
                        real_name="恢复测试管理员",
                        role="ADMIN",
                        password_hash="unusable-synthetic-hash",
                    )
                )
    return AuthContext(admin_id, "ADMIN", uuid4(), False)


@pytest.mark.asyncio
async def test_delayed_asr_preserves_original_owner_then_new_human_gets_fresh_identity(
    agent_fault_probe,
):
    probe = agent_fault_probe
    now, first, second, envelopes = await prepare(probe)
    old = await start_human(probe, first)
    now[0] = 110
    await probe.manager.submit(
        probe.actor.state.match_id, MatchCommand(type="speech.deadline", message_id="deadline")
    )
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(type="hand.raise", message_id="second-hand", actor_user_id=second),
    )
    now[0] = 113
    await probe.manager.submit(
        probe.actor.state.match_id, MatchCommand(type="hand.window_closed", message_id="cutoff")
    )
    assert probe.actor.state.current_speech_id is None
    assert probe.actor.state.current_speaker_user_id == second
    before = probe.actor.state
    await finalize(probe.manager, envelopes[old])
    assert probe.actor.state == before
    async with probe.factory() as session:
        source = await session.get(Speech, old)
        assert (
            source.user_id == first
            and source.side == "AFFIRMATIVE"
            and source.status == "FINALIZED"
        )
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(type="speech.start", message_id="next-start", actor_user_id=second),
    )
    new = probe.actor.state.current_speech_id
    assert new != old
    async with probe.factory() as session:
        target = await session.get(Speech, new)
        assert target.user_id == second and target.side == "NEGATIVE"
        assert target.opportunity_id != source.opportunity_id
        assert target.attempt_no == envelopes[new].attempt_no
    current = probe.actor.state
    await finalize(probe.manager, envelopes[old])
    assert probe.actor.state == current
    assert probe.actor.view().free_affirmative_remaining_ms == 50_000


@pytest.mark.asyncio
@pytest.mark.parametrize("final_delay", [0, 2.9, 3, 3.1, 10])
async def test_original_asr_final_on_both_sides_of_cutoff_never_reuses_identity(
    agent_fault_probe, final_delay
):
    probe = agent_fault_probe
    now, first, second, envelopes = await prepare(probe)
    match_id = probe.actor.state.match_id
    old = await start_human(probe, first)
    now[0] = 110
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.deadline", message_id="deadline")
    )
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.raise", message_id="hand", actor_user_id=second)
    )
    if final_delay < 3:
        now[0] = 110 + final_delay
        await finalize(probe.manager, envelopes[old])
    now[0] = 113
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id="cutoff")
    )
    if final_delay >= 3:
        now[0] = 110 + final_delay
        await finalize(probe.manager, envelopes[old])
    assert probe.actor.state.current_speech_id is None
    assert probe.actor.state.current_speaker_user_id == second
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.start", message_id="next", actor_user_id=second)
    )
    new = probe.actor.state.current_speech_id
    assert new is not None and new != old
    assert probe.actor.view().free_affirmative_remaining_ms == 50_000
    async with probe.factory() as session:
        source, target = await session.get(Speech, old), await session.get(Speech, new)
        assert source.status == "FINALIZED" and source.user_id == first
        assert target.status == "STARTED" and target.user_id == second
        assert source.opportunity_id != target.opportunity_id


@pytest.mark.asyncio
async def test_late_asr_failure_is_fenced_and_settles_only_original_speaker(agent_fault_probe):
    probe = agent_fault_probe
    now, first, second, envelopes = await prepare(probe)
    match_id = probe.actor.state.match_id
    old = await start_human(probe, first)
    now[0] = 110
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.deadline", message_id="deadline")
    )
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.raise", message_id="hand", actor_user_id=second)
    )
    now[0] = 113
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id="cutoff")
    )
    before = probe.actor.state
    envelope = envelopes[old]
    for fields in (
        {"speech_id": uuid4()},
        {"attempt_no": 2},
        {"generation_id": uuid4()},
        {"connection_epoch": 2},
        {"context_version": 1},
        {"opportunity_id": uuid4()},
        {"opportunity_generation": envelope.opportunity_generation + 1},
    ):
        await probe.manager.handle_asr_failure(
            envelope=replace(envelope, **fields), code="asr_task_failed"
        )
        assert probe.actor.state == before
        async with probe.factory() as session:
            assert (await session.get(Speech, old)).status == "FINALIZING"
    await probe.manager.handle_asr_failure(envelope=envelope, code="asr_task_failed")
    assert probe.actor.state.status == "ERROR"
    assert probe.actor.state.action_state == "RECOVERY_REQUIRED"
    assert probe.actor.state.asr_failed_speech_id == old
    assert probe.actor.state.current_speaker_user_id == second
    assert probe.actor.view().free_affirmative_remaining_ms == 50_000
    async with probe.factory() as session:
        original = await session.get(Speech, old)
        assert original.status == "FAILED" and original.asr_error_code == "asr_task_failed"
        assert original.user_id == first and original.display_text is None
        assert (await session.get(Match, match_id)).context_version == envelope.context_version
    with pytest.raises(MatchDomainError, match="match_not_running"):
        await probe.manager.submit(
            match_id,
            MatchCommand(type="speech.start", message_id="blocked-next", actor_user_id=second),
        )
    failed_state = probe.actor.state
    await probe.manager.handle_asr_failure(envelope=envelope, code="asr_stream_failed")
    assert probe.actor.state == failed_state

    async def replay(_match, _speech, captured, *, deadline_mono):
        assert captured is envelope and deadline_mono > asyncio.get_running_loop().time()
        envelopes[old] = replace(captured)
        await finalize(probe.manager, envelopes[old])

    media = probe.manager._speech_runtime
    media.retry_from_recording.side_effect = replay
    await probe.manager.submit(
        match_id,
        MatchCommand(
            "asr.retry.begin",
            "explicit-source-retry",
            payload=operator_payload(probe.actor, context_version=envelope.context_version),
        ),
    )
    async with asyncio.timeout(2):
        while probe.actor.state.action_state != "RESUME_COUNTDOWN":
            await asyncio.sleep(0.005)
    await probe.manager.submit(match_id, MatchCommand("resume.elapsed", "retry-resumed"))
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.start", message_id="next-start", actor_user_id=second)
    )
    current = probe.actor.state
    await probe.manager.handle_asr_failure(envelope=envelope, code="asr_stream_failed")
    assert probe.actor.state == current and current.current_speech_id != old
    assert probe.actor.view().free_affirmative_remaining_ms == 50_000
    async with probe.factory() as session:
        source = await session.get(Speech, old)
        assert source.status == "FINALIZED" and source.asr_error_code is None
        assert source.user_id == first and source.attempt_no == envelope.attempt_no


@pytest.mark.asyncio
async def test_admin_agent_reset_stays_paused_until_real_resume_countdown(
    agent_fault_probe, monkeypatch
):
    from time import monotonic
    from types import SimpleNamespace

    import httpx
    from starlette.requests import Request

    import jx_core.agent.runtime as runtime_module
    from jx_core.admin_routes import MatchControlRequest, control_match, match_control_target
    from jx_core.agent.llm import OpenAIStreamingClient

    probe = agent_fault_probe
    actor, match_id = probe.actor, probe.actor.state.match_id
    entered = asyncio.Event()

    async def held_provider(request):
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("owned request must be cancelled")

    def provider_client(**arguments):
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(held_provider))
        probe.http_clients.append(http_client)
        return OpenAIStreamingClient(**arguments, client=http_client)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider_client)
    await probe.runtime.start_agent(
        match_id=match_id,
        action_key=actor.state.current_action.action_key,
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    async with asyncio.timeout(3):
        await entered.wait()
    assert probe.provider_calls == 1 and actor.state.status == "RUNNING"
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(match_runtime_manager=probe.manager)),
        }
    )
    auth = await admin_auth(probe)
    async with probe.factory() as session:
        target = await match_control_target(match_id, request, auth, session)
        result = await control_match(
            match_id,
            MatchControlRequest(
                action="reset_speech",
                operation_id=uuid4(),
                expected_sequence=target.sequence,
                expected_action_key=target.action_key,
                expected_stage_generation=target.stage_generation,
                expected_speech_id=target.speech_id,
                expected_context_version=target.context_version,
            ),
            request,
            auth,
            session,
        )
    assert result["status"] == actor.state.status == "PAUSED"
    assert actor.state.paused_from_action_state == "AGENT_PREPARING"
    assert match_id not in probe.runtime._runs
    await asyncio.sleep(0.15)
    assert probe.provider_calls == 1
    started = monotonic()
    async with probe.factory() as session:
        await control_match(match_id, MatchControlRequest(action="resume"), request, auth, session)
    assert actor.state.status == "PAUSED" and actor.state.action_state == "RESUME_COUNTDOWN"
    await asyncio.sleep(2.5)
    assert probe.provider_calls == 1
    async with asyncio.timeout(3):
        while probe.provider_calls != 2:
            await asyncio.sleep(0.02)
    assert monotonic() - started >= 2.9
    assert actor.state.status == "RUNNING" and actor.state.action_state == "AGENT_PREPARING"


@pytest.mark.asyncio
async def test_failed_final_at_zero_budget_requires_explicit_reset_before_new_attempt(
    agent_fault_probe,
):
    probe = agent_fault_probe
    now, first, _, envelopes = await prepare(probe)
    match_id = probe.actor.state.match_id
    probe.actor.state = replace(
        probe.actor.state, free_affirmative_remaining_ms=10_000, free_negative_remaining_ms=0
    )
    old = await start_human(probe, first)
    now[0] = 110
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.deadline", message_id="deadline")
    )
    now[0] = 113
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id="cutoff")
    )
    assert probe.actor.state.action_state == "SPEECH_FINALIZING"
    await probe.manager.handle_asr_failure(envelope=envelopes[old], code="asr_task_failed")
    assert probe.actor.state.status == "ERROR"
    assert probe.actor.state.action_state == "RECOVERY_REQUIRED"
    assert probe.actor.state.current_speech_id == old
    assert probe.actor.state.asr_failed_speech_id == old
    assert probe.actor.view().free_affirmative_remaining_ms == 0
    await probe.manager.submit(
        match_id,
        MatchCommand(
            "match.resume",
            "ordinary-resume-is-not-asr-recovery",
            payload={"privileged": True, "reasons": []},
        ),
    )
    assert probe.actor.state.status == "ERROR" and probe.actor.state.current_speech_id == old
    async with probe.factory() as session:
        context_before_reset = (await session.get(Match, match_id)).context_version
    await probe.manager.submit(
        match_id,
        MatchCommand(
            "speech.reset",
            "explicit-first-reset",
            actor_user_id=first,
            payload=operator_payload(probe.actor, context_version=context_before_reset),
        ),
    )
    assert probe.actor.state.status == "PAUSED" and probe.actor.state.current_speech_id is None
    assert probe.actor.view().free_affirmative_remaining_ms == 10_000
    await probe.manager.submit(
        match_id,
        MatchCommand(
            "match.resume", "resume-explicit-reset", payload={"privileged": True, "reasons": []}
        ),
    )
    await probe.manager.submit(match_id, MatchCommand("resume.elapsed", "explicit-reset-resumed"))
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.start", message_id="retry", actor_user_id=first)
    )
    retry = probe.actor.state.current_speech_id
    assert retry != old
    now[0] += 10
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.deadline", message_id="retry-deadline")
    )
    now[0] += 3
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id="retry-cutoff")
    )
    await probe.manager.handle_asr_failure(envelope=envelopes[retry], code="asr_task_failed")
    assert (
        probe.actor.state.status == "ERROR"
        and probe.actor.state.action_state == "RECOVERY_REQUIRED"
    )
    async with probe.factory() as session:
        original = await session.get(Speech, old)
        assert original.status == "RESET" and original.asr_error_code == "asr_task_failed"
        assert (await session.get(Speech, retry)).status == "FAILED"
        context = (await session.get(Match, match_id)).context_version
        assert not list(
            (
                await session.scalars(
                    select(MatchEvent).where(
                        MatchEvent.match_id == match_id, MatchEvent.event_type == "match.finished"
                    )
                )
            ).all()
        )
    from types import SimpleNamespace

    from starlette.requests import Request

    from jx_core.admin_routes import MatchControlRequest, control_match, match_control_target

    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(match_runtime_manager=probe.manager)),
        }
    )
    auth = await admin_auth(probe)
    async with probe.factory() as session:
        target = await match_control_target(match_id, request, auth, session)
        assert target.can_reset_speech and target.status == "ERROR"
        assert target.context_version == context
        await control_match(
            match_id,
            MatchControlRequest(
                action="reset_speech",
                operation_id=uuid4(),
                expected_sequence=target.sequence,
                expected_action_key=target.action_key,
                expected_stage_generation=target.stage_generation,
                expected_speech_id=target.speech_id,
                expected_context_version=target.context_version,
            ),
            request,
            auth,
            session,
        )
    assert (
        probe.actor.state.status == "PAUSED"
        and probe.actor.view().free_affirmative_remaining_ms == 10_000
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [0, 4, 10])
async def test_skip_has_one_placeholder_and_charges_exact_real_time(agent_fault_probe, elapsed):
    probe = agent_fault_probe
    now, first, _, _ = await prepare(probe)
    old = await start_human(probe, first)
    now[0] += elapsed
    if elapsed == 10:
        await probe.manager.submit(
            probe.actor.state.match_id, MatchCommand(type="speech.deadline", message_id="deadline")
        )
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(
            type="speech.skip.begin",
            message_id="begin",
            actor_user_id=first,
            payload=operator_payload(probe.actor),
        ),
    )
    operation = probe.actor.state.emergency_operation
    async with probe.factory() as session:
        original = await session.get(Speech, old)
        assert original.status == "RESET"
        assert (await session.get(Match, probe.actor.state.match_id)).context_version == 1
    payload = {
        "privileged": True,
        "operation_id": operation["id"],
        "operation_generation": operation["generation"],
        "context_version": operation["context_version"],
        "text": "管理员 AI 辅助占位，未实际播报。",
    }
    for message in ("complete", "duplicate-http"):
        await probe.manager.submit(
            probe.actor.state.match_id,
            MatchCommand(
                type="speech.skip.complete",
                message_id=message,
                actor_user_id=first,
                payload=payload,
            ),
        )
    assert probe.actor.view().free_affirmative_remaining_ms == 60_000 - elapsed * 1000
    async with probe.factory() as session:
        placeholders = list(
            (
                await session.scalars(
                    select(Speech).where(
                        Speech.match_id == probe.actor.state.match_id,
                        Speech.origin == "ADMIN_PLACEHOLDER",
                    )
                )
            ).all()
        )
        assert len(placeholders) == 1
        assert placeholders[0].user_id == first and placeholders[0].audio_storage_path is None
        events = list(
            (
                await session.scalars(
                    select(MatchEvent)
                    .where(MatchEvent.match_id == probe.actor.state.match_id)
                    .order_by(MatchEvent.sequence)
                )
            ).all()
        )
        assert [event.sequence for event in events] == list(
            range(1, probe.actor.state.sequence + 1)
        )
        assert sum(event.event_type == "speech.skipped" for event in events) == 1


@pytest.mark.asyncio
async def test_stage_reset_preserves_preceding_stage_and_durable_checkpoint(agent_fault_probe):
    probe = agent_fault_probe
    _, first, second, envelopes = await prepare(probe, fixed=True)
    completed = await start_human(probe, first, free=False)
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(type="speech.finish", message_id="finish-first", actor_user_id=first),
    )
    await finalize(probe.manager, envelopes[completed])
    assert probe.actor.state.current_action.stage_position == 2
    current = await start_human(probe, second, free=False)
    async with probe.factory() as session:
        context = (await session.get(Match, probe.actor.state.match_id)).context_version
    payload = operator_payload(probe.actor, context_version=context)
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(
            type="stage.reset", message_id="reset-stage", actor_user_id=first, payload=payload
        ),
    )
    async with probe.factory() as session:
        assert (await session.get(Speech, completed)).status == "FINALIZED"
        assert (await session.get(Speech, current)).status == "RESET"
        match = await session.get(Match, probe.actor.state.match_id)
        assert match.status == "PAUSED"
        assert match.runtime_snapshot["stage_checkpoint"]["stage_position"] == 2
        assert (
            match.runtime_snapshot["stage_checkpoint"]["generation"]
            == probe.actor.state.stage_generation
        )
    await finalize(probe.manager, envelopes[current])
    async with probe.factory() as session:
        assert (await session.get(Speech, current)).status == "RESET"


@pytest.mark.asyncio
async def test_existing_speech_cannot_change_user_or_opportunity(agent_fault_probe):
    probe = agent_fault_probe
    _, first, second, _ = await prepare(probe)
    old = await start_human(probe, first)
    async with probe.factory() as session:
        original = await session.get(Speech, old)
        candidate = replace(
            probe.actor.state, current_speaker_user_id=second, current_speaker_side="NEGATIVE"
        )
        from jx_core.matches.domain import MatchEvent as Event

        event = Event(
            type="speech.started",
            match_id=candidate.match_id,
            sequence=candidate.sequence + 1,
            server_time_ms=1000,
            payload={"speech_id": str(old), "opportunity_id": str(original.opportunity_id)},
        )
        with pytest.raises(MatchDomainError, match="match_state_conflict"):
            await probe.manager._persist_started_speech(session, candidate, event)
        await session.rollback()
    async with probe.factory() as session:
        untouched = await session.get(Speech, old)
        assert untouched.user_id == first and untouched.side == "AFFIRMATIVE"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [False, True])
async def test_skip_selected_owner_before_first_pcm_preserves_budget(agent_fault_probe, agent):
    probe = agent_fault_probe
    now, first, _, envelopes = await prepare(probe)
    match_id = probe.actor.state.match_id
    if agent:
        source_id = await start_human(probe, first)
        async with probe.factory() as session:
            target = await session.scalar(
                select(MatchParticipant).where(
                    MatchParticipant.match_id == match_id,
                    MatchParticipant.side == "NEGATIVE",
                    MatchParticipant.seat_no == 3,
                )
            )
        await probe.manager.submit(
            match_id,
            MatchCommand(
                type="free.agent_decision_start",
                message_id="decision",
                payload={
                    "opportunity_id": str(probe.actor.state.opportunity_id),
                    "opportunity_generation": probe.actor.state.opportunity_generation,
                    "source_speech_id": str(probe.actor.state.current_speech_id),
                    "trigger_kind": "HUMAN_SPEECH",
                },
            ),
        )
        await probe.manager.submit(
            match_id,
            MatchCommand(
                type="free.agent_decision_result",
                message_id="agent-hand",
                payload={
                    "action_key": probe.actor.state.current_action.action_key,
                    "agent_profile_id": str(target.agent_profile_id),
                    "decision_round_id": str(probe.actor.state.agent_decision_round_id),
                    "should_speak": True,
                    "decision_reason": "受控候选",
                },
            ),
        )
        now[0] = 110
        await probe.manager.submit(
            match_id, MatchCommand(type="speech.deadline", message_id="deadline")
        )
        now[0] = 113
    else:
        await probe.manager.submit(
            match_id, MatchCommand(type="hand.raise", message_id="hand", actor_user_id=first)
        )
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id="selected")
    )
    assert probe.actor.state.action_state == (
        "AGENT_PREPARING" if agent else "HUMAN_READY_TO_START"
    )
    assert probe.actor.state.current_speech_id is None
    opportunity_id = probe.actor.state.allocated_opportunity_id or probe.actor.state.opportunity_id
    context = 0
    if agent:
        before = probe.actor.state
        with pytest.raises(MatchDomainError, match="asr_finalization_pending"):
            await probe.manager.submit(
                match_id,
                MatchCommand(
                    type="speech.skip.begin",
                    message_id="pending-source-skip",
                    payload=operator_payload(probe.actor),
                ),
            )
        assert probe.actor.state == before
        await finalize(probe.manager, envelopes[source_id])
        async with probe.factory() as session:
            assert (await session.get(Speech, source_id)).status == "FINALIZED"
            context = (await session.get(Match, match_id)).context_version
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="speech.skip.begin",
            message_id="skip",
            payload=operator_payload(probe.actor, context_version=context),
        ),
    )
    op = probe.actor.state.emergency_operation
    assert op["used_budget_ms"] == 0 and op["opportunity_id"] == str(opportunity_id)
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="speech.skip.complete",
            message_id="placeholder",
            payload={
                "privileged": True,
                "operation_id": op["id"],
                "operation_generation": op["generation"],
                "context_version": op["context_version"],
                "text": "管理员 AI 辅助占位，未实际播报。",
            },
        ),
    )
    view = probe.actor.view()
    assert (
        view.free_negative_remaining_ms if agent else view.free_affirmative_remaining_ms
    ) == 60_000
    from jx_core.models import FreeDebateOpportunity

    async with probe.factory() as session:
        slot = await session.get(FreeDebateOpportunity, opportunity_id)
        assert slot.status == "COMPLETED" and slot.execution_fact == "NO_SPEECH"


@pytest.mark.asyncio
async def test_restart_cancels_pending_skip_and_preserves_recoverable_budget(agent_fault_probe):
    from typing import cast

    from jx_core.matches.emergency import MatchEmergencyCoordinator
    from jx_core.matches.service import MatchRuntimeManager

    probe = agent_fault_probe
    now, first, _, _ = await prepare(probe)
    match_id = probe.actor.state.match_id
    await start_human(probe, first)
    now[0] += 4
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="speech.skip.begin", message_id="skip", payload=operator_payload(probe.actor)
        ),
    )
    op = dict(probe.actor.state.emergency_operation)
    async with probe.factory() as session:
        expected_recoverable = set(
            await session.scalars(
                select(Match.id).where(
                    Match.status.in_(
                        (
                            "START_PENDING_RUNTIME",
                            "START_COUNTDOWN",
                            "RUNNING",
                            "PAUSED",
                            "SYSTEM_RECOVERY",
                            "ERROR",
                        )
                    )
                )
            )
        )
        expected_pending = set(
            await session.scalars(
                select(Match.id).where(
                    Match.status.not_in(("FINISHED", "TERMINATED")),
                    Match.runtime_snapshot["emergency_operation"]["status"].astext == "PENDING",
                )
            )
        )
    assert match_id in expected_recoverable and match_id in expected_pending
    await probe.manager.close()
    manager = MatchRuntimeManager(probe.factory)
    coordinator = MatchEmergencyCoordinator(
        settings=cast(Any, None),
        session_factory=probe.factory,
        manager=manager,
        limiter=cast(Any, None),
    )
    try:
        assert await manager.recover_unfinished() == len(expected_recoverable)
        assert set(manager._actors) == expected_recoverable
        assert await coordinator.recover_unfinished() == len(expected_pending)
        for pending_id in expected_pending:
            pending_actor = await manager.get_actor(pending_id)
            assert pending_actor.state.emergency_operation["status"] == "CANCELLED"
        actor = await manager.get_actor(match_id)
        assert actor.state.status == "PAUSED"
        assert actor.state.emergency_operation["status"] == "CANCELLED"
        assert actor.view().free_affirmative_remaining_ms == 56_000
        await manager.submit(
            match_id,
            MatchCommand(
                type="speech.skip.complete",
                message_id="late-placeholder",
                payload={
                    "privileged": True,
                    "operation_id": op["id"],
                    "operation_generation": op["generation"],
                    "context_version": op["context_version"],
                    "text": "迟到结果不可提交。",
                },
            ),
        )
        async with probe.factory() as session:
            match = await session.get(Match, match_id)
            assert match.runtime_snapshot["emergency_operation"]["status"] == "CANCELLED"
            assert not list(
                (
                    await session.scalars(
                        select(Speech).where(
                            Speech.match_id == match_id, Speech.origin == "ADMIN_PLACEHOLDER"
                        )
                    )
                ).all()
            )
        await manager.submit(
            match_id,
            MatchCommand(
                type="match.resume",
                message_id="resume",
                payload={"privileged": True, "reasons": []},
            ),
        )
        await manager.submit(match_id, MatchCommand(type="resume.elapsed", message_id="resumed"))
        # A fresh real Manager enforces the shared necessary-person checks;
        # this old fixture has no connected/checked participant yet.
        assert actor.state.status == "PAUSED" and actor.state.action_state == "RECOVERY_REQUIRED"
        assert actor.view().free_affirmative_remaining_ms == 56_000
        from datetime import UTC, datetime, timedelta

        from jx_core.models import DeviceCheck, RoomConnectionLease, RoomMember

        async with probe.factory() as session:
            async with session.begin():
                match = await session.get(Match, match_id)
                session.add(
                    RoomMember(
                        room_id=match.room_id,
                        user_id=first,
                        member_role="DEBATER",
                        online=True,
                        ready=True,
                    )
                )
                session.add(
                    RoomConnectionLease(
                        room_id=match.room_id,
                        user_id=first,
                        connection_id=uuid4(),
                        connection_epoch=1,
                    )
                )
                session.add(
                    DeviceCheck(
                        room_id=match.room_id,
                        user_id=first,
                        check_version=1,
                        status="PASS",
                        valid_until=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
        await manager.submit(
            match_id,
            MatchCommand(
                "match.resume", "resume-ready-human", payload={"privileged": True, "reasons": []}
            ),
        )
        assert actor.state.action_state == "RESUME_COUNTDOWN"
        await manager.submit(match_id, MatchCommand("resume.elapsed", "ready-human-resumed"))
        assert actor.state.action_state == "HUMAN_READY_TO_START"
        assert actor.state.current_speaker_user_id == first
        assert actor.view().free_affirmative_remaining_ms == 56_000
    finally:
        await coordinator.close()
        await manager.close()


@pytest.mark.asyncio
async def test_cleanup_failure_after_commit_fails_operation_without_starting_llm(agent_fault_probe):
    from typing import cast

    from jx_core.matches.emergency import MatchEmergencyCoordinator

    probe = agent_fault_probe
    now, first, _, _ = await prepare(probe)
    match_id = probe.actor.state.match_id
    old = await start_human(probe, first)
    now[0] += 4
    probe.manager._speech_runtime.reset_speech.side_effect = RuntimeError(
        "synthetic cleanup failure"
    )
    coordinator = MatchEmergencyCoordinator(
        settings=cast(Any, None),
        session_factory=probe.factory,
        manager=probe.manager,
        limiter=cast(Any, None),
    )
    generated = AsyncMock(return_value="不应调用模型。")
    coordinator._generate = generated
    try:
        with pytest.raises(MatchDomainError, match="asr_start_timeout"):
            await coordinator.start_skip(
                match_id, actor_user_id=first, payload=operator_payload(probe.actor)
            )
        async with asyncio.timeout(3):
            while coordinator._tasks:
                await asyncio.sleep(0.01)
        generated.assert_not_awaited()
        assert probe.actor.state.status == "PAUSED"
        assert probe.actor.state.emergency_operation["status"] == "FAILED"
        assert probe.actor.view().free_affirmative_remaining_ms == 56_000
        async with probe.factory() as session:
            assert (await session.get(Speech, old)).status == "RESET"
            match = await session.get(Match, match_id)
            assert match.runtime_snapshot["emergency_operation"]["status"] == "FAILED"
    finally:
        await coordinator.close()
