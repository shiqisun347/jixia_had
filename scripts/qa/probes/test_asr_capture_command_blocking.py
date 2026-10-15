"""Explicit local PostgreSQL probe of optional ASR capture command blocking.

Run explicitly with PYTHONPATH=. and RUN_DATABASE_INTEGRATION=1, using an
independent TEST_DATABASE_URL for the dedicated local QA database. The normal
green gates do not include this file. Strong failure assertions remain visible.
No provider, microphone, LiveKit or production resource is accessed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from apps.core.tests.test_agent_runtime_fault_integration import AgentFaultProbe
from apps.core.tests.test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from apps.core.tests.test_agent_runtime_fault_integration import (
    agent_fault_probe as agent_fault_probe,
)
from sqlalchemy import event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

import jx_core.data_capture.provider_writer as writer_module
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_writer import ProviderCaptureWriter
from jx_core.matches.domain import MatchAction, MatchActor, MatchCommand
from jx_core.matches.service import state_snapshot
from jx_core.models import AsrSegment, ExternalCall, Match, MatchParticipant, Speech
from jx_core.models import ProviderCallCapture as CaptureRow
from jx_core.runtime_identity import CallbackEnvelope

pytestmark = pytest.mark.integration
# Wire the same production Actor hooks as the existing isolated fixture.
# pyright: reportPrivateUsage=false


@dataclass
class AsrCaptureProbe:
    agent: AgentFaultProbe
    writer: ProviderCaptureWriter
    envelope: CallbackEnvelope
    user_id: UUID
    task_id: UUID
    segment_id: UUID
    call_id: UUID
    capture: ProviderCallCapture
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: list[asyncio.Task[Any]] = field(default_factory=lambda: list[asyncio.Task[Any]]())

    def start_capture(self) -> asyncio.Task[None]:
        task = asyncio.create_task(
            self.agent.manager.persist_asr_capture(
                envelope=self.envelope, task_id=self.task_id, provider_capture=self.capture
            ),
            name="qa-asr-optional-capture",
        )
        self.tasks.append(task)
        return task

    def start_pause(self) -> asyncio.Task[Any]:
        task = asyncio.create_task(
            self.agent.actor.submit(
                MatchCommand(
                    type="match.pause",
                    message_id=f"capture-pause-{uuid4()}",
                    actor_user_id=self.user_id,
                    payload={"authorized": True},
                )
            ),
            name="qa-asr-authoritative-pause",
        )
        self.tasks.append(task)
        return task

    async def snapshot(self) -> dict[str, Any]:
        async with self.agent.factory() as session:
            match = await session.get(Match, self.envelope.match_id)
            segment = await session.get(AsrSegment, self.segment_id)
            call = await session.get(ExternalCall, self.call_id)
            assert match is not None and segment is not None and call is not None
            raw = await session.scalar(
                select(CaptureRow).where(CaptureRow.external_call_id == self.call_id)
            )
            return {
                "match_status": match.status,
                "match_sequence": match.sequence,
                "actor_status": self.agent.actor.state.status,
                "actor_sequence": self.agent.actor.state.sequence,
                "segment_status": segment.status,
                "segment_text_preserved": segment.raw_final_text == "Synthetic ASR final",
                "call_status": call.status,
                "capture_error": raw.capture_error_code
                if raw is not None
                else "capture_not_persisted",
                "capture_storage_mode": call.capture_storage_mode,
                "writer_stats": self.writer.stats(),
                "broadcast_count": self.agent.queue.qsize(),
            }

    async def authority_lock(self, table: Literal["matches", "speeches"]) -> dict[str, Any]:
        row_id = self.envelope.match_id if table == "matches" else self.envelope.speech_id
        started = monotonic()
        try:
            async with asyncio.timeout(0.8):
                async with self.agent.factory() as session:
                    async with session.begin():
                        await session.execute(text("SET LOCAL lock_timeout = '150ms'"))
                        await session.execute(
                            text(f"SELECT id FROM {table} WHERE id = :id FOR NO KEY UPDATE"),
                            {"id": row_id},
                        )
        except DBAPIError as error:
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate != "55P03":
                raise
            return {
                "acquired": False,
                "sqlstate": sqlstate,
                "elapsed_ms": round((monotonic() - started) * 1000, 2),
            }
        return {"acquired": True, "elapsed_ms": round((monotonic() - started) * 1000, 2)}


@pytest.fixture
async def asr_capture_probe(
    agent_fault_probe: AgentFaultProbe,
    agent_fault_database_url: str,
) -> AsyncGenerator[AsrCaptureProbe, None]:
    agent = agent_fault_probe
    writer = ProviderCaptureWriter(agent_fault_database_url)
    await writer.start()
    agent.manager._capture_writer = writer
    await agent.actor.close()
    speech_id, segment_id, task_id, call_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with agent.factory() as session:
        async with session.begin():
            participant = await session.scalar(
                select(MatchParticipant).where(
                    MatchParticipant.match_id == agent.actor.state.match_id,
                    MatchParticipant.side == "AFFIRMATIVE",
                    MatchParticipant.seat_no == 1,
                )
            )
            assert participant is not None and participant.user_id is not None
            user_id = participant.user_id
            match = await session.get(Match, agent.actor.state.match_id, with_for_update=True)
            assert match is not None
            context_version = match.context_version
            state = replace(
                agent.actor.state,
                actions=(
                    MatchAction(
                        stage_position=1,
                        action_position=1,
                        action_kind="HUMAN_SPEECH",
                        duration_seconds=30,
                        side="AFFIRMATIVE",
                        seat_no=1,
                        speaker_user_id=user_id,
                    ),
                ),
                action_state="HUMAN_SPEAKING",
                current_speech_id=speech_id,
                current_speaker_user_id=user_id,
                current_agent_profile_id=None,
                current_speaker_side="AFFIRMATIVE",
                current_speaker_seat_no=1,
                speech_start_sequence=0,
                connection_epochs=((user_id, 1),),
            )
            match.runtime_snapshot = state_snapshot(state)
            session.add(
                Speech(
                    id=speech_id,
                    match_id=state.match_id,
                    action_key="1:1",
                    user_id=user_id,
                    speaker_kind="HUMAN",
                    side="AFFIRMATIVE",
                    seat_no=1,
                    status="STARTED",
                    attempt_no=1,
                )
            )
            await session.flush()
            session.add(
                AsrSegment(
                    id=segment_id,
                    speech_id=speech_id,
                    segment_no=1,
                    task_id=task_id,
                    status="FINALIZED",
                    raw_final_text="Synthetic ASR final",
                    pcm_sample_count=1600,
                    finalized_at=datetime.now(UTC),
                )
            )
            await session.flush()
            session.add(
                ExternalCall(
                    id=call_id,
                    call_kind="ASR",
                    provider="DASHSCOPE",
                    operation="asr.stream",
                    model="synthetic-asr",
                    attempt_no=1,
                    status="SUCCEEDED",
                    match_id=state.match_id,
                    speech_id=speech_id,
                    asr_segment_id=segment_id,
                    connection_epoch=1,
                    context_version=context_version,
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
            )
    actor = MatchActor(
        state,
        commit=agent.manager._commit,
        publish=agent.manager._publish,
        pre_commit=agent.manager._pre_commit,
    )
    agent.manager._actors[state.match_id] = actor
    agent.actor = actor
    await actor.start()
    capture = writer.new_capture()
    capture.capture_request(
        transport="websocket", url="wss://synthetic.invalid/asr", body={"task_id": str(task_id)}
    )
    capture.capture_response(
        transport="websocket",
        url="wss://synthetic.invalid/asr",
        body={"text": "Synthetic ASR final"},
    )
    probe = AsrCaptureProbe(
        agent,
        writer,
        CallbackEnvelope(
            match_id=state.match_id,
            speech_id=speech_id,
            attempt_no=1,
            generation_id=None,
            connection_epoch=1,
            context_version=context_version,
            opportunity_id=None,
            opportunity_generation=None,
        ),
        user_id,
        task_id,
        segment_id,
        call_id,
        capture,
    )
    try:
        yield probe
    finally:
        probe.release.set()
        for task in probe.tasks:
            if not task.done():
                task.cancel()
        if probe.tasks:
            _, pending = await asyncio.wait(probe.tasks, timeout=1.0)
            assert not pending, "synthetic ASR probe tasks must terminate within cleanup bound"
            await asyncio.gather(*probe.tasks, return_exceptions=True)
        capture.close()
        await writer.close()
        agent.manager._capture_writer = None


def inject_capture_fault(
    probe: AsrCaptureProbe, monkeypatch: pytest.MonkeyPatch, fault: Literal["sql", "hang"]
) -> None:
    original = writer_module._store_encoded_blob

    async def store(
        session: AsyncSession,
        kind: str,
        raw: bytes,
        prepared: tuple[bytes, str],
    ) -> UUID:
        if kind == "REQUEST":
            probe.entered.set()
            if fault == "sql":
                await session.execute(text("SELECT CAST('synthetic-not-an-integer' AS integer)"))
            else:
                await probe.release.wait()
        return await original(session, kind, raw, prepared)

    monkeypatch.setattr(writer_module, "_store_encoded_blob", store)


@pytest.mark.asyncio
async def test_capture_sql_error_control_keeps_segment_and_pause_available(
    asr_capture_probe: AsrCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = asr_capture_probe
    inject_capture_fault(probe, monkeypatch, "sql")
    await asyncio.wait_for(probe.start_capture(), timeout=0.8)
    await asyncio.wait_for(probe.entered.wait(), timeout=0.8)
    await probe.writer.close()
    before = await probe.snapshot()
    assert before["segment_status"] == "FINALIZED" and before["segment_text_preserved"]
    assert (
        before["call_status"] == "SUCCEEDED" and before["capture_error"] == "capture_not_persisted"
    )
    assert before["capture_storage_mode"] == "ASYNC_V1"
    assert probe.writer.stats().get("retry") == 1
    assert (await probe.authority_lock("matches"))["acquired"]
    assert (await probe.authority_lock("speeches"))["acquired"]
    await asyncio.wait_for(probe.start_pause(), timeout=0.8)
    after = await probe.snapshot()
    assert after["match_status"] == after["actor_status"] == "PAUSED"
    assert after["broadcast_count"] > 0


@pytest.mark.asyncio
async def test_optional_capture_hang_does_not_hold_match_or_speech_authority_locks(
    asr_capture_probe: AsrCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = asr_capture_probe
    inject_capture_fault(probe, monkeypatch, "hang")
    capture_task = probe.start_capture()
    await asyncio.wait_for(probe.entered.wait(), timeout=0.8)
    match_lock = await probe.authority_lock("matches")
    speech_lock = await probe.authority_lock("speeches")
    pause_task = probe.start_pause()
    done, _ = await asyncio.wait({pause_task}, timeout=0.35)
    observed = await probe.snapshot()
    proof = {
        "capture_pending": not capture_task.done(),
        "pause_pending": pause_task not in done,
        "match_lock": match_lock,
        "speech_lock": speech_lock,
        "state": observed,
    }
    print(proof)
    assert capture_task.done() and not capture_task.cancelled(), proof
    assert observed["segment_text_preserved"] and observed["segment_status"] == "FINALIZED"
    assert match_lock["acquired"] and speech_lock["acquired"] and pause_task in done, proof
    assert observed["match_status"] == "PAUSED" and observed["broadcast_count"] > 0, proof
    # This accepted historical call remains valid after the Actor's current
    # action has advanced; writer must not re-run the old callback claim.
    probe.release.set()
    await probe.writer.close()
    async with probe.agent.factory() as session:
        persisted = await session.scalar(
            select(CaptureRow).where(CaptureRow.external_call_id == probe.call_id)
        )
        assert persisted is not None and persisted.request_capture_status == "COMPLETE"


@pytest.mark.asyncio
async def test_cancel_optional_capture_releases_authority_without_losing_segment(
    asr_capture_probe: AsrCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = asr_capture_probe
    inject_capture_fault(probe, monkeypatch, "hang")
    capture_task = probe.start_capture()
    await asyncio.wait_for(probe.entered.wait(), timeout=0.8)
    # Accepted capture is no longer awaited by this callback. A cancellation
    # offered after storage starts cannot roll back its historical result.
    assert capture_task.done() and capture_task.cancel() is False
    await capture_task
    assert (await probe.authority_lock("matches"))["acquired"]
    assert (await probe.authority_lock("speeches"))["acquired"]
    await asyncio.wait_for(probe.start_pause(), timeout=0.8)
    observed = await probe.snapshot()
    assert observed["segment_text_preserved"] and observed["segment_status"] == "FINALIZED"
    assert observed["match_status"] == observed["actor_status"] == "PAUSED"


@pytest.mark.asyncio
async def test_stale_asr_capture_is_not_submitted_or_retained(
    asr_capture_probe: AsrCaptureProbe,
) -> None:
    probe = asr_capture_probe
    await probe.agent.manager.persist_asr_capture(
        envelope=replace(probe.envelope, connection_epoch=2),
        task_id=probe.task_id,
        provider_capture=probe.capture,
    )
    assert probe.writer.stats().get("accepted", 0) == 0
    assert probe.writer.stats()["items"] == 0
    observed = await probe.snapshot()
    assert observed["segment_text_preserved"] and observed["call_status"] == "SUCCEEDED"
    assert observed["match_status"] == observed["actor_status"] == "RUNNING"
    assert observed["broadcast_count"] == 0


@pytest.mark.asyncio
async def test_asr_acceptance_transaction_failure_rolls_back_without_submission(
    asr_capture_probe: AsrCaptureProbe,
) -> None:
    probe = asr_capture_probe
    injected = False

    def fail_acceptance(
        connection: Connection, _: Any, statement: str, __: Any, ___: Any, ____: bool
    ) -> None:
        nonlocal injected
        if not injected and statement.lstrip().startswith("UPDATE external_calls"):
            injected = True
            connection.execute(text("SELECT 1 / 0"))

    event.listen(probe.agent.engine.sync_engine, "before_cursor_execute", fail_acceptance)
    try:
        with pytest.raises(DBAPIError):
            await probe.agent.manager.persist_asr_capture(
                envelope=probe.envelope, task_id=probe.task_id, provider_capture=probe.capture
            )
    finally:
        event.remove(probe.agent.engine.sync_engine, "before_cursor_execute", fail_acceptance)
    assert injected
    assert probe.writer.stats().get("accepted", 0) == 0
    assert probe.writer.stats()["items"] == 0
    observed = await probe.snapshot()
    assert observed["capture_storage_mode"] is None
    assert observed["segment_text_preserved"] and observed["call_status"] == "SUCCEEDED"
    assert observed["match_status"] == observed["actor_status"] == "RUNNING"
    assert observed["broadcast_count"] == 0
