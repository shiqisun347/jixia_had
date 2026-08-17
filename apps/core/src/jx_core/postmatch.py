"""Post-match transcript submission and AI judging runtime."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .agent.llm import LlmCapacityLimiter, LlmProviderError, OpenAIStreamingClient
from .config import Settings
from .data_capture.content import CAPTURE_VERSION, store_content_blob
from .models import (
    ExternalCall,
    JudgeProfile,
    JudgeResult,
    Match,
    MatchParticipant,
    ModelProfile,
    Room,
    Speech,
)
from .security.crypto import decrypt_secret


def _parse_result(
    text: str,
    participant_ids: set[str],
    participant_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.removeprefix("```").removeprefix("json").removesuffix("```").strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise LlmProviderError("judge_result_invalid") from error
    if not isinstance(parsed, dict):
        raise LlmProviderError("judge_result_invalid")
    parsed = cast(dict[str, Any], parsed)
    winner = {
        "正方": "AFFIRMATIVE",
        "反方": "NEGATIVE",
        "平局": "DRAW",
        "平手": "DRAW",
    }.get(str(parsed.get("winner")), parsed.get("winner"))
    parsed["winner"] = winner
    if winner not in {"AFFIRMATIVE", "NEGATIVE", "DRAW"}:
        raise LlmProviderError("judge_result_invalid")
    team_scores_value = parsed.get("team_scores") or parsed.get("scores")
    if not isinstance(team_scores_value, dict):
        raise LlmProviderError("judge_result_invalid")
    team_scores_raw = cast(dict[str, Any], team_scores_value)
    team_scores: dict[str, Any] = {}
    for key, value in team_scores_raw.items():
        side = {"正方": "AFFIRMATIVE", "反方": "NEGATIVE"}.get(str(key), str(key))
        team_scores[side] = value
    if set(team_scores) != {"AFFIRMATIVE", "NEGATIVE"}:
        raise LlmProviderError("judge_result_invalid")
    for side in ("AFFIRMATIVE", "NEGATIVE"):
        scores_value = team_scores.get(side)
        if scores_value is None:
            raise LlmProviderError("judge_result_invalid")
        if not isinstance(scores_value, dict):
            raise LlmProviderError("judge_result_invalid")
        scores_raw = cast(dict[str, Any], scores_value)
        aliases = {
            "论点": "argument",
            "立论": "argument",
            "反驳": "rebuttal",
            "证据": "evidence",
            "事实与证据": "evidence",
            "团队协作": "teamwork",
            "协作": "teamwork",
            "表达与规则": "expression",
            "表达": "expression",
        }
        scores = {aliases.get(str(key), str(key)): value for key, value in scores_raw.items()}
        if set(scores) != {
            "argument",
            "rebuttal",
            "evidence",
            "teamwork",
            "expression",
        }:
            raise LlmProviderError("judge_result_invalid")
        limits = {
            "argument": 30,
            "rebuttal": 25,
            "evidence": 20,
            "teamwork": 15,
            "expression": 10,
        }
        for key, maximum in limits.items():
            score = scores.get(key)
            if not isinstance(score, (int, float)) or not 0 <= float(score) <= maximum:
                raise LlmProviderError("judge_result_invalid")
    participants_value = parsed.get("participants")
    if not isinstance(participants_value, list):
        raise LlmProviderError("judge_result_invalid")
    participants = cast(list[Any], participants_value)
    if participant_records is not None and len(participants) == len(participant_records):
        by_name = {
            str(item.get("name")): str(item["participant_id"]) for item in participant_records
        }
        by_seat = {
            f"{item.get('side')}-{item.get('seat_no')}": str(item["participant_id"])
            for item in participant_records
        }
        for index, item_value in enumerate(participants):
            if not isinstance(item_value, dict):
                continue
            item = cast(dict[str, Any], item_value)
            candidate = str(item.get("participant_id", ""))
            if candidate not in participant_ids:
                candidate = by_name.get(candidate, by_seat.get(candidate, ""))
            if candidate not in participant_ids:
                candidate = str(participant_records[index]["participant_id"])
            item["participant_id"] = candidate
    normalized_ids: set[str] = set()
    for item_value in participants:
        if not isinstance(item_value, dict):
            raise LlmProviderError("judge_result_invalid")
        item = cast(dict[str, Any], item_value)
        normalized_ids.add(str(item.get("participant_id")))
    if normalized_ids != participant_ids:
        raise LlmProviderError("judge_result_invalid")
    for item_value in participants:
        item = cast(dict[str, Any], item_value)
        if not isinstance(item.get("score"), (int, float)) or not 0 <= float(item["score"]) <= 20:
            raise LlmProviderError("judge_result_invalid")
    if not isinstance(parsed.get("team_comments"), dict):
        raise LlmProviderError("judge_result_invalid")
    parsed["team_scores"] = team_scores
    return parsed


class PostmatchService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        limiter: LlmCapacityLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._limiter = limiter or LlmCapacityLimiter(settings.llm_global_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def request_judge(self, match_id: UUID, *, force: bool = False) -> UUID | None:
        async with self._session_factory() as session:
            async with session.begin():
                match = await session.get(Match, match_id, with_for_update=True)
                if match is None or match.status != "FINISHED":
                    return None
                latest = await session.scalar(
                    select(JudgeResult)
                    .where(JudgeResult.match_id == match_id)
                    .order_by(JudgeResult.created_at.desc())
                    .limit(1)
                )
                if latest is not None and latest.status in {"PENDING", "RUNNING"} and not force:
                    return latest.id
                profile = await session.scalar(
                    select(JudgeProfile)
                    .where(JudgeProfile.status == "ENABLED")
                    .order_by(JudgeProfile.updated_at.desc())
                    .limit(1)
                )
                if profile is None:
                    model = await session.scalar(
                        select(ModelProfile)
                        .where(ModelProfile.status == "ENABLED")
                        .order_by(ModelProfile.updated_at.desc())
                        .limit(1)
                    )
                    if model is None:
                        return None
                    profile = JudgeProfile(
                        model_profile_id=model.id,
                        system_prompt="你是客观、简洁的中文辩论裁判，只输出 JSON。",
                        judge_prompt=(
                            "按 argument/rebuttal/evidence/teamwork/expression 五项评分。"
                            "每方总计 100；每名辩手 0-20。严格输出 winner、team_scores、"
                            "participants、team_comments。"
                        ),
                    )
                    session.add(profile)
                    await session.flush()
                participants = list(
                    (
                        await session.scalars(
                            select(MatchParticipant)
                            .where(MatchParticipant.match_id == match_id)
                            .order_by(MatchParticipant.side, MatchParticipant.seat_no)
                        )
                    ).all()
                )
                speeches = list(
                    (
                        await session.scalars(
                            select(Speech)
                            .where(Speech.match_id == match_id, Speech.status == "FINALIZED")
                            .order_by(Speech.created_at)
                        )
                    ).all()
                )
                room = await session.get(Room, match.room_id)
                snapshot = {
                    "topic": (room.topic_snapshot if room else {}).get("title", ""),
                    "participants": [
                        {
                            "participant_id": str(item.id),
                            "name": item.display_name,
                            "kind": item.kind,
                            "side": item.side,
                            "seat_no": item.seat_no,
                        }
                        for item in participants
                    ],
                    "history": [
                        {
                            "speaker": f"{speech.side}-{speech.seat_no}",
                            "content": speech.display_text or "",
                        }
                        for speech in speeches
                    ],
                }
                result = JudgeResult(
                    match_id=match_id,
                    judge_profile_id=profile.id,
                    context_version=match.context_version,
                    input_snapshot=snapshot,
                )
                session.add(result)
                await session.flush()
                result_id = result.id
        task = asyncio.create_task(self._execute(result_id), name=f"judge-{result_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return result_id

    async def _execute(self, result_id: UUID) -> None:
        for attempt in (1, 2):
            external_call_id: UUID | None = None
            raw_response: str | None = None
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        row = await session.get(JudgeResult, result_id, with_for_update=True)
                        if row is None:
                            return
                        row.status = "RUNNING"
                        row.attempt_no = attempt
                        profile = await session.get(JudgeProfile, row.judge_profile_id)
                        model = (
                            await session.get(ModelProfile, profile.model_profile_id)
                            if profile
                            else None
                        )
                        if (
                            profile is None
                            or model is None
                            or not model.base_url
                            or not model.model_id
                            or model.api_key_ciphertext is None
                            or model.api_key_nonce is None
                            or self._settings.llm_key_encryption_key is None
                        ):
                            raise LlmProviderError("judge_profile_unavailable")
                        key = decrypt_secret(
                            model.api_key_ciphertext,
                            model.api_key_nonce,
                            self._settings.llm_key_encryption_key.get_secret_value(),
                        )
                        prompt = (
                            (profile.judge_prompt or "请严格按 JSON 评分。")
                            + "\n"
                            + json.dumps(row.input_snapshot, ensure_ascii=False)
                        )
                        messages = [
                            {
                                "role": "system",
                                "content": profile.system_prompt or "你是客观的中文辩论裁判。",
                            },
                            {"role": "user", "content": prompt},
                        ]
                        config = dict(profile.generation_params)
                        participant_records = cast(
                            list[dict[str, Any]], row.input_snapshot.get("participants", [])
                        )
                        participant_ids = {
                            str(item["participant_id"]) for item in participant_records
                        }
                        request_payload = {
                            "model": model.model_id,
                            "messages": messages,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                            "max_tokens": 1600,
                            "generation_params": {
                                **config,
                                "enable_thinking": config.get("enable_thinking", False),
                            },
                        }
                        request_blob_id = await store_content_blob(
                            session,
                            content_kind="REQUEST",
                            payload=request_payload,
                        )
                        external_call_id = uuid4()
                        row.request_blob_id = request_blob_id
                        row.capture_version = CAPTURE_VERSION
                        row.capture_completeness = "COMPLETE"
                        session.add(
                            ExternalCall(
                                id=external_call_id,
                                call_kind="JUDGE",
                                provider="OPENAI_COMPATIBLE",
                                operation="chat.completions.stream",
                                model=model.model_id,
                                attempt_no=attempt,
                                status="STARTED",
                                match_id=row.match_id,
                                judge_result_id=row.id,
                                context_version=row.context_version,
                                request_blob_id=request_blob_id,
                                started_at=datetime.now(UTC),
                            )
                        )
                leases = await self._limiter.acquire(
                    model.name, model.max_concurrency, timeout_seconds=3
                )
                client = OpenAIStreamingClient(
                    base_url=model.base_url, api_key=key, model=model.model_id
                )
                try:
                    result = await client.stream_chat(
                        messages=messages,
                        max_tokens=1600,
                        generation_params=config,
                        on_delta=lambda _: _async_noop(),
                    )
                    raw_response = result.text
                finally:
                    await client.close()
                    self._limiter.release(leases)
                parsed = _parse_result(result.text, participant_ids, participant_records)
                async with self._session_factory() as session:
                    async with session.begin():
                        row = await session.get(JudgeResult, result_id, with_for_update=True)
                        if row is not None:
                            response_blob_id = await store_content_blob(
                                session,
                                content_kind="RESPONSE",
                                payload={"text": result.text},
                            )
                            row.status = "SUCCEEDED"
                            row.result = parsed
                            row.response_blob_id = response_blob_id
                            row.completed_at = datetime.now(UTC)
                            call = await session.get(
                                ExternalCall, external_call_id, with_for_update=True
                            )
                            if call is not None:
                                call.status = "SUCCEEDED"
                                call.response_blob_id = response_blob_id
                                call.first_result_latency_ms = result.first_token_latency_ms
                                call.completed_latency_ms = result.completed_latency_ms
                                call.completion_tokens = result.completion_tokens
                                call.first_result_at = call.started_at + timedelta(
                                    milliseconds=max(0, result.first_token_latency_ms)
                                )
                                call.completed_at = datetime.now(UTC)
                return
            except Exception as error:
                async with self._session_factory() as session:
                    async with session.begin():
                        call = (
                            await session.get(ExternalCall, external_call_id, with_for_update=True)
                            if external_call_id is not None
                            else None
                        )
                        if call is not None and call.status == "STARTED":
                            if raw_response is not None:
                                response_blob_id = await store_content_blob(
                                    session,
                                    content_kind="RESPONSE",
                                    payload={"text": raw_response},
                                )
                                call.response_blob_id = response_blob_id
                            call.status = "FAILED"
                            call.error_code = getattr(error, "code", "judge_failed")
                            call.completed_at = datetime.now(UTC)
                if attempt == 2:
                    async with self._session_factory() as session:
                        async with session.begin():
                            row = await session.get(JudgeResult, result_id, with_for_update=True)
                            if row is not None:
                                row.status = "FAILED"
                                row.error_code = getattr(error, "code", "judge_failed")
                                row.completed_at = datetime.now(UTC)


async def _async_noop() -> None:
    return


__all__ = ["PostmatchService"]
