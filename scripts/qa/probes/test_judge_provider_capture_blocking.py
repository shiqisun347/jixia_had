"""Opt-in known-risk probes of real PostmatchService/HTTP adapter/PostgreSQL.

Run explicitly with PYTHONPATH=. and the dedicated local
TEST_DATABASE_URL database jx_debate_qa_agent_faults, distinct DATABASE_URL,
and RUN_DATABASE_INTEGRATION=1. This path is outside the default green gates.

No xfail: assertions retain the TechDesign requirement that optional raw
capture cannot block or replace authoritative judge results. HTTP is synthetic;
this is neither a provider benchmark nor production acceptance.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import httpx
import pytest
from apps.core.tests.test_agent_runtime_fault_integration import AgentFaultProbe
from apps.core.tests.test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from apps.core.tests.test_agent_runtime_fault_integration import (
    agent_fault_probe as agent_fault_probe,
)
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

import jx_core.data_capture.provider_writer as writer_module
import jx_core.postmatch as postmatch_module
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.data_capture.content import load_content_blob
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import AuditLog, ExternalCall, JudgeResult, Match, MatchParticipant, Room
from jx_core.models import ProviderCallCapture as CaptureRow
from jx_core.postmatch import PostmatchService

pytestmark = pytest.mark.integration
# Real lifecycle and transaction ownership are deliberately observed here.
# pyright: reportPrivateUsage=false

ProviderOutcome = Literal["success", "failure"]
CaptureFault = Literal["normal", "boundary_raise", "storage_raise", "storage_sql_error", "hang"]


def _empty_call_ids() -> list[UUID]:
    return []


@dataclass
class JudgeCaptureProbe:
    agent: AgentFaultProbe
    service: PostmatchService
    writer: ProviderCaptureWriter
    expected_result: dict[str, Any]
    response_text: str
    capture_entered: asyncio.Event = field(default_factory=asyncio.Event)
    capture_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    call_ids: list[UUID] = field(default_factory=_empty_call_ids)
    provider_outcome: ProviderOutcome = "success"
    capture_fault: CaptureFault = "normal"
    provider_calls: int = 0
    storage_failures: int = 0
    result_id: UUID | None = None
    task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.result_id = await self.service.request_judge(self.agent.actor.state.match_id)
        assert self.result_id is not None
        assert len(self.service._tasks) == 1
        self.task = next(iter(self.service._tasks))

    async def capacity_available(self) -> None:
        limiter = self.service._limiter
        first = await limiter.acquire(str(self.agent.model_id), 1, timeout_seconds=0.1)
        try:
            second = await limiter.acquire("synthetic-unused-model", 1, timeout_seconds=0.1)
            limiter.release(second)
        finally:
            limiter.release(first)

    async def snapshot(self) -> dict[str, Any]:
        assert self.result_id is not None and self.task is not None
        async with self.agent.factory() as session:
            row = await session.get(JudgeResult, self.result_id)
            assert row is not None
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall)
                        .where(ExternalCall.judge_result_id == self.result_id)
                        .order_by(ExternalCall.attempt_no)
                    )
                ).all()
            )
            match = await session.get(Match, row.match_id)
            assert match is not None
            request = (
                await load_content_blob(session, row.request_blob_id)
                if row.request_blob_id is not None
                else None
            )
            response = (
                await load_content_blob(session, row.response_blob_id)
                if row.response_blob_id is not None
                else None
            )
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id.in_([str(row.id), str(row.match_id)]))
            )
            capture_count = await session.scalar(
                select(func.count())
                .select_from(CaptureRow)
                .where(CaptureRow.external_call_id.in_([call.id for call in calls]))
            )
        task_error = (
            self.task.exception() if self.task.done() and not self.task.cancelled() else None
        )
        return {
            "provider_outcome": self.provider_outcome,
            "capture_fault": self.capture_fault,
            "provider_calls": self.provider_calls,
            "capture_calls": len(self.call_ids),
            "storage_failures": self.storage_failures,
            "task_done": self.task.done(),
            "task_cancelled": self.task.cancelled(),
            "task_exception_type": type(task_error).__name__ if task_error else None,
            "task_exception_code": getattr(task_error, "code", None),
            "tracked_tasks": len(self.service._tasks),
            "judge_status": row.status,
            "judge_error": row.error_code,
            "judge_completed": row.completed_at is not None,
            "judge_attempt": row.attempt_no,
            "business_request_present": isinstance(request, dict)
            and isinstance(cast(dict[str, Any], request).get("messages"), list),
            "business_response_complete": response == {"text": self.response_text},
            "parsed_result_complete": row.result == self.expected_result,
            "call_statuses": [call.status for call in calls],
            "call_errors": [call.error_code for call in calls],
            "call_completed": [call.completed_at is not None for call in calls],
            "raw_capture_errors": [call.capture_error_code for call in calls],
            "capture_storage_modes": [call.capture_storage_mode for call in calls],
            "persisted_captures": int(capture_count or 0),
            "writer_stats": self.writer.stats(),
            "match_status": match.status,
            # PostmatchService has no AuditService call. Observe the actual
            # scoped count; do not invent a new audit requirement here.
            "related_audit_count": int(audit_count or 0),
        }


@pytest.fixture
async def judge_capture_probe(
    agent_fault_probe: AgentFaultProbe,
    agent_fault_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[JudgeCaptureProbe, None]:
    agent = agent_fault_probe
    async with agent.factory() as session:
        async with session.begin():
            match = await session.get(Match, agent.actor.state.match_id)
            assert match is not None
            room = await session.get(Room, match.room_id)
            assert room is not None
            match.status = room.status = "FINISHED"
            match.ended_at = room.ended_at = datetime.now(UTC)
            room.format_snapshot = {
                "judge": {
                    "enabled": True,
                    "system_prompt": "Synthetic judge; return JSON.",
                    "judge_prompt": "Synthetic scores for the frozen participants.",
                    "model": {
                        "id": str(agent.model_id),
                        "base_url": "https://synthetic.invalid/v1",
                        "model_id": "synthetic-model",
                        "max_concurrency": 1,
                    },
                }
            }
            participants = list(
                (
                    await session.scalars(
                        select(MatchParticipant)
                        .where(MatchParticipant.match_id == match.id)
                        .order_by(MatchParticipant.side, MatchParticipant.seat_no)
                    )
                ).all()
            )
    dimensions = {"argument": 30, "rebuttal": 25, "evidence": 20, "teamwork": 15, "expression": 10}
    result = {
        "winner": "AFFIRMATIVE",
        "team_scores": {"AFFIRMATIVE": dimensions, "NEGATIVE": dimensions},
        "participants": [
            {"participant_id": str(row.id), "score": 18, "comment": "Synthetic comment"}
            for row in participants
        ],
        "team_comments": {"AFFIRMATIVE": "Synthetic affirmative", "NEGATIVE": "Synthetic negative"},
    }
    writer = ProviderCaptureWriter(agent_fault_database_url)
    await writer.start()
    service = PostmatchService(
        settings=agent.runtime._settings,
        session_factory=agent.factory,
        limiter=agent.runtime.capacity_limiter,
        capture_writer=writer,
    )
    probe = JudgeCaptureProbe(
        agent, service, writer, result, json.dumps(result, ensure_ascii=False)
    )

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        if probe.provider_outcome == "failure":
            return httpx.Response(503, json={"code": "synthetic_unavailable"})
        response = {
            "choices": [{"delta": {"content": probe.response_text}}],
            "usage": {"completion_tokens": 12},
        }
        return httpx.Response(200, content=f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n")

    def provider_client(**arguments: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        agent.http_clients.append(client)
        adapter = OpenAIStreamingClient(**arguments, client=client)
        # Production creates an owned client. Preserve that ownership while
        # replacing only HTTP transport; no fixture-owned shared client exists.
        adapter._owns_client = True
        return adapter

    original_item = writer._persist_item

    async def capture_item(
        session: AsyncSession,
        item: CaptureSubmission,
        prepared: tuple[tuple[bytes, str], tuple[bytes, str]],
    ) -> None:
        assert item.identity.associations[0] == "JUDGE"
        probe.call_ids.append(item.identity.external_call_id)
        if probe.capture_fault == "boundary_raise":
            probe.capture_entered.set()
            raise RuntimeError("synthetic_capture_boundary_failed")
        await original_item(session, item, prepared)

    original_blob = writer_module._store_encoded_blob

    async def persist_blob(
        session: AsyncSession,
        kind: str,
        raw: bytes,
        prepared: tuple[bytes, str],
    ) -> UUID:
        if kind == "RESPONSE":
            probe.capture_entered.set()
            if probe.capture_fault == "hang":
                try:
                    await asyncio.Event().wait()
                finally:
                    probe.capture_cancelled.set()
            if probe.capture_fault == "storage_raise":
                probe.storage_failures += 1
                raise RuntimeError("synthetic_raw_capture_storage_failed")
            if probe.capture_fault == "storage_sql_error":
                probe.storage_failures += 1
                await session.execute(text("SELECT 1 / 0"))
        return await original_blob(session, kind, raw, prepared)

    monkeypatch.setattr(postmatch_module, "OpenAIStreamingClient", provider_client)
    monkeypatch.setattr(writer, "_persist_item", capture_item)
    monkeypatch.setattr(writer_module, "_store_encoded_blob", persist_blob)
    try:
        yield probe
    finally:
        await service.close()
        await writer.close()
        if probe.task is not None and not probe.task.done():
            probe.task.cancel()
            done, _ = await asyncio.wait({probe.task}, timeout=0.5)
            assert done, "synthetic cooperative probe cleanup did not finish"
        if probe.task is not None and not probe.task.cancelled():
            probe.task.exception()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_outcome", ["success", "failure"])
@pytest.mark.parametrize(
    "capture_fault", ["normal", "boundary_raise", "storage_raise", "storage_sql_error", "hang"]
)
async def test_optional_judge_capture_neither_blocks_nor_replaces_authoritative_outcome(
    judge_capture_probe: JudgeCaptureProbe,
    provider_outcome: ProviderOutcome,
    capture_fault: CaptureFault,
) -> None:
    probe = judge_capture_probe
    probe.provider_outcome, probe.capture_fault = provider_outcome, capture_fault
    await probe.start()
    assert probe.task is not None
    await asyncio.wait({probe.task}, timeout=0.8)
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=0.8)
    if capture_fault != "hang":
        await probe.writer.close()
    await asyncio.sleep(0)
    await probe.capacity_available()
    evidence = await probe.snapshot()
    print(json.dumps(evidence, sort_keys=True))
    assert all(client.is_closed for client in probe.agent.http_clients), evidence
    assert evidence["match_status"] == "FINISHED", evidence
    assert evidence["related_audit_count"] == 0, evidence
    assert evidence["business_request_present"], evidence
    assert evidence["task_done"], evidence
    assert not evidence["task_cancelled"] and evidence["task_exception_type"] is None, evidence
    assert evidence["tracked_tasks"] == 0, evidence
    assert evidence["judge_completed"], evidence
    assert all(mode == "ASYNC_V1" for mode in evidence["capture_storage_modes"]), evidence
    if provider_outcome == "success":
        assert evidence["provider_calls"] == 1, evidence
        assert evidence["judge_status"] == "SUCCEEDED" and evidence["judge_error"] is None, evidence
        assert evidence["business_response_complete"] and evidence["parsed_result_complete"], (
            evidence
        )
        assert evidence["call_statuses"] == ["SUCCEEDED"], evidence
        assert evidence["call_errors"] == [None], evidence
        assert evidence["call_completed"] == [True], evidence
    else:
        assert evidence["provider_calls"] == 2, evidence
        assert evidence["judge_status"] == "FAILED", evidence
        assert evidence["judge_error"] == "llm_provider_failed", evidence
        assert evidence["call_statuses"] == ["FAILED", "FAILED"], evidence
        assert evidence["call_errors"] == ["llm_provider_failed", "llm_provider_failed"], evidence
        assert evidence["call_completed"] == [True, True], evidence
        assert not evidence["business_response_complete"], evidence
    if capture_fault in {"storage_raise", "storage_sql_error"}:
        assert evidence["storage_failures"] == 2, evidence
        assert evidence["persisted_captures"] == 0, evidence
        assert probe.writer.stats().get("retry") == 1, evidence
    elif capture_fault == "normal":
        assert evidence["persisted_captures"] == probe.provider_calls, evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_outcome", ["success", "failure"])
async def test_judge_cancel_during_raw_capture_preserves_already_observed_call_result(
    judge_capture_probe: JudgeCaptureProbe,
    provider_outcome: ProviderOutcome,
) -> None:
    probe = judge_capture_probe
    probe.provider_outcome, probe.capture_fault = provider_outcome, "hang"
    await probe.start()
    assert probe.task is not None
    await asyncio.wait_for(probe.capture_entered.wait(), 0.5)
    # The caller committed and returned before independent writer storage.
    assert probe.task.done() and probe.task.cancel() is False
    await probe.service.close()
    await asyncio.sleep(0)
    await probe.capacity_available()
    evidence = await probe.snapshot()
    print(json.dumps(evidence, sort_keys=True))
    assert not evidence["task_cancelled"] and evidence["tracked_tasks"] == 0, evidence
    assert evidence["match_status"] == "FINISHED", evidence
    assert all(client.is_closed for client in probe.agent.http_clients), evidence
    assert evidence["business_request_present"], evidence
    assert evidence["related_audit_count"] == 0, evidence
    if provider_outcome == "success":
        assert evidence["judge_status"] == "SUCCEEDED", evidence
        assert evidence["business_response_complete"] and evidence["parsed_result_complete"], (
            evidence
        )
        assert evidence["call_statuses"] == ["SUCCEEDED"], evidence
    else:
        assert evidence["judge_status"] == "FAILED" and evidence["judge_completed"], evidence
        assert evidence["call_statuses"] == ["FAILED", "FAILED"], evidence
        assert evidence["call_errors"] == ["llm_provider_failed", "llm_provider_failed"], evidence
        assert evidence["provider_calls"] == 2, evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_outcome", ["success", "failure"])
async def test_judge_raw_capture_cannot_hold_the_authoritative_external_call_row(
    judge_capture_probe: JudgeCaptureProbe,
    provider_outcome: ProviderOutcome,
) -> None:
    probe = judge_capture_probe
    probe.provider_outcome, probe.capture_fault = provider_outcome, "hang"
    await probe.start()
    await asyncio.wait_for(probe.capture_entered.wait(), 0.5)
    call_id = probe.call_ids[0]
    lock_error: DBAPIError | None = None
    try:
        async with probe.agent.factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                await session.execute(
                    update(ExternalCall)
                    .where(ExternalCall.id == call_id)
                    .values(audio_duration_ms=1)
                )
    except DBAPIError as error:
        lock_error = error
    await probe.capacity_available()
    evidence = await probe.snapshot()
    evidence["authority_update_lock_sqlstate"] = getattr(
        getattr(lock_error, "orig", None), "sqlstate", None
    )
    print(json.dumps(evidence, sort_keys=True))
    assert lock_error is None, evidence


@pytest.mark.asyncio
async def test_judge_authoritative_failure_commit_error_propagates_and_rolls_back(
    judge_capture_probe: JudgeCaptureProbe,
) -> None:
    probe = judge_capture_probe
    probe.provider_outcome = "failure"
    injected = False

    def fail_result_commit(
        connection: Connection,
        _: Any,
        statement: str,
        __: Any,
        ___: Any,
        ____: bool,
    ) -> None:
        nonlocal injected
        if not injected and statement.lstrip().startswith("UPDATE external_calls SET status="):
            injected = True
            # The database, rather than a fake Session, raises the control
            # error while flushing the actual FAILED-result transaction.
            connection.execute(text("SELECT 1 / 0"))

    event.listen(probe.agent.engine.sync_engine, "before_cursor_execute", fail_result_commit)
    try:
        await probe.start()
        assert probe.task is not None
        with pytest.raises(DBAPIError):
            await probe.task
    finally:
        event.remove(probe.agent.engine.sync_engine, "before_cursor_execute", fail_result_commit)
    await asyncio.sleep(0)
    await probe.capacity_available()
    evidence = await probe.snapshot()
    evidence["authority_failure_injected"] = injected
    print(json.dumps(evidence, sort_keys=True))
    assert injected, evidence
    assert evidence["task_done"] and evidence["task_exception_type"] is not None, evidence
    assert evidence["tracked_tasks"] == 0, evidence
    assert evidence["provider_calls"] == 1, evidence
    assert evidence["judge_status"] == "RUNNING" and not evidence["judge_completed"], evidence
    assert evidence["business_request_present"] and not evidence["business_response_complete"], (
        evidence
    )
    assert evidence["call_statuses"] == ["STARTED"], evidence
    assert evidence["call_errors"] == [None] and evidence["call_completed"] == [False], evidence
    assert evidence["match_status"] == "FINISHED", evidence
    assert probe.writer.stats().get("accepted", 0) == 0, evidence
