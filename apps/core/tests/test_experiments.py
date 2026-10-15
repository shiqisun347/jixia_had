from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from jx_core.auth.errors import APIError
from jx_core.config import Settings
from jx_core.experiments.postmatch import (
    PostmatchAnnotationWorkError,
    _bounded_context,
    _check_speech_budget,
    create_postmatch_annotation_work,
)
from jx_core.experiments.routes import (
    _decode_task_cursor,
    _encode_task_cursor,
    experiment_capabilities,
    get_experiment_prompt_templates,
)
from jx_core.experiments.schemas import (
    ExperimentBatchCreateRequest,
    ExperimentBatchUpdateRequest,
    ParticipantAnnotationAnswerSaveRequest,
    ParticipantAnnotationItemResponse,
    ParticipantAnnotationTaskResponse,
)
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


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class _StatsSession:
    def __init__(self, results: list[list[object]]) -> None:
        self.results = iter(results)
        self.calls = 0

    async def execute(self, _query: object) -> _Rows:
        self.calls += 1
        return _Rows(next(self.results))


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


def test_annotation_answer_request_rejects_too_many_keys() -> None:
    with pytest.raises(ValidationError):
        ParticipantAnnotationAnswerSaveRequest(
            stage=1,
            answers={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5},
            client_version=1,
        )


def test_annotation_answer_request_rejects_oversized_json() -> None:
    with pytest.raises(ValidationError):
        ParticipantAnnotationAnswerSaveRequest(
            stage=1,
            answers={"primary_reason": "x" * (64 * 1024)},
            client_version=1,
        )


@pytest.mark.asyncio
async def test_capabilities_are_closed_by_default_and_report_v2() -> None:
    response = await experiment_capabilities(_request(enabled=False))

    assert response.model_dump() == {
        "creation_enabled": False,
        "history_readable": True,
        "target_version": "2.1.0",
    }


def test_experiment_index_cursors_are_stable_and_invalid_values_are_rejected() -> None:
    service = ExperimentService()
    created_at = datetime(2026, 10, 13, 1, 2, 3, tzinfo=UTC)
    batch = ExperimentBatch(
        id=uuid4(),
        code="PAGE_01",
        title="分页批次",
        status="DRAFT",
        schedule_version=0,
        consent_version="v1",
        consent_summary="",
        consent_document="",
        rule_id=uuid4(),
        created_by=uuid4(),
        created_at=created_at,
        updated_at=created_at,
    )
    batch_cursor = service._encode_batch_cursor(batch)
    assert service._decode_batch_cursor(batch_cursor) == (created_at, batch.id)

    scheduled = ScheduledMatch(
        id=uuid4(),
        batch_id=batch.id,
        round_no=2,
        match_no=3,
        topic_id=uuid4(),
        affirmative_team_id=uuid4(),
        negative_team_id=uuid4(),
        scheduled_at=created_at,
        kind="FORMAL",
        status="DRAFT",
        schedule_version=1,
    )
    appointment_cursor = service._encode_appointment_cursor(scheduled)
    decoded = service._decode_appointment_cursor(appointment_cursor)
    assert decoded == (created_at, 2, 3, scheduled.id)

    with pytest.raises(APIError, match="experiment_query_invalid"):
        service._decode_batch_cursor("not-a-cursor")
    with pytest.raises(APIError, match="experiment_query_invalid"):
        service._page_limit(51)


def test_annotation_task_cursor_is_stable_and_rejects_malformed_values() -> None:
    created_at = datetime(2026, 10, 13, 1, 2, 3, tzinfo=UTC)
    task_id = uuid4()
    encoded = _encode_task_cursor((created_at, task_id))
    assert _decode_task_cursor(encoded) == (created_at, task_id)
    with pytest.raises(APIError, match="experiment_query_invalid"):
        _decode_task_cursor("not-a-cursor")


