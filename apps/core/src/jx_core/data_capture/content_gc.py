"""Collect owned candidates and protect every immutable content-blob reference."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import batched
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from ..auth.errors import APIError
from ..models import (
    AgentGeneration,
    CallContentBlob,
    ExportProviderCaptureSnapshot,
    ExternalCall,
    JudgeResult,
    MatchExportItem,
    ProviderCallCapture,
)

CONTENT_GC_BATCH_SIZE = 512
CONTENT_GC_MAX_CANDIDATES = 65_536


def content_blob_unreferenced() -> ColumnElement[bool]:
    """One reference rule shared by match and export-input cleanup."""

    return and_(
        *(
            ~select(model.id)
            .where(
                or_(
                    model.request_blob_id == CallContentBlob.id,
                    model.response_blob_id == CallContentBlob.id,
                )
            )
            .exists()
            for model in (
                AgentGeneration,
                JudgeResult,
                ExternalCall,
                ProviderCallCapture,
                ExportProviderCaptureSnapshot,
            )
        )
    )


async def _collect_pairs(
    session: AsyncSession,
    statements: Sequence[Select[tuple[UUID | None, UUID | None]]],
) -> set[UUID]:
    candidates: set[UUID] = set()
    for statement in statements:
        rows = await session.stream(statement.execution_options(yield_per=CONTENT_GC_BATCH_SIZE))
        try:
            async for pair in rows:
                for blob_id in pair:
                    if blob_id is None or blob_id in candidates:
                        continue
                    if len(candidates) >= CONTENT_GC_MAX_CANDIDATES:
                        raise APIError("content_cleanup_too_large")
                    candidates.add(blob_id)
        finally:
            await rows.close()
    return candidates


async def collect_match_blob_ids(session: AsyncSession, match_id: UUID) -> set[UUID]:
    """Collect this match's pointers inside its explicit deletion transaction.

    The caller first locks the terminal Match. Lock the owned call/capture
    rows before deleting: this prevents late inserts/revisions from changing
    the collected pointers while their parent rows are being cascaded away.
    No unrelated match/capture row is locked or considered a GC candidate.
    """

    return await _collect_pairs(
        session,
        [
            select(AgentGeneration.request_blob_id, AgentGeneration.response_blob_id).where(
                AgentGeneration.match_id == match_id
            ),
            select(JudgeResult.request_blob_id, JudgeResult.response_blob_id).where(
                JudgeResult.match_id == match_id
            ),
            select(ExternalCall.request_blob_id, ExternalCall.response_blob_id)
            .where(ExternalCall.match_id == match_id)
            .with_for_update(),
            select(ProviderCallCapture.request_blob_id, ProviderCallCapture.response_blob_id)
            .join(ExternalCall, ExternalCall.id == ProviderCallCapture.external_call_id)
            .where(ExternalCall.match_id == match_id)
            .with_for_update(of=ProviderCallCapture),
            select(
                ExportProviderCaptureSnapshot.request_blob_id,
                ExportProviderCaptureSnapshot.response_blob_id,
            )
            .join(
                MatchExportItem, MatchExportItem.id == ExportProviderCaptureSnapshot.export_item_id
            )
            .where(MatchExportItem.match_id == match_id)
            .with_for_update(of=ExportProviderCaptureSnapshot),
        ],
    )


async def collect_export_blob_ids(session: AsyncSession, export_id: UUID) -> set[UUID]:
    """Collect only this export's frozen-input pointers before its normal deletion."""

    return await _collect_pairs(
        session,
        [
            select(
                ExportProviderCaptureSnapshot.request_blob_id,
                ExportProviderCaptureSnapshot.response_blob_id,
            )
            .join(
                MatchExportItem, MatchExportItem.id == ExportProviderCaptureSnapshot.export_item_id
            )
            .where(MatchExportItem.export_id == export_id)
            .with_for_update(of=ExportProviderCaptureSnapshot)
        ],
    )


async def collect_unreferenced_content_blobs(
    session: AsyncSession, candidates: Iterable[UUID]
) -> int:
    """Delete only supplied candidates with no reference anywhere in the database.

    Run after the caller flushes deletion of its owning rows, in that same
    transaction. FK RESTRICT remains the final guard against concurrent new
    references; an authority transaction error is never swallowed here.
    """

    deleted = 0
    for chunk in batched(candidates, CONTENT_GC_BATCH_SIZE):
        rows = await session.scalars(
            delete(CallContentBlob)
            .where(CallContentBlob.id.in_(chunk), content_blob_unreferenced())
            .returning(CallContentBlob.id)
        )
        deleted += len(rows.all())
    return deleted
