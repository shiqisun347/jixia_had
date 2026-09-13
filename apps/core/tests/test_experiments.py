from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from jx_core.config import Settings
from jx_core.experiments.postmatch import create_postmatch_annotation_work
from jx_core.experiments.routes import experiment_capabilities, get_experiment_prompt_templates
from jx_core.experiments.schemas import ExperimentBatchCreateRequest, ExperimentBatchUpdateRequest
from jx_core.experiments.service import ExperimentContext, ExperimentService
from jx_core.models import ExperimentBatch, ExperimentMatchAttempt, ScheduledMatch


class _ExecuteResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def one_or_none(self) -> object | None:
        return self._row


class _Session:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.query = None

    async def execute(self, query: object) -> _ExecuteResult:
        self.query = query
        return _ExecuteResult(self.row)


def _request(*, enabled: bool) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(
                database_url="postgresql+psycopg://test:test@127.0.0.1:5432/test",
                paper_experiment_enabled=enabled,
            )
        )
    )
    return Request({"type": "http", "method": "GET", "path": "/", "app": app})


@pytest.mark.asyncio
async def test_capabilities_are_closed_by_default_and_report_v2() -> None:
    response = await experiment_capabilities(_request(enabled=False))

    assert response.model_dump() == {
        "creation_enabled": False,
        "history_readable": True,
        "target_version": "2.1.0",
    }


def test_batch_request_normalizes_text_and_rejects_unknown_fields() -> None:
    payload = ExperimentBatchCreateRequest.model_validate(
        {
            "code": " PAPER_01 ",
            "title": " 论文实验 ",
            "rule_id": str(uuid4()),
            "training_room_quota": 5,
        }
    )
    assert payload.code == "PAPER_01"
    assert payload.title == "论文实验"
    assert payload.training_room_quota == 5

    with pytest.raises(ValidationError):
        ExperimentBatchCreateRequest.model_validate(
            {
                **payload.model_dump(),
                "status": "PUBLISHED",
            }
        )
    with pytest.raises(ValidationError):
        ExperimentBatchCreateRequest.model_validate(
            {
                **payload.model_dump(),
                "format_version_id": str(uuid4()),
            }
        )

    update = ExperimentBatchUpdateRequest.model_validate(
        {
            "code": " PAPER_02 ",
            "title": " 修改后的实验 ",
            "rule_id": str(uuid4()),
            "training_room_quota": 3,
        }
    )
    assert update.code == "PAPER_02"
    assert update.title == "修改后的实验"

    with pytest.raises(ValidationError):
        ExperimentBatchCreateRequest.model_validate(
            {
                "code": "PAPER_03",
                "title": "训练配额无效",
                "rule_id": str(uuid4()),
                "training_room_quota": 21,
            }
        )


@pytest.mark.asyncio
async def test_prompt_template_preview_uses_the_approved_experiment_prompts() -> None:
    response = await get_experiment_prompt_templates(SimpleNamespace())  # type: ignore[arg-type]

    assert response.version == "paper-v2.1-json-history-2026-08-30"
    assert '{"should_speak": true|false}' in response.decision_prompt
    assert "你的席位：二辩" in response.decision_prompt
    assert "只输出将被朗读的发言正文" in response.speech_prompt
    assert "{{DEBATE_HISTORY}}" in response.speech_prompt


def test_experiment_context_is_enabled_only_for_published_active_attempts() -> None:
    base = dict(
        batch_id=uuid4(),
        batch_status="PUBLISHED",
        scheduled_match_id=uuid4(),
        scheduled_match_kind="FORMAL",
        scheduled_match_status="RUNNING",
        schedule_version=1,
        attempt_id=uuid4(),
        attempt_no=1,
        attempt_status="RUNNING",
        room_id=uuid4(),
        match_id=uuid4(),
    )
    assert ExperimentContext(**base).runtime_enabled is True
    assert ExperimentContext(**{**base, "batch_status": "DISABLED"}).runtime_enabled is False
    assert ExperimentContext(**{**base, "attempt_status": "COMPLETED"}).runtime_enabled is False


@pytest.mark.asyncio
async def test_resolve_context_returns_validated_linkage() -> None:
    batch = ExperimentBatch(
        id=uuid4(),
        code="PAPER_01",
        title="论文实验",
        status="PUBLISHED",
        schedule_version=2,
        consent_version="v1",
        consent_summary="summary",
        consent_document="document",
        rule_id=uuid4(),
        created_by=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    scheduled = ScheduledMatch(
        id=uuid4(),
        batch_id=batch.id,
        round_no=1,
        match_no=1,
        topic_id=uuid4(),
        affirmative_team_id=uuid4(),
        negative_team_id=uuid4(),
        kind="FORMAL",
        status="RUNNING",
        schedule_version=2,
    )
    attempt = ExperimentMatchAttempt(
        id=uuid4(),
        scheduled_match_id=scheduled.id,
        attempt_no=1,
        room_id=uuid4(),
        match_id=uuid4(),
        status="RUNNING",
    )
    session = _Session((attempt, scheduled, batch))

    context = await ExperimentService().resolve_context(session, room_id=attempt.room_id)  # type: ignore[arg-type]

    assert context is not None
    assert context.runtime_enabled is True
    assert context.batch_id == batch.id
    assert context.attempt_id == attempt.id
    assert session.query is not None


@pytest.mark.asyncio
async def test_resolve_context_returns_none_and_requires_exactly_one_identifier() -> None:
    service = ExperimentService()
    assert await service.resolve_context(_Session(None), match_id=uuid4()) is None  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="exactly one"):
        await service.resolve_context(_Session(None))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly one"):
        await service.resolve_context(  # type: ignore[arg-type]
            _Session(None), room_id=uuid4(), match_id=uuid4()
        )


@pytest.mark.asyncio
async def test_training_match_skips_formal_annotation_work() -> None:
    scheduled = ScheduledMatch(
        id=uuid4(),
        batch_id=uuid4(),
        round_no=7,
        match_no=1,
        topic_id=uuid4(),
        affirmative_team_id=uuid4(),
        negative_team_id=uuid4(),
        kind="TRAINING",
        status="COMPLETED",
        schedule_version=1,
    )
    session = _Session(None)

    await create_postmatch_annotation_work(  # type: ignore[arg-type]
        session,
        attempt_id=uuid4(),
        scheduled_match=scheduled,
        match_id=uuid4(),
    )

    assert session.query is None