@pytest.mark.asyncio
async def test_batch_history_helper_fails_closed_without_returning_truncated_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ExperimentService()
    service.EXPERIMENT_HISTORY_MAX_ROWS = 1  # type: ignore[assignment]

    async def fake_page(*_args: object, **_kwargs: object) -> tuple[list[object], None]:
        return [object(), object()], None

    monkeypatch.setattr(service, "list_batches_page", fake_page)

    with pytest.raises(APIError, match="experiment_history_too_large"):
        await service.list_batches(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_appointment_history_helper_rejects_unique_cursor_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ExperimentService()
    service.EXPERIMENT_HISTORY_MAX_PAGES = 2  # type: ignore[assignment]
    calls = 0

    async def fake_page(*_args: object, **_kwargs: object) -> tuple[list[object], str]:
        nonlocal calls
        calls += 1
        row = SimpleNamespace(
            id=uuid4(),
            scheduled_at=datetime(2026, 10, 13 + calls, tzinfo=UTC),
            round_no=1,
            match_no=calls,
        )
        return [], service._encode_appointment_cursor(row)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "list_appointments_page", fake_page)

    with pytest.raises(APIError, match="experiment_history_too_large"):
        await service.list_appointments(object(), user_id=uuid4())  # type: ignore[arg-type]
    assert calls == 2


