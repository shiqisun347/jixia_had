"""Idempotent full leaderboard snapshot worker."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .task_queue import claim_next, complete, fail


async def ensure_daily_tasks(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            for task_type in (
                "LEADERBOARD_DAILY",
                "TRANSCRIPT_AUTO_ARCHIVE",
                "FILE_CLEANUP",
            ):
                await session.execute(
                    text(
                        """
                        INSERT INTO background_tasks
                          (id, task_type, payload, status, attempts, max_attempts,
                           available_at, created_at, updated_at)
                        SELECT gen_random_uuid(), CAST(:task_type AS varchar), '{}'::jsonb,
                               'PENDING', 0, 2,
                               now(), now(), now()
                        WHERE NOT EXISTS (
                          SELECT 1 FROM background_tasks
                          WHERE task_type = CAST(:task_type AS varchar)
                            AND created_at >= date_trunc('day', now())
                        )
                        """
                    ),
                    {"task_type": task_type},
                )


async def process_one_leaderboard(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="LEADERBOARD_DAILY")
    if claim is None:
        return False
    try:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        WITH latest AS (
                            SELECT DISTINCT ON (jr.match_id) jr.match_id, jr.result
                            FROM judge_results jr
                            JOIN matches m ON m.id = jr.match_id AND m.status = 'FINISHED'
                            WHERE jr.status = 'SUCCEEDED'
                            ORDER BY jr.match_id, jr.created_at DESC
                        )
                        SELECT latest.match_id, latest.result, mp.id AS match_participant_id,
                               COALESCE(mp.user_id, mp.agent_profile_id) AS participant_id,
                               mp.kind, mp.display_name, mp.side
                        FROM latest
                        JOIN match_participants mp ON mp.match_id = latest.match_id
                        """
                    )
                )
            ).all()
        totals: dict[tuple[str, UUID], dict[str, Any]] = defaultdict(
            lambda: {
                "points": 0,
                "wins": 0,
                "matches": 0,
                "scores": [],
                "name": "",
                "match_outcomes": {},
            }
        )
        for (
            match_id,
            result_value,
            match_participant_id,
            participant_id,
            kind,
            display_name,
            side,
        ) in rows:
            result = cast(dict[str, Any], result_value)
            personal = next(
                (
                    item
                    for item in cast(list[dict[str, Any]], result.get("participants", []))
                    if str(item.get("participant_id")) == str(match_participant_id)
                ),
                None,
            )
            if personal is None:
                continue
            winner = str(result.get("winner"))
            outcome = 20 if winner == "DRAW" else 30 if winner == side else 10
            bucket = totals[(str(kind), participant_id)]
            bucket["name"] = str(display_name)
            score = int(float(personal.get("score", 0)))
            bucket["points"] += score
            match_outcomes = cast(dict[UUID, tuple[int, bool]], bucket["match_outcomes"])
            previous_outcome = match_outcomes.get(match_id)
            current_outcome = (outcome, winner == side)
            if previous_outcome is None:
                bucket["points"] += outcome
                bucket["wins"] += int(winner == side)
                bucket["matches"] += 1
                match_outcomes[match_id] = current_outcome
            elif current_outcome[0] > previous_outcome[0]:
                bucket["points"] += current_outcome[0] - previous_outcome[0]
                bucket["wins"] += int(current_outcome[1]) - int(previous_outcome[1])
                match_outcomes[match_id] = current_outcome
            cast(list[float], bucket["scores"]).append(float(score))
        generated_at = datetime.now(UTC)
        batch_id = uuid4()
        async with session_factory() as session:
            async with session.begin():
                for kind in ("HUMAN", "AGENT"):
                    ranked = sorted(
                        ((key, value) for key, value in totals.items() if key[0] == kind),
                        key=lambda item: (
                            -int(item[1]["points"]),
                            -int(item[1]["wins"]),
                            -sum(cast(list[float], item[1]["scores"])),
                            str(item[0][1]),
                        ),
                    )
                    for rank, ((_, participant_id), value) in enumerate(ranked, 1):
                        scores = cast(list[float], value["scores"])
                        await session.execute(
                            text(
                                """
                                INSERT INTO leaderboard_snapshots
                                (id, batch_id, kind, rank, participant_id, display_name,
                                 points, wins, matches, average_personal_score, generated_at)
                                VALUES (:id, :batch_id, :kind, :rank, :participant_id,
                                        :display_name, :points, :wins, :matches,
                                        :average, :generated_at)
                                """
                            ),
                            {
                                "id": uuid4(),
                                "batch_id": batch_id,
                                "kind": kind,
                                "rank": rank,
                                "participant_id": participant_id,
                                "display_name": value["name"],
                                "points": value["points"],
                                "wins": value["wins"],
                                "matches": value["matches"],
                                "average": sum(scores) / len(scores),
                                "generated_at": generated_at,
                            },
                        )
        async with session_factory() as session:
            await complete(session, task_id=claim.task_id)
    except Exception:
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code="leaderboard_rebuild_failed")
    return True


async def process_one_transcript_archive(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="TRANSCRIPT_AUTO_ARCHIVE")
    if claim is None:
        return False
    try:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO transcript_submissions
                          (id, match_id, user_id, context_version, auto_submitted)
                        SELECT gen_random_uuid(), mp.match_id, mp.user_id, m.context_version, true
                        FROM matches m
                        JOIN match_participants mp ON mp.match_id = m.id
                        WHERE m.status = 'FINISHED'
                          AND m.ended_at <= now() - interval '24 hours'
                          AND mp.kind = 'HUMAN' AND mp.user_id IS NOT NULL
                        ON CONFLICT (match_id, user_id) DO UPDATE
                          SET context_version = EXCLUDED.context_version,
                              auto_submitted = true,
                              submitted_at = now()
                        """
                    )
                )
                await session.execute(
                    text(
                        "UPDATE matches SET archived_at = COALESCE(archived_at, ended_at) "
                        "WHERE status = 'FINISHED' AND ended_at <= now() - interval '24 hours'"
                    )
                )
        async with session_factory() as session:
            await complete(session, task_id=claim.task_id)
    except Exception:
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code="transcript_archive_failed")
    return True


__all__ = ["ensure_daily_tasks", "process_one_leaderboard", "process_one_transcript_archive"]
