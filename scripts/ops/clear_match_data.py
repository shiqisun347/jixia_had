"""Delete production match/room facts while preserving identities and configuration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select

from jx_core.config import Settings
from jx_core.database import Database
from jx_core.models import (
    AgentGeneration,
    BackgroundTask,
    CallContentBlob,
    ExperimentMatchAttempt,
    ExperimentResultOverride,
    ExpertAnnotationAnswer,
    ExpertAnnotationTask,
    ExternalCall,
    HumanHandEvent,
    JudgeResult,
    LeaderboardSnapshot,
    Match,
    MatchExport,
    MatchFile,
    MatchQuestionnaire,
    ParticipantAnnotationAnswer,
    ParticipantAnnotationItem,
    ParticipantAnnotationTask,
    PostmatchSurveyTask,
    Room,
    ScheduledMatch,
    SpeakerAllocation,
)


async def clear() -> None:
    settings = Settings()
    database = Database(settings.database_url.get_secret_value())
    storage_roots = (
        Path(settings.agent_audio_storage_dir).resolve(),
        Path(settings.match_audio_storage_dir).resolve(),
    )
    try:
        async with database.session_factory() as session:
            match_count = int(await session.scalar(select(func.count()).select_from(Match)) or 0)
            room_count = int(await session.scalar(select(func.count()).select_from(Room)) or 0)
            file_paths = [
                Path(path).resolve()
                for path in (
                    await session.scalars(
                        select(MatchFile.storage_path).where(MatchFile.storage_path.is_not(None))
                    )
                ).all()
                if path
            ]
            export_paths = [
                Path(path).resolve()
                for path in (
                    await session.scalars(
                        select(MatchExport.storage_path).where(MatchExport.storage_path.is_not(None))
                    )
                ).all()
                if path
            ]
            blob_ids: set[object] = set()
            for model in (AgentGeneration, JudgeResult, ExternalCall):
                rows = (
                    await session.execute(
                        select(model.request_blob_id, model.response_blob_id).where(
                            model.match_id.is_not(None)
                        )
                    )
                ).all()
                for request_blob_id, response_blob_id in rows:
                    blob_ids.update(
                        item for item in (request_blob_id, response_blob_id) if item is not None
                    )

            async with session.begin_nested():
                attempts = list((await session.scalars(select(ExperimentMatchAttempt))).all())
                for attempt in attempts:
                    if attempt.match_id is None and attempt.room_id is None:
                        continue
                    attempt.match_id = None
                    attempt.room_id = None
                    if attempt.status in {"CREATED", "WAITING", "RUNNING", "PAUSED"}:
                        attempt.status = "INCOMPLETE"
                        attempt.ended_at = datetime.now(UTC)
                        attempt.termination_reason = "ADMIN_MATCH_DATA_CLEAR"
                    scheduled = await session.get(ScheduledMatch, attempt.scheduled_match_id)
                    if scheduled is not None and scheduled.status in {"RUNNING", "PAUSED"}:
                        scheduled.status = "INCOMPLETE"
                    if scheduled is not None and scheduled.effective_attempt_id == attempt.id:
                        scheduled.effective_attempt_id = None
                await session.execute(delete(PostmatchSurveyTask))
                await session.execute(delete(MatchQuestionnaire))
                await session.execute(delete(ParticipantAnnotationAnswer))
                await session.execute(delete(ParticipantAnnotationItem))
                await session.execute(delete(ParticipantAnnotationTask))
                await session.execute(delete(ExpertAnnotationAnswer))
                await session.execute(delete(ExpertAnnotationTask))
                await session.execute(delete(SpeakerAllocation))
                await session.execute(delete(HumanHandEvent))
                await session.execute(delete(ExperimentResultOverride))
                await session.execute(
                    delete(BackgroundTask).where(
                        func.jsonb_extract_path_text(
                            BackgroundTask.payload, "match_id"
                        ).is_not(None)
                        | func.jsonb_extract_path_text(
                            BackgroundTask.payload, "room_id"
                        ).is_not(None)
                    )
                )
                await session.execute(delete(MatchExport))
                await session.execute(delete(LeaderboardSnapshot))
                await session.execute(delete(Match))
                await session.execute(delete(Room))
                await session.flush()
                if blob_ids:
                    await session.execute(
                        delete(CallContentBlob).where(
                            CallContentBlob.id.in_(blob_ids),
                            ~select(AgentGeneration.id)
                            .where(
                                (AgentGeneration.request_blob_id == CallContentBlob.id)
                                | (AgentGeneration.response_blob_id == CallContentBlob.id)
                            )
                            .exists(),
                            ~select(JudgeResult.id)
                            .where(
                                (JudgeResult.request_blob_id == CallContentBlob.id)
                                | (JudgeResult.response_blob_id == CallContentBlob.id)
                            )
                            .exists(),
                            ~select(ExternalCall.id)
                            .where(
                                (ExternalCall.request_blob_id == CallContentBlob.id)
                                | (ExternalCall.response_blob_id == CallContentBlob.id)
                            )
                            .exists(),
                        )
                    )
            await session.commit()

        deleted_files = 0
        for candidate in [*file_paths, *export_paths]:
            if not any(candidate.is_relative_to(root) for root in storage_roots):
                continue
            if candidate.is_file():
                candidate.unlink()
                deleted_files += 1
        print(
            f"cleared matches={match_count} rooms={room_count} files={deleted_files}",
            flush=True,
        )
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(clear())