@pytest.mark.asyncio
async def test_batch_history_helper_rejects_cursor_that_moves_backwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ExperimentService()
    calls = 0
    first = SimpleNamespace(
        id=uuid4(),
        created_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    # Batch history is created_at DESC, id ASC. Returning a newer timestamp
    # after the first page is a cursor regression.
    second = SimpleNamespace(
        id=uuid4(),
        created_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
    cursors = [
        service._encode_batch_cursor(first),  # type: ignore[arg-type]
        service._encode_batch_cursor(second),  # type: ignore[arg-type]
    ]

    async def fake_page(*_args: object, **_kwargs: object) -> tuple[list[object], str]:
        nonlocal calls
        cursor = cursors[calls]
        calls += 1
        return [], cursor

    monkeypatch.setattr(service, "list_batches_page", fake_page)

    with pytest.raises(APIError, match="experiment_query_invalid"):
        await service.list_batches(object())  # type: ignore[arg-type]
    assert calls == 2


@pytest.mark.asyncio
async def test_appointment_history_helper_rejects_cursor_that_moves_backwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ExperimentService()
    calls = 0
    first = SimpleNamespace(
        id=uuid4(),
        scheduled_at=datetime(2026, 10, 12, tzinfo=UTC),
        round_no=1,
        match_no=1,
    )
    # Appointment history is scheduled_at ASC NULLS LAST. Returning an older
    # timestamp after the first page violates that ordering.
    second = SimpleNamespace(
        id=uuid4(),
        scheduled_at=datetime(2026, 10, 11, tzinfo=UTC),
        round_no=1,
        match_no=2,
    )
    cursors = [
        service._encode_appointment_cursor(first),  # type: ignore[arg-type]
        service._encode_appointment_cursor(second),  # type: ignore[arg-type]
    ]

    async def fake_page(*_args: object, **_kwargs: object) -> tuple[list[object], str]:
        nonlocal calls
        cursor = cursors[calls]
        calls += 1
        return [], cursor

    monkeypatch.setattr(service, "list_appointments_page", fake_page)

    with pytest.raises(APIError, match="experiment_query_invalid"):
        await service.list_appointments(object(), user_id=uuid4())  # type: ignore[arg-type]
    assert calls == 2


def test_appointment_history_cursor_handles_null_schedule_bucket() -> None:
    service = ExperimentService()
    timed = SimpleNamespace(
        id=uuid4(),
        scheduled_at=datetime(2026, 10, 12, tzinfo=UTC),
        round_no=1,
        match_no=1,
    )
    null_first = SimpleNamespace(id=uuid4(), scheduled_at=None, round_no=1, match_no=1)
    null_second = SimpleNamespace(id=uuid4(), scheduled_at=None, round_no=1, match_no=2)
    timed_cursor = service._encode_appointment_cursor(timed)  # type: ignore[arg-type]
    null_first_cursor = service._encode_appointment_cursor(null_first)  # type: ignore[arg-type]
    null_second_cursor = service._encode_appointment_cursor(null_second)  # type: ignore[arg-type]

    previous = service._validate_appointment_history_cursor(None, timed_cursor, set())
    assert service._validate_appointment_history_cursor(
        previous, null_first_cursor, {previous}
    ) == (None, 1, 1, null_first.id)
    null_previous = service._decode_appointment_cursor(null_first_cursor)
    assert service._validate_appointment_history_cursor(
        null_previous, null_second_cursor, {previous, null_previous}
    ) == (None, 1, 2, null_second.id)

    with pytest.raises(APIError, match="experiment_query_invalid"):
        service._validate_appointment_history_cursor(
            null_previous, timed_cursor, {previous, null_previous}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper_name", "page_name"),
    [
        ("list_participant_tasks", "list_participant_tasks_page"),
        ("list_expert_tasks", "list_expert_tasks_page"),
    ],
)
async def test_annotation_history_helpers_fail_closed_on_row_budget(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    page_name: str,
) -> None:
    service = ExperimentService()
    service.EXPERIMENT_HISTORY_MAX_ROWS = 1  # type: ignore[assignment]

    async def fake_page(*_args: object, **_kwargs: object) -> tuple[list[object], None]:
        return [object(), object()], None

    monkeypatch.setattr(service, page_name, fake_page)

    with pytest.raises(APIError, match="experiment_history_too_large"):
        await getattr(service, helper_name)(object(), user_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper_name", "page_name"),
    [
        ("list_participant_tasks", "list_participant_tasks_page"),
        ("list_expert_tasks", "list_expert_tasks_page"),
    ],
)
async def test_annotation_history_helpers_fail_closed_on_page_budget(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    page_name: str,
) -> None:
    service = ExperimentService()
    service.EXPERIMENT_HISTORY_MAX_PAGES = 2  # type: ignore[assignment]
    calls = 0

    async def fake_page(
        *_args: object, **_kwargs: object
    ) -> tuple[list[object], tuple[datetime, UUID]]:
        nonlocal calls
        calls += 1
        return [], (datetime(2026, 10, 13 - calls, tzinfo=UTC), uuid4())

    monkeypatch.setattr(service, page_name, fake_page)

    with pytest.raises(APIError, match="experiment_history_too_large"):
        await getattr(service, helper_name)(object(), user_id=uuid4())
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper_name", "page_name"),
    [
        ("list_participant_tasks", "list_participant_tasks_page"),
        ("list_expert_tasks", "list_expert_tasks_page"),
    ],
)
async def test_annotation_history_helpers_reject_repeated_cursor(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    page_name: str,
) -> None:
    service = ExperimentService()
    cursor = (datetime(2026, 10, 12, tzinfo=UTC), uuid4())
    calls = 0

    async def fake_page(
        *_args: object, **_kwargs: object
    ) -> tuple[list[object], tuple[datetime, UUID]]:
        nonlocal calls
        calls += 1
        return [], cursor

    monkeypatch.setattr(service, page_name, fake_page)

    with pytest.raises(APIError, match="experiment_query_invalid"):
        await getattr(service, helper_name)(object(), user_id=uuid4())
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper_name", "page_name"),
    [
        ("list_participant_tasks", "list_participant_tasks_page"),
        ("list_expert_tasks", "list_expert_tasks_page"),
    ],
)
async def test_annotation_history_helpers_reject_non_progress_cursor(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    page_name: str,
) -> None:
    service = ExperimentService()
    newest = (datetime(2026, 10, 12, tzinfo=UTC), uuid4())
    older = (datetime(2026, 10, 11, tzinfo=UTC), uuid4())
    calls = 0

    async def fake_page(
        *_args: object, **_kwargs: object
    ) -> tuple[list[object], tuple[datetime, UUID] | None]:
        nonlocal calls
        calls += 1
        return [], older if calls == 1 else newest

    monkeypatch.setattr(service, page_name, fake_page)

    with pytest.raises(APIError, match="experiment_query_invalid"):
        await getattr(service, helper_name)(object(), user_id=uuid4())
    assert calls == 2


@pytest.mark.asyncio
async def test_annotation_child_budget_rejects_rows_before_payload_queries() -> None:
    task_id = uuid4()
    session = _StatsSession([[(task_id, ExperimentService.ANNOTATION_MAX_ITEMS_PER_TASK + 1)]])

    with pytest.raises(APIError, match="experiment_annotation_payload_too_large"):
        await ExperimentService._assert_participant_child_budget(session, [task_id])  # type: ignore[arg-type]

    assert session.calls == 1


@pytest.mark.asyncio
async def test_annotation_child_budget_rejects_oversized_questionnaire_q6() -> None:
    task_id = uuid4()
    session = _StatsSession(
        [
            [],  # items
            [],  # answers
            [],  # frozen opportunity contexts
            [],  # speech transcripts
            [(task_id, ExperimentService.ANNOTATION_MAX_TASK_PAYLOAD_BYTES + 1)],
        ]
    )

    with pytest.raises(APIError, match="experiment_annotation_payload_too_large"):
        await ExperimentService._assert_participant_child_budget(session, [task_id])  # type: ignore[arg-type]

    assert session.calls == 5


@pytest.mark.asyncio
async def test_expert_child_budget_rejects_utf8_bytes_before_payload_queries() -> None:
    task_id = uuid4()
    session = _StatsSession(
        [[(task_id, 1, ExperimentService.ANNOTATION_MAX_TASK_PAYLOAD_BYTES + 1)]]
    )

    with pytest.raises(APIError, match="experiment_annotation_payload_too_large"):
        await ExperimentService._assert_expert_child_budget(session, [task_id])  # type: ignore[arg-type]

    assert session.calls == 1


def test_annotation_response_budget_rejects_oversized_frozen_context() -> None:
    item = ParticipantAnnotationItemResponse(
        id=uuid4(),
        opportunity_id=uuid4(),
        subject_kind="TEAM_AI",
        speech_id=uuid4(),
        position=1,
        stage1_locked=False,
        revealed=False,
        frozen_context={"history": ["x" * ExperimentService.ANNOTATION_MAX_TASK_PAYLOAD_BYTES]},
        speech_text=None,
        answers={},
        answer_versions={},
    )
    response = ParticipantAnnotationTaskResponse(
        id=uuid4(),
        experiment_attempt_id=uuid4(),
        questionnaire_version="v1",
        status="PENDING",
        due_at=datetime.now(UTC),
        late=False,
        submitted_at=None,
        scheduled_match_kind="FORMAL",
        items=[item],
        questionnaire=None,
    )

    with pytest.raises(APIError, match="experiment_annotation_payload_too_large"):
        ExperimentService._assert_annotation_response_budget([response])


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


def test_postmatch_payload_budget_rejects_oversized_context(monkeypatch) -> None:
    import jx_core.experiments.postmatch as postmatch_module

    monkeypatch.setattr(postmatch_module, "POSTMATCH_MAX_CONTEXT_BYTES", 8)
    with pytest.raises(
        PostmatchAnnotationWorkError, match="experiment_postmatch_payload_too_large"
    ):
        _bounded_context({"history": ["x" * 32]})


def test_postmatch_payload_budget_rejects_oversized_speech_rows(monkeypatch) -> None:
    import jx_core.experiments.postmatch as postmatch_module

    monkeypatch.setattr(postmatch_module, "POSTMATCH_MAX_SPEECH_ROWS", 1)
    speeches = [
        SimpleNamespace(display_text="a"),
        SimpleNamespace(display_text="b"),
    ]
    with pytest.raises(
        PostmatchAnnotationWorkError, match="experiment_postmatch_payload_too_large"
    ):
        _check_speech_budget(speeches)  # type: ignore[arg-type]


def test_postmatch_payload_budget_rejects_non_object_context() -> None:
    with pytest.raises(
        PostmatchAnnotationWorkError, match="experiment_postmatch_payload_too_large"
    ):
        _bounded_context(["unexpected-json-array"])
