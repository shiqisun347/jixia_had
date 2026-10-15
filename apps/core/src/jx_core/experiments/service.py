"""Transactional services and a single experiment-context resolver."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import secrets
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import Select, Text, and_, delete, func, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from ..audit.service import AuditService
from ..auth.errors import APIError, AuthError
from ..auth.passwords import PasswordService
from ..legal.terms import get_current_human_participation_terms
from ..models import (
    AgentProfile,
    BackgroundTask,
    ExperimentBatch,
    ExperimentConsent,
    ExperimentExpert,
    ExperimentMatchAttempt,
    ExperimentResultOverride,
    ExperimentTeam,
    ExperimentTeamMember,
    ExpertAnnotationAnswer,
    ExpertAnnotationTask,
    FormatVersion,
    FreeDebateOpportunity,
    Match,
    MatchQuestionnaire,
    ModelProfile,
    ParticipantAnnotationAnswer,
    ParticipantAnnotationItem,
    ParticipantAnnotationTask,
    PostmatchSurveyTask,
    Room,
    RoomMember,
    Rule,
    ScheduledMatch,
    ScheduledSeat,
    Seat,
    Speech,
    Topic,
    User,
    UserConsent,
    VoiceProfile,
)
from ..rooms.service import RoomService
from ..survey_definitions import (
    AI_APPROPRIATENESS,
    AI_MATCH_REASONS,
    HUMAN_GOALS,
    HUMAN_PRIMARY_REASONS,
)
from ..users.avatar_catalog import random_avatar_key
from .postmatch import QUESTIONNAIRE_VERSION
from .schedule import (
    MatchSpec,
    ScheduleIssue,
    SeatSpec,
    TeamSpec,
    generate_formal_schedule,
    generate_training_schedule,
    schedule_from_csv,
    schedule_to_csv,
    validate_formal_schedule,
    validate_training_schedule,
)
from .schemas import (
    ExperimentAccountGenerationResponse,
    ExperimentAppointmentResponse,
    ExperimentBatchCreateRequest,
    ExperimentBatchDisableResponse,
    ExperimentBatchProgressResponse,
    ExperimentBatchUpdateRequest,
    ExperimentEnterResponse,
    ExperimentExpertBinding,
    ExperimentGeneratedAccountResponse,
    ExperimentJobResponse,
    ExperimentMatchProgressResponse,
    ExperimentMemberBinding,
    ExperimentPublishResponse,
    ExperimentResultOverrideRequest,
    ExperimentRetentionRequest,
    ExperimentRosterDetailResponse,
    ExperimentRosterPutRequest,
    ExperimentRosterResponse,
    ExperimentScheduleCsvImportResponse,
    ExperimentScheduledMatchResponse,
    ExperimentScheduleResponse,
    ExperimentSeatResponse,
    ExperimentTeamBinding,
    ExpertAnnotationItemResponse,
    ExpertAnnotationSaveRequest,
    ExpertAnnotationTaskResponse,
    ParticipantAnnotationAnswerSaveRequest,
    ParticipantAnnotationItemResponse,
    ParticipantAnnotationTaskResponse,
    ParticipantQuestionnaireResponse,
    ParticipantQuestionnaireSubmitRequest,
)


@dataclass(frozen=True, slots=True)
class ExperimentContext:
    batch_id: UUID
    batch_status: str
    scheduled_match_id: UUID
    scheduled_match_kind: str
    scheduled_match_status: str
    schedule_version: int
    attempt_id: UUID
    attempt_no: int
    attempt_status: str
    room_id: UUID | None
    match_id: UUID | None

    @property
    def runtime_enabled(self) -> bool:
        return self.batch_status == "PUBLISHED" and self.attempt_status in {
            "CREATED",
            "WAITING",
            "RUNNING",
            "PAUSED",
        }


def _context_query() -> Select[tuple[ExperimentMatchAttempt, ScheduledMatch, ExperimentBatch]]:
    return (
        select(ExperimentMatchAttempt, ScheduledMatch, ExperimentBatch)
        .join(ScheduledMatch, ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id)
        .join(ExperimentBatch, ExperimentBatch.id == ScheduledMatch.batch_id)
    )


class ExperimentService:
    EXPERIMENT_PAGE_DEFAULT = 50
    EXPERIMENT_PAGE_MAX = 50
    # HTTP callers can continue through the cursor page by page.  These limits
    # only protect the legacy complete-array compatibility helpers from
    # retaining an unbounded history or following a broken unique cursor.
    EXPERIMENT_HISTORY_MAX_PAGES = 200
    EXPERIMENT_HISTORY_MAX_ROWS = 10_000
    # Annotation pages are intentionally smaller than the index pages.  A
    # task carries nested transcript/context JSON, so the task count alone is
    # not a sufficient memory bound.
    ANNOTATION_PAGE_DEFAULT = 25
    ANNOTATION_PAGE_MAX = 50
    ANNOTATION_MAX_ITEMS_PER_TASK = 512
    ANNOTATION_MAX_ANSWERS_PER_TASK = 2_048
    ANNOTATION_MAX_TASK_PAYLOAD_BYTES = 8 * 1024 * 1024
    ANNOTATION_MAX_PAGE_PAYLOAD_BYTES = 64 * 1024 * 1024
    ANNOTATION_MAX_AUDIO_AUTH_ROWS = 4_096
    ANNOTATION_MAX_AUDIO_AUTH_BYTES = 64 * 1024 * 1024
    _DEFAULT_ACCOUNT_PASSWORD = "Jixia2026"
    _HUMAN_PRIMARY_REASONS = set(HUMAN_PRIMARY_REASONS)
    _HUMAN_GOALS = set(HUMAN_GOALS)

    async def _freeze_rule_config(
        self, session: AsyncSession, *, rule_id: UUID
    ) -> tuple[Rule, dict[str, Any]]:
        room_service = RoomService()
        try:
            rule, rule_snapshot = await room_service.load_rule_snapshot(session, rule_id=rule_id)
        except AuthError as error:
            raise APIError(error.code) from None
        if rule.side_size != 4:
            raise APIError("rule_unavailable")
        config_snapshot = await room_service.load_rule_config_snapshot(session, rule=rule)
        return rule, {
            "schema": "experiment-rule-config-v1",
            "rule": rule_snapshot,
            "config": config_snapshot,
        }

    @staticmethod
    def _current_batch_snapshots(
        batch: ExperimentBatch,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        snapshot = batch.rule_config_snapshot
        if not isinstance(snapshot, dict) or snapshot.get("schema") != "experiment-rule-config-v1":
            return None
        rule_snapshot = cast(object, snapshot.get("rule"))
        config_snapshot = cast(object, snapshot.get("config"))
        if not isinstance(rule_snapshot, dict) or not isinstance(config_snapshot, dict):
            return None
        typed_rule_snapshot = cast(dict[str, Any], rule_snapshot)
        typed_config_snapshot = cast(dict[str, Any], config_snapshot)
        if typed_config_snapshot.get("schema") != "rule-config-v1":
            return None
        return typed_rule_snapshot, typed_config_snapshot

    @staticmethod
    def _snapshot_agent_ids(batch: ExperimentBatch) -> list[UUID]:
        snapshots = ExperimentService._current_batch_snapshots(batch)
        if snapshots is None:
            return []
        agents = cast(object, snapshots[1].get("agents"))
        if not isinstance(agents, list):
            return []
        result: list[UUID] = []
        for raw_agent in cast(list[object], agents):
            agent = cast(dict[str, Any], raw_agent) if isinstance(raw_agent, dict) else None
            if not isinstance(agent, dict) or agent.get("status") != "ENABLED":
                continue
            try:
                result.append(UUID(str(agent["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        return result

    _AI_MATCH = set(AI_MATCH_REASONS)
    _AI_APPROPRIATENESS = set(AI_APPROPRIATENESS)
    _EXPERT_ACTIONS = {
        "回应对手",
        "接续队友",
        "补足缺口",
        "主动推进",
        "调整方向",
        "让出机会",
        "其他",
    }

    @classmethod
    def _page_limit(cls, value: int) -> int:
        if value < 1 or value > cls.EXPERIMENT_PAGE_MAX:
            raise APIError("experiment_query_invalid")
        return value

    @staticmethod
    def _validate_annotation_history_cursor(
        previous: tuple[datetime, UUID] | None,
        next_cursor: tuple[datetime, UUID],
        seen: set[tuple[datetime, UUID]],
    ) -> None:
        """Reject cursor loops and cursors that do not move through the sort order.

        Annotation task pages are ordered by ``created_at DESC, id DESC``.  A
        compatibility helper must therefore receive a strictly older cursor on
        every page.  Checking this before retaining the next page prevents a
        broken page implementation from relying on the global page cap to
        eventually fail.
        """

        if next_cursor in seen or next_cursor == previous:
            raise APIError("experiment_query_invalid")
        if previous is not None and next_cursor >= previous:
            raise APIError("experiment_query_invalid")

    @staticmethod
    def _encode_batch_cursor(row: ExperimentBatch) -> str:
        payload = f"{row.created_at.isoformat()}|{row.id}"
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_batch_cursor(value: str) -> tuple[datetime, UUID]:
        try:
            padded = value + "=" * (-len(value) % 4)
            timestamp, row_id = base64.urlsafe_b64decode(padded).decode().rsplit("|", 1)
            created_at = datetime.fromisoformat(timestamp)
            if created_at.tzinfo is None:
                raise ValueError
            return created_at, UUID(row_id)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            raise APIError("experiment_query_invalid") from None

    @staticmethod
    def _encode_appointment_cursor(row: ScheduledMatch) -> str:
        payload = json.dumps(
            {
                "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
                "round_no": row.round_no,
                "match_no": row.match_no,
                "id": str(row.id),
            },
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_appointment_cursor(value: str) -> tuple[datetime | None, int, int, UUID]:
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
            if not isinstance(decoded, dict):
                raise ValueError
            raw = cast(dict[str, Any], decoded)
            scheduled_at_raw = raw.get("scheduled_at")
            scheduled_at = (
                datetime.fromisoformat(str(scheduled_at_raw))
                if scheduled_at_raw is not None
                else None
            )
            if scheduled_at is not None and scheduled_at.tzinfo is None:
                raise ValueError
            round_no = int(str(raw["round_no"]))
            match_no = int(str(raw["match_no"]))
            row_id = UUID(str(raw["id"]))
            if round_no < 1 or match_no < 1:
                raise ValueError
            return scheduled_at, round_no, match_no, row_id
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            raise APIError("experiment_query_invalid") from None

    @classmethod
    def _validate_batch_history_cursor(
        cls,
        previous: tuple[datetime, UUID] | None,
        encoded: str,
        seen: set[tuple[datetime, UUID]],
    ) -> tuple[datetime, UUID]:
        """Decode and validate a batch cursor before retaining another page.

        list_batches_page orders by created_at DESC, id ASC. A compatibility
        helper must therefore receive a cursor that is strictly older in that
        same order. Validating the decoded tuple, rather than only the opaque
        string, also rejects the same cursor encoded with different optional
        padding.
        """

        next_cursor = cls._decode_batch_cursor(encoded)
        if next_cursor in seen or (
            previous is not None
            and not (
                next_cursor[0] < previous[0]
                or (next_cursor[0] == previous[0] and next_cursor[1] > previous[1])
            )
        ):
            raise APIError("experiment_query_invalid")
        return next_cursor

    @classmethod
    def _validate_appointment_history_cursor(
        cls,
        previous: tuple[datetime | None, int, int, UUID] | None,
        encoded: str,
        seen: set[tuple[datetime | None, int, int, UUID]],
    ) -> tuple[datetime | None, int, int, UUID]:
        """Decode and validate an appointment cursor in query sort order.

        Appointments are ordered by scheduled_at ASC NULLS LAST followed by
        round_no, match_no, id ascending. The null branch is kept explicit
        because Python cannot compare None and datetimes.
        """

        next_cursor = cls._decode_appointment_cursor(encoded)
        if next_cursor in seen:
            raise APIError("experiment_query_invalid")
        if previous is not None:
            previous_at, previous_round, previous_match, previous_id = previous
            next_at, next_round, next_match, next_id = next_cursor
            previous_slot = (previous_round, previous_match, previous_id)
            next_slot = (next_round, next_match, next_id)
            if previous_at is None:
                progressed = next_at is None and next_slot > previous_slot
            elif next_at is None:
                # NULL values are last in the SQL ordering.
                progressed = True
            elif next_at > previous_at:
                progressed = True
            elif next_at == previous_at:
                progressed = next_slot > previous_slot
            else:
                progressed = False
            if not progressed:
                raise APIError("experiment_query_invalid")
        return next_cursor

    async def generate_anonymous_accounts(
        self, session: AsyncSession, *, actor_user_id: UUID
    ) -> ExperimentAccountGenerationResponse:
        codes = tuple(f"P{index:02d}" for index in range(1, 19)) + tuple(
            f"E{index:02d}" for index in range(1, 4)
        )
        passwords = PasswordService()
        results: list[ExperimentGeneratedAccountResponse] = []
        async with session.begin():
            existing = {
                user.username_normalized: user
                for user in (
                    await session.scalars(
                        select(User).where(
                            User.username_normalized.in_([code.lower() for code in codes])
                        )
                    )
                ).all()
            }
            for code in codes:
                user = existing.get(code.lower())
                if user is not None:
                    if user.role != "USER" or user.status != "ACTIVE":
                        raise APIError(
                            "experiment_account_conflict",
                            {"accounts": f"{code} 已存在但不是可用普通账号"},
                        )
                    if user.real_name in {"待绑定参与者", "待绑定专家"}:
                        user.real_name = code
                    user.password_hash = passwords.hash(self._DEFAULT_ACCOUNT_PASSWORD)
                    user.must_change_password = False
                    results.append(
                        ExperimentGeneratedAccountResponse(
                            code=code,
                            user_id=user.id,
                            username=user.username,
                            temporary_password=None,
                            created=False,
                        )
                    )
                    continue
                default_password_hash = passwords.hash(self._DEFAULT_ACCOUNT_PASSWORD)
                user = User(
                    username=code,
                    username_normalized=code.lower(),
                    real_name=code,
                    password_hash=default_password_hash,
                    must_change_password=False,
                    default_avatar_key=random_avatar_key("HUMAN"),
                )
                session.add(user)
                await session.flush()
                results.append(
                    ExperimentGeneratedAccountResponse(
                        code=code,
                        user_id=user.id,
                        username=user.username,
                        temporary_password=None,
                        created=True,
                    )
                )
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.experiment.accounts_generated",
                target_type="experiment_accounts",
                target_id="P01-P18,E01-E03",
                details={"created_count": sum(item.created for item in results)},
            )
            await session.flush()
        created_count = sum(item.created for item in results)
        return ExperimentAccountGenerationResponse(
            accounts=results,
            created_count=created_count,
            existing_count=len(results) - created_count,
            default_password=self._DEFAULT_ACCOUNT_PASSWORD,
        )

    async def _draft_batch(
        self, session: AsyncSession, batch_id: UUID, *, lock: bool = False
    ) -> ExperimentBatch:
        query = select(ExperimentBatch).where(ExperimentBatch.id == batch_id)
        if lock:
            query = query.with_for_update()
        batch = await session.scalar(query)
        if batch is None:
            raise APIError("experiment_batch_not_found")
        if batch.status != "DRAFT":
            raise APIError("experiment_batch_locked")
        return batch

    async def create_batch(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        payload: ExperimentBatchCreateRequest,
    ) -> ExperimentBatch:
        try:
            async with session.begin():
                _, rule_config_snapshot = await self._freeze_rule_config(
                    session, rule_id=payload.rule_id
                )
                batch = ExperimentBatch(
                    code=payload.code,
                    title=payload.title,
                    # Legacy columns remain non-null for published schema compatibility.
                    consent_version="legacy-unused",
                    consent_summary="",
                    consent_document="",
                    rule_id=payload.rule_id,
                    format_version_id=None,
                    rule_config_snapshot=rule_config_snapshot,
                    training_room_quota=payload.training_room_quota,
                    created_by=actor_user_id,
                )
                session.add(batch)
                await session.flush()
        except IntegrityError as error:
            if "uq_experiment_batches_code" in str(error.orig):
                raise APIError("experiment_batch_code_taken") from None
            raise
        return batch

    async def update_batch(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        actor_user_id: UUID,
        payload: ExperimentBatchUpdateRequest,
    ) -> ExperimentBatch:
        try:
            async with session.begin():
                batch = await self._draft_batch(session, batch_id, lock=True)
                _, rule_config_snapshot = await self._freeze_rule_config(
                    session, rule_id=payload.rule_id
                )
                previous = {
                    "code": batch.code,
                    "title": batch.title,
                    "rule_id": str(batch.rule_id),
                    "training_room_quota": batch.training_room_quota,
                }
                batch.code = payload.code
                batch.title = payload.title
                batch.rule_id = payload.rule_id
                batch.format_version_id = None
                batch.rule_config_snapshot = rule_config_snapshot
                batch.training_room_quota = payload.training_room_quota
                AuditService().record(
                    session,
                    actor_user_id=actor_user_id,
                    action="admin.experiment.batch_updated",
                    target_type="experiment_batch",
                    target_id=str(batch.id),
                    details={
                        "previous": previous,
                        "current": {
                            "code": batch.code,
                            "title": batch.title,
                            "rule_id": str(batch.rule_id),
                        },
                    },
                )
                await session.flush()
        except IntegrityError as error:
            if "uq_experiment_batches_code" in str(error.orig):
                raise APIError("experiment_batch_code_taken") from None
            raise
        return batch

    async def delete_draft_batch(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        actor_user_id: UUID,
    ) -> None:
        async with session.begin():
            batch = await self._draft_batch(session, batch_id, lock=True)
            scheduled_ids = select(ScheduledMatch.id).where(ScheduledMatch.batch_id == batch_id)
            attempt_id = await session.scalar(
                select(ExperimentMatchAttempt.id)
                .where(ExperimentMatchAttempt.scheduled_match_id.in_(scheduled_ids))
                .limit(1)
            )
            if attempt_id is not None:
                raise APIError("experiment_batch_has_attempts")

            team_ids = select(ExperimentTeam.id).where(ExperimentTeam.batch_id == batch_id)
            await session.execute(
                delete(ScheduledSeat).where(ScheduledSeat.scheduled_match_id.in_(scheduled_ids))
            )
            await session.execute(delete(ScheduledMatch).where(ScheduledMatch.batch_id == batch_id))
            await session.execute(
                delete(ExperimentTeamMember).where(ExperimentTeamMember.batch_id == batch_id)
            )
            await session.execute(
                delete(ExperimentExpert).where(ExperimentExpert.batch_id == batch_id)
            )
            await session.execute(
                delete(ExperimentConsent).where(ExperimentConsent.batch_id == batch_id)
            )
            await session.execute(delete(ExperimentTeam).where(ExperimentTeam.id.in_(team_ids)))
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.experiment.batch_deleted",
                target_type="experiment_batch",
                target_id=str(batch.id),
                details={"code": batch.code, "title": batch.title},
            )
            await session.delete(batch)
            await session.flush()

    async def replace_roster(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        payload: ExperimentRosterPutRequest,
    ) -> ExperimentRosterResponse:
        team_codes = [team.team_code for team in payload.teams]
        agent_ids = [team.agent_profile_id for team in payload.teams]
        members = [member for team in payload.teams for member in team.members]
        participant_ids = [member.user_id for member in members]
        participant_codes = [member.participant_code for member in members]
        expert_ids = [expert.user_id for expert in payload.experts]
        expert_codes = [expert.expert_code for expert in payload.experts]
        unique_groups = (
            (team_codes, "队伍编号不可重复"),
            (agent_ids, "每支队伍必须绑定不同 Agent"),
            (participant_ids, "参与者账号不可重复"),
            (participant_codes, "参与者编号不可重复"),
            (expert_ids, "专家账号不可重复"),
            (expert_codes, "专家编号不可重复"),
        )
        for values, message in unique_groups:
            if len(values) != len(set(values)):
                raise APIError("experiment_roster_invalid", {"roster": message})
        if set(participant_ids) & set(expert_ids):
            raise APIError("experiment_roster_invalid", {"roster": "参与者账号与专家账号必须分开"})

        async with session.begin():
            batch = await self._draft_batch(session, batch_id, lock=True)
            scheduled_ids = select(ScheduledMatch.id).where(ScheduledMatch.batch_id == batch_id)
            has_attempt = await session.scalar(
                select(ExperimentMatchAttempt.id)
                .where(ExperimentMatchAttempt.scheduled_match_id.in_(scheduled_ids))
                .limit(1)
            )
            if has_attempt is not None:
                raise APIError("experiment_batch_locked")

            users = list(
                (
                    await session.scalars(
                        select(User).where(User.id.in_([*participant_ids, *expert_ids]))
                    )
                ).all()
            )
            active_user_ids = {user.id for user in users if user.status == "ACTIVE"}
            if active_user_ids != set([*participant_ids, *expert_ids]):
                raise APIError(
                    "experiment_roster_invalid",
                    {"users": "所有参与者和专家账号必须存在且处于启用状态"},
                )
            agents = list(
                (
                    await session.scalars(
                        select(AgentProfile)
                        .join(ModelProfile, ModelProfile.id == AgentProfile.model_profile_id)
                        .join(VoiceProfile, VoiceProfile.id == AgentProfile.voice_profile_id)
                        .where(
                            AgentProfile.id.in_(agent_ids),
                            ModelProfile.status == "ENABLED",
                            VoiceProfile.status == "ENABLED",
                        )
                    )
                ).all()
            )
            if batch.rule_config_snapshot is not None:
                valid_agents = {
                    agent.id
                    for agent in agents
                    if agent.status == "ENABLED"
                    and agent.rule_id == batch.rule_id
                    and agent.id in set(self._snapshot_agent_ids(batch))
                }
                agent_error = "所有固定 Agent 必须启用且属于批次绑定的赛制规则"
            else:
                valid_agents = {
                    agent.id
                    for agent in agents
                    if agent.status == "ENABLED"
                    and agent.format_version_id == batch.format_version_id
                }
                agent_error = "所有固定 Agent 必须启用且属于批次绑定的赛制版本"
            if valid_agents != set(agent_ids):
                raise APIError(
                    "experiment_roster_invalid",
                    {"agents": agent_error},
                )

            await session.execute(
                delete(ScheduledSeat).where(ScheduledSeat.scheduled_match_id.in_(scheduled_ids))
            )
            await session.execute(delete(ScheduledMatch).where(ScheduledMatch.batch_id == batch_id))
            await session.execute(
                delete(ExperimentTeamMember).where(ExperimentTeamMember.batch_id == batch_id)
            )
            await session.execute(
                delete(ExperimentExpert).where(ExperimentExpert.batch_id == batch_id)
            )
            await session.execute(delete(ExperimentTeam).where(ExperimentTeam.batch_id == batch_id))

            for team_payload in payload.teams:
                team = ExperimentTeam(
                    batch_id=batch_id,
                    team_code=team_payload.team_code,
                    agent_profile_id=team_payload.agent_profile_id,
                )
                session.add(team)
                await session.flush()
                session.add_all(
                    [
                        ExperimentTeamMember(
                            batch_id=batch_id,
                            team_id=team.id,
                            user_id=member.user_id,
                            participant_code=member.participant_code,
                        )
                        for member in team_payload.members
                    ]
                )
            session.add_all(
                [
                    ExperimentExpert(
                        batch_id=batch_id,
                        user_id=expert.user_id,
                        expert_code=expert.expert_code,
                    )
                    for expert in payload.experts
                ]
            )
            await session.flush()
        return ExperimentRosterResponse(team_count=6, participant_count=18, expert_count=3)

    async def get_roster(
        self, session: AsyncSession, *, batch_id: UUID
    ) -> ExperimentRosterDetailResponse:
        await self.get_batch(session, batch_id)
        teams = list(
            (
                await session.scalars(
                    select(ExperimentTeam)
                    .where(ExperimentTeam.batch_id == batch_id)
                    .order_by(ExperimentTeam.team_code, ExperimentTeam.id)
                )
            ).all()
        )
        members = list(
            (
                await session.scalars(
                    select(ExperimentTeamMember)
                    .where(ExperimentTeamMember.batch_id == batch_id)
                    .order_by(ExperimentTeamMember.participant_code)
                )
            ).all()
        )
        experts = list(
            (
                await session.scalars(
                    select(ExperimentExpert)
                    .where(ExperimentExpert.batch_id == batch_id)
                    .order_by(ExperimentExpert.expert_code)
                )
            ).all()
        )
        members_by_team: dict[UUID, list[ExperimentTeamMember]] = {team.id: [] for team in teams}
        for member in members:
            members_by_team.setdefault(member.team_id, []).append(member)
        return ExperimentRosterDetailResponse(
            teams=[
                ExperimentTeamBinding(
                    team_code=team.team_code,
                    agent_profile_id=team.agent_profile_id,
                    members=[
                        ExperimentMemberBinding(
                            user_id=member.user_id,
                            participant_code=member.participant_code,
                        )
                        for member in members_by_team.get(team.id, [])
                    ],
                )
                for team in teams
            ],
            experts=[
                ExperimentExpertBinding(user_id=expert.user_id, expert_code=expert.expert_code)
                for expert in experts
            ],
        )

    async def _team_specs(self, session: AsyncSession, batch_id: UUID) -> tuple[TeamSpec, ...]:
        teams = list(
            (
                await session.scalars(
                    select(ExperimentTeam)
                    .where(ExperimentTeam.batch_id == batch_id)
                    .order_by(ExperimentTeam.team_code, ExperimentTeam.id)
                )
            ).all()
        )
        members = list(
            (
                await session.scalars(
                    select(ExperimentTeamMember)
                    .where(ExperimentTeamMember.batch_id == batch_id)
                    .order_by(ExperimentTeamMember.participant_code, ExperimentTeamMember.id)
                )
            ).all()
        )
        members_by_team: dict[UUID, list[UUID]] = {}
        for member in members:
            members_by_team.setdefault(member.team_id, []).append(member.user_id)
        if len(teams) != 6 or any(len(members_by_team.get(team.id, [])) != 3 for team in teams):
            raise APIError("experiment_roster_incomplete")
        return tuple(
            TeamSpec(
                team_id=team.id,
                member_ids=tuple(members_by_team[team.id]),  # type: ignore[arg-type]
                agent_profile_id=team.agent_profile_id,
            )
            for team in teams
        )

    async def _schedule_response(
        self, session: AsyncSession, batch: ExperimentBatch
    ) -> ExperimentScheduleResponse:
        matches = list(
            (
                await session.scalars(
                    select(ScheduledMatch)
                    .where(ScheduledMatch.batch_id == batch.id)
                    .order_by(ScheduledMatch.round_no, ScheduledMatch.match_no)
                )
            ).all()
        )
        match_ids = [match.id for match in matches]
        seats = (
            list(
                (
                    await session.scalars(
                        select(ScheduledSeat)
                        .where(ScheduledSeat.scheduled_match_id.in_(match_ids))
                        .order_by(
                            ScheduledSeat.scheduled_match_id,
                            ScheduledSeat.side,
                            ScheduledSeat.seat_no,
                        )
                    )
                ).all()
            )
            if match_ids
            else []
        )
        seats_by_match: dict[UUID, list[ScheduledSeat]] = {}
        for seat in seats:
            seats_by_match.setdefault(seat.scheduled_match_id, []).append(seat)
        return ExperimentScheduleResponse(
            batch_id=batch.id,
            batch_status=batch.status,
            schedule_version=batch.schedule_version,
            matches=[
                ExperimentScheduledMatchResponse(
                    id=match.id,
                    round_no=match.round_no,
                    match_no=match.match_no,
                    topic_id=match.topic_id,
                    affirmative_team_id=match.affirmative_team_id,
                    negative_team_id=match.negative_team_id,
                    kind=cast(Literal["FORMAL", "TRAINING"], match.kind),
                    status=match.status,
                    schedule_version=match.schedule_version,
                    seats=[
                        ExperimentSeatResponse.model_validate(seat)
                        for seat in seats_by_match.get(match.id, [])
                    ],
                )
                for match in matches
            ],
        )

    async def generate_schedule(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        topic_ids: tuple[UUID, ...],
        training_topic_id: UUID,
    ) -> ExperimentScheduleResponse:
        if len(topic_ids) != 6 or len(set(topic_ids)) != 6:
            raise APIError("experiment_schedule_invalid", {"topic_ids": "必须选择 6 个不同辩题"})
        if training_topic_id in set(topic_ids):
            raise APIError(
                "experiment_schedule_invalid", {"training_topic_id": "训练题不得与正式辩题重复"}
            )
        async with session.begin():
            batch = await self._draft_batch(session, batch_id, lock=True)
            teams = await self._team_specs(session, batch_id)
            approved_topic_ids = {*topic_ids, training_topic_id}
            topics = list(
                (await session.scalars(select(Topic).where(Topic.id.in_(approved_topic_ids)))).all()
            )
            if {topic.id for topic in topics if topic.status == "ENABLED"} != approved_topic_ids:
                raise APIError(
                    "experiment_schedule_invalid",
                    {"topic_ids": "所有正式辩题和训练题必须存在且处于启用状态"},
                )
            missing_sources = [
                topic.title for topic in topics if not (topic.source_text or "").strip()
            ]
            if missing_sources:
                raise APIError(
                    "experiment_schedule_invalid",
                    {"topic_sources": f"以下辩题缺少原始来源：{'、'.join(missing_sources)}"},
                )
            existing_ids = select(ScheduledMatch.id).where(ScheduledMatch.batch_id == batch_id)
            has_attempt = await session.scalar(
                select(ExperimentMatchAttempt.id)
                .where(ExperimentMatchAttempt.scheduled_match_id.in_(existing_ids))
                .limit(1)
            )
            if has_attempt is not None:
                raise APIError("experiment_batch_locked")
            await session.execute(
                delete(ScheduledSeat).where(ScheduledSeat.scheduled_match_id.in_(existing_ids))
            )
            await session.execute(delete(ScheduledMatch).where(ScheduledMatch.batch_id == batch_id))
            draft_version = batch.schedule_version + 1
            formal_specs = generate_formal_schedule(teams, topic_ids)
            all_specs = tuple(("FORMAL", spec) for spec in formal_specs) + tuple(
                ("TRAINING", spec)
                for spec in generate_training_schedule(formal_specs, training_topic_id)
            )
            for kind, match_spec in all_specs:
                scheduled = ScheduledMatch(
                    batch_id=batch_id,
                    round_no=match_spec.round_no,
                    match_no=match_spec.match_no,
                    topic_id=match_spec.topic_id,
                    affirmative_team_id=match_spec.affirmative_team_id,
                    negative_team_id=match_spec.negative_team_id,
                    kind=kind,
                    status="DRAFT",
                    schedule_version=draft_version,
                )
                session.add(scheduled)
                await session.flush()
                session.add_all(
                    [
                        ScheduledSeat(
                            scheduled_match_id=scheduled.id,
                            side=seat.side,
                            seat_no=seat.seat_no,
                            occupant_kind=seat.occupant_kind,
                            user_id=seat.user_id,
                            agent_profile_id=seat.agent_profile_id,
                        )
                        for seat in match_spec.seats
                    ]
                )
            await session.flush()
            response = await self._schedule_response(session, batch)
            response.schedule_version = draft_version
        return response

    async def get_schedule(
        self, session: AsyncSession, *, batch_id: UUID
    ) -> ExperimentScheduleResponse:
        batch = await self.get_batch(session, batch_id)
        response = await self._schedule_response(session, batch)
        if response.matches and batch.status == "DRAFT":
            response.schedule_version = response.matches[0].schedule_version
        return response

    async def export_schedule_csv(self, session: AsyncSession, *, batch_id: UUID) -> str:
        schedule = await self.get_schedule(session, batch_id=batch_id)
        rows = tuple(
            (
                match.kind,
                MatchSpec(
                    round_no=match.round_no,
                    match_no=match.match_no,
                    topic_id=match.topic_id,
                    affirmative_team_id=match.affirmative_team_id,
                    negative_team_id=match.negative_team_id,
                    seats=tuple(
                        SeatSpec(
                            side=seat.side,
                            seat_no=seat.seat_no,
                            occupant_kind=seat.occupant_kind,
                            user_id=seat.user_id,
                            agent_profile_id=seat.agent_profile_id,
                        )
                        for seat in match.seats
                    ),
                ),
            )
            for match in schedule.matches
        )
        return schedule_to_csv(rows, schedule_version=schedule.schedule_version or 1)

    async def export_readable_schedule_csv(self, session: AsyncSession, *, batch_id: UUID) -> str:
        batch = await self.get_batch(session, batch_id)
        schedule = await self._schedule_response(session, batch)
        topic_ids = {match.topic_id for match in schedule.matches}
        team_ids = {
            team_id
            for match in schedule.matches
            for team_id in (match.affirmative_team_id, match.negative_team_id)
        }
        user_ids = {
            seat.user_id
            for match in schedule.matches
            for seat in match.seats
            if seat.user_id is not None
        }
        agent_ids = {
            seat.agent_profile_id
            for match in schedule.matches
            for seat in match.seats
            if seat.agent_profile_id is not None
        }
        topics = {
            item.id: item.title
            for item in await session.scalars(select(Topic).where(Topic.id.in_(topic_ids)))
        }
        teams = {
            item.id: item.team_code
            for item in await session.scalars(
                select(ExperimentTeam).where(ExperimentTeam.id.in_(team_ids))
            )
        }
        users = {
            item.id: item.username
            for item in await session.scalars(select(User).where(User.id.in_(user_ids)))
        }
        agents = {
            item.id: item.name
            for item in await session.scalars(
                select(AgentProfile).where(AgentProfile.id.in_(agent_ids))
            )
        }
        output = io.StringIO(newline="")
        columns = [
            "类型",
            "轮次",
            "场次",
            "辩题",
            "正方队伍",
            "反方队伍",
            "正方席位",
            "反方席位",
            "状态",
        ]
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()

        def lineup(seats: list[ExperimentSeatResponse], side: str) -> str:
            values: list[str] = []
            for seat in sorted(
                (item for item in seats if item.side == side),
                key=lambda item: item.seat_no,
            ):
                if seat.user_id is not None:
                    name = users.get(seat.user_id, "未知账号")
                elif seat.agent_profile_id is not None:
                    name = agents.get(seat.agent_profile_id, "未知 Agent")
                else:
                    name = "未知席位"
                values.append(f"{seat.seat_no}辩:{name}")
            return " / ".join(values)

        for match in schedule.matches:
            writer.writerow(
                {
                    "类型": "正式赛" if match.kind == "FORMAL" else "训练赛",
                    "轮次": match.round_no,
                    "场次": match.match_no,
                    "辩题": topics.get(match.topic_id, "未知辩题"),
                    "正方队伍": teams.get(match.affirmative_team_id, "未知队伍"),
                    "反方队伍": teams.get(match.negative_team_id, "未知队伍"),
                    "正方席位": lineup(match.seats, "AFFIRMATIVE"),
                    "反方席位": lineup(match.seats, "NEGATIVE"),
                    "状态": match.status,
                }
            )
        return "\ufeff" + output.getvalue()

    async def export_accounts_csv(self, session: AsyncSession) -> str:
        codes = [f"P{index:02d}" for index in range(1, 19)] + [
            f"E{index:02d}" for index in range(1, 4)
        ]
        users = {
            user.username.upper(): user
            for user in await session.scalars(
                select(User).where(User.username_normalized.in_([code.lower() for code in codes]))
            )
        }
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=["编号", "用户名", "默认密码", "用途", "状态"],
            lineterminator="\n",
        )
        writer.writeheader()
        for code in codes:
            user = users.get(code)
            writer.writerow(
                {
                    "编号": code,
                    "用户名": user.username if user is not None else code,
                    "默认密码": self._DEFAULT_ACCOUNT_PASSWORD,
                    "用途": "参赛者" if code.startswith("P") else "专家",
                    "状态": "已创建" if user is not None else "待创建",
                }
            )
        return "\ufeff" + output.getvalue()

    async def import_schedule_csv(
        self, session: AsyncSession, *, batch_id: UUID, csv_text: str
    ) -> ExperimentScheduleCsvImportResponse:
        version, rows, parse_issues = schedule_from_csv(csv_text)
        if parse_issues or version is None:
            raise APIError(
                "experiment_schedule_invalid",
                {
                    "csv": "; ".join(
                        f"{issue.message}{f'（{issue.field}）' if issue.field else ''}"
                        for issue in parse_issues
                    )
                },
            )
        formal_specs = tuple(spec for kind, spec in rows if kind == "FORMAL")
        training_specs = tuple(spec for kind, spec in rows if kind == "TRAINING")
        async with session.begin():
            batch = await self._draft_batch(session, batch_id, lock=True)
            if version != batch.schedule_version + 1:
                raise APIError(
                    "experiment_schedule_invalid",
                    {"schedule_version": "CSV 排表版本与当前草稿版本不一致"},
                )
            teams = await self._team_specs(session, batch_id)
            formal_topics_by_round = {
                round_no: {match.topic_id for match in formal_specs if match.round_no == round_no}
                for round_no in range(1, 7)
            }
            formal_shape_valid = len(formal_specs) == 18 and all(
                len(values) == 1 for values in formal_topics_by_round.values()
            )
            formal_topic_ids = (
                tuple(next(iter(formal_topics_by_round[round_no])) for round_no in range(1, 7))
                if formal_shape_valid
                else ()
            )
            training_topic_ids = {match.topic_id for match in training_specs}
            issues: list[ScheduleIssue] = []
            if not formal_shape_valid:
                issues.append(
                    ScheduleIssue("formal_shape", "正式排表必须包含第 1 至 6 轮、每轮同题的 18 场")
                )
            else:
                issues.extend(
                    validate_formal_schedule(
                        formal_specs,
                        teams=teams,
                        topic_ids=formal_topic_ids,
                    )
                )
            if len(training_topic_ids) != 1:
                issues.append(ScheduleIssue("training_topic", "训练赛必须使用同一个独立训练题"))
            else:
                training_topic_id = next(iter(training_topic_ids))
                if training_topic_id in set(formal_topic_ids):
                    issues.append(ScheduleIssue("training_topic", "训练题不得与正式辩题重复"))
                issues.extend(
                    validate_training_schedule(
                        training_specs,
                        teams=teams,
                        training_topic_id=training_topic_id,
                    )
                )
            if issues:
                raise APIError(
                    "experiment_schedule_invalid",
                    {"csv": "; ".join(dict.fromkeys(issue.message for issue in issues))},
                )
            topic_ids = {spec.topic_id for _, spec in rows}
            enabled_topic_ids = set(
                (
                    await session.scalars(
                        select(Topic.id).where(Topic.id.in_(topic_ids), Topic.status == "ENABLED")
                    )
                ).all()
            )
            if enabled_topic_ids != topic_ids:
                raise APIError(
                    "experiment_schedule_invalid", {"csv": "CSV 包含不存在或未启用的辩题"}
                )
            imported_topics = list(
                (await session.scalars(select(Topic).where(Topic.id.in_(topic_ids)))).all()
            )
            missing_sources = [
                topic.title for topic in imported_topics if not (topic.source_text or "").strip()
            ]
            if missing_sources:
                raise APIError(
                    "experiment_schedule_invalid",
                    {"topic_sources": f"以下辩题缺少原始来源：{'、'.join(missing_sources)}"},
                )
            existing_ids = select(ScheduledMatch.id).where(ScheduledMatch.batch_id == batch_id)
            has_attempt = await session.scalar(
                select(ExperimentMatchAttempt.id)
                .where(ExperimentMatchAttempt.scheduled_match_id.in_(existing_ids))
                .limit(1)
            )
            if has_attempt is not None:
                raise APIError("experiment_batch_locked")
            await session.execute(
                delete(ScheduledSeat).where(ScheduledSeat.scheduled_match_id.in_(existing_ids))
            )
            await session.execute(delete(ScheduledMatch).where(ScheduledMatch.batch_id == batch_id))
            for kind, spec in rows:
                scheduled = ScheduledMatch(
                    batch_id=batch_id,
                    round_no=spec.round_no,
                    match_no=spec.match_no,
                    topic_id=spec.topic_id,
                    affirmative_team_id=spec.affirmative_team_id,
                    negative_team_id=spec.negative_team_id,
                    kind=kind,
                    status="DRAFT",
                    schedule_version=version,
                )
                session.add(scheduled)
                await session.flush()
                session.add_all(
                    [
                        ScheduledSeat(
                            scheduled_match_id=scheduled.id,
                            side=seat.side,
                            seat_no=seat.seat_no,
                            occupant_kind=seat.occupant_kind,
                            user_id=seat.user_id,
                            agent_profile_id=seat.agent_profile_id,
                        )
                        for seat in spec.seats
                    ]
                )
            await session.flush()
        return ExperimentScheduleCsvImportResponse(
            batch_id=batch_id,
            schedule_version=version,
            imported_match_count=len(rows),
            training_match_count=len(training_specs),
        )

    async def publish_batch(
        self, session: AsyncSession, *, batch_id: UUID
    ) -> ExperimentPublishResponse:
        async with session.begin():
            batch = await self._draft_batch(session, batch_id, lock=True)
            if batch.rule_config_snapshot is not None:
                _, batch.rule_config_snapshot = await self._freeze_rule_config(
                    session, rule_id=batch.rule_id
                )
                available_agents = await RoomService().available_agent_ids(
                    session, rule_id=batch.rule_id
                )
            else:
                format_version = (
                    await session.get(FormatVersion, batch.format_version_id)
                    if batch.format_version_id is not None
                    else None
                )
                if format_version is None or format_version.status != "PUBLISHED":
                    raise APIError("format_not_published")
                available_agents = await RoomService().available_agent_ids(
                    session, format_version_id=format_version.id
                )
            if len(available_agents) < 8:
                raise APIError("agent_capacity_insufficient")
            teams = await self._team_specs(session, batch_id)
            if batch.rule_config_snapshot is not None:
                frozen_agent_ids = set(self._snapshot_agent_ids(batch))
                if any(team.agent_profile_id not in frozen_agent_ids for team in teams):
                    raise APIError(
                        "experiment_roster_invalid",
                        {"agents": "固定 Agent 已不属于当前赛制规则，请重新保存人员配置"},
                    )
            schedule = await self._schedule_response(session, batch)
            formal_matches = tuple(match for match in schedule.matches if match.kind == "FORMAL")
            training_matches = tuple(
                match for match in schedule.matches if match.kind == "TRAINING"
            )
            if len(formal_matches) != 18 or len(training_matches) != 3:
                raise APIError("experiment_schedule_incomplete")
            topic_ids = tuple(
                next(match.topic_id for match in formal_matches if match.round_no == round_no)
                for round_no in range(1, 7)
            )
            formal_specs = tuple(
                MatchSpec(
                    round_no=match.round_no,
                    match_no=match.match_no,
                    topic_id=match.topic_id,
                    affirmative_team_id=match.affirmative_team_id,
                    negative_team_id=match.negative_team_id,
                    seats=tuple(
                        SeatSpec(
                            side=seat.side,
                            seat_no=seat.seat_no,
                            occupant_kind=seat.occupant_kind,
                            user_id=seat.user_id,
                            agent_profile_id=seat.agent_profile_id,
                        )
                        for seat in match.seats
                    ),
                )
                for match in formal_matches
            )
            training_topic_ids = {match.topic_id for match in training_matches}
            if len(training_topic_ids) != 1 or next(iter(training_topic_ids)) in set(topic_ids):
                raise APIError(
                    "experiment_schedule_invalid",
                    {"training_topic_id": "训练题必须唯一且不同于正式题"},
                )
            all_topic_ids = set(topic_ids) | training_topic_ids
            topics = list(
                (await session.scalars(select(Topic).where(Topic.id.in_(all_topic_ids)))).all()
            )
            missing_sources = [
                topic.title for topic in topics if not (topic.source_text or "").strip()
            ]
            if len(topics) != len(all_topic_ids) or missing_sources:
                raise APIError(
                    "experiment_schedule_invalid",
                    {
                        "topic_sources": (
                            f"以下辩题缺少原始来源："
                            f"{'、'.join(missing_sources) or '存在未找到的辩题'}"
                        )
                    },
                )
            training_specs = tuple(
                MatchSpec(
                    round_no=match.round_no,
                    match_no=match.match_no,
                    topic_id=match.topic_id,
                    affirmative_team_id=match.affirmative_team_id,
                    negative_team_id=match.negative_team_id,
                    seats=tuple(
                        SeatSpec(
                            side=seat.side,
                            seat_no=seat.seat_no,
                            occupant_kind=seat.occupant_kind,
                            user_id=seat.user_id,
                            agent_profile_id=seat.agent_profile_id,
                        )
                        for seat in match.seats
                    ),
                )
                for match in training_matches
            )
            issues = (
                *validate_formal_schedule(formal_specs, teams=teams, topic_ids=topic_ids),
                *validate_training_schedule(
                    training_specs,
                    teams=teams,
                    training_topic_id=next(iter(training_topic_ids)),
                ),
            )
            if issues:
                raise APIError(
                    "experiment_schedule_invalid",
                    {"schedule": "; ".join(dict.fromkeys(issue.message for issue in issues))},
                )
            version = schedule.matches[0].schedule_version
            batch.status = "PUBLISHED"
            batch.schedule_version = version
            batch.published_at = datetime.now(UTC)
            for match in await session.scalars(
                select(ScheduledMatch).where(ScheduledMatch.batch_id == batch_id)
            ):
                match.status = "SCHEDULED"
            await session.flush()
        return ExperimentPublishResponse(
            batch_id=batch_id,
            status="PUBLISHED",
            schedule_version=version,
            formal_match_count=18,
        )

    async def disable_batch(
        self, session: AsyncSession, *, batch_id: UUID
    ) -> ExperimentBatchDisableResponse:
        async with session.begin():
            batch = await session.scalar(
                select(ExperimentBatch).where(ExperimentBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                raise APIError("experiment_batch_not_found")
            if batch.status == "DISABLED":
                assert batch.disabled_at is not None
                return ExperimentBatchDisableResponse(
                    id=batch.id, status="DISABLED", disabled_at=batch.disabled_at
                )
            scheduled_ids = select(ScheduledMatch.id).where(ScheduledMatch.batch_id == batch_id)
            active_attempt = await session.scalar(
                select(ExperimentMatchAttempt.id)
                .where(
                    ExperimentMatchAttempt.scheduled_match_id.in_(scheduled_ids),
                    ExperimentMatchAttempt.status.in_(("CREATED", "WAITING", "RUNNING", "PAUSED")),
                )
                .limit(1)
            )
            if active_attempt is not None:
                raise APIError("experiment_batch_active")
            batch.status = "DISABLED"
            disabled_at = datetime.now(UTC)
            batch.disabled_at = disabled_at
            await session.flush()
            return ExperimentBatchDisableResponse(
                id=batch.id, status="DISABLED", disabled_at=disabled_at
            )

    async def list_appointments_page(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int = EXPERIMENT_PAGE_DEFAULT,
        cursor: str | None = None,
    ) -> tuple[list[ExperimentAppointmentResponse], str | None]:
        page_limit = self._page_limit(limit)
        filters = [
            ScheduledSeat.user_id == user_id,
            ScheduledSeat.occupant_kind == "HUMAN",
            ExperimentBatch.status == "PUBLISHED",
        ]
        if cursor:
            cursor_at, cursor_round, cursor_match, cursor_id = self._decode_appointment_cursor(
                cursor
            )
            after_slot = or_(
                ScheduledMatch.round_no > cursor_round,
                and_(
                    ScheduledMatch.round_no == cursor_round,
                    ScheduledMatch.match_no > cursor_match,
                ),
                and_(
                    ScheduledMatch.round_no == cursor_round,
                    ScheduledMatch.match_no == cursor_match,
                    ScheduledMatch.id > cursor_id,
                ),
            )
            if cursor_at is None:
                filters.append(ScheduledMatch.scheduled_at.is_(None) & after_slot)
            else:
                filters.append(
                    or_(
                        ScheduledMatch.scheduled_at.is_(None),
                        ScheduledMatch.scheduled_at > cursor_at,
                        and_(ScheduledMatch.scheduled_at == cursor_at, after_slot),
                    )
                )
        rows = list(
            (
                await session.execute(
                    select(ScheduledMatch, ScheduledSeat, ExperimentBatch)
                    .options(
                        load_only(
                            ScheduledMatch.id,
                            ScheduledMatch.round_no,
                            ScheduledMatch.match_no,
                            ScheduledMatch.topic_id,
                            ScheduledMatch.scheduled_at,
                            ScheduledMatch.kind,
                            ScheduledMatch.status,
                        ),
                        load_only(ScheduledSeat.side, ScheduledSeat.seat_no),
                        load_only(ExperimentBatch.id, ExperimentBatch.title),
                    )
                    .join(ScheduledSeat, ScheduledSeat.scheduled_match_id == ScheduledMatch.id)
                    .join(ExperimentBatch, ExperimentBatch.id == ScheduledMatch.batch_id)
                    .where(*filters)
                    .order_by(
                        ScheduledMatch.scheduled_at.asc().nulls_last(),
                        ScheduledMatch.round_no,
                        ScheduledMatch.match_no,
                        ScheduledMatch.id,
                    )
                    .limit(page_limit + 1)
                )
            ).all()
        )
        has_more = len(rows) > page_limit
        rows = rows[:page_limit]
        scheduled_ids = [scheduled.id for scheduled, _, _ in rows]
        latest_attempt_by_schedule: dict[UUID, ExperimentMatchAttempt] = {}
        if scheduled_ids:
            latest_attempt_numbers = (
                select(
                    ExperimentMatchAttempt.scheduled_match_id.label("scheduled_match_id"),
                    func.max(ExperimentMatchAttempt.attempt_no).label("attempt_no"),
                )
                .where(ExperimentMatchAttempt.scheduled_match_id.in_(scheduled_ids))
                .group_by(ExperimentMatchAttempt.scheduled_match_id)
                .subquery()
            )
            attempt_rows = (
                await session.scalars(
                    select(ExperimentMatchAttempt).join(
                        latest_attempt_numbers,
                        and_(
                            ExperimentMatchAttempt.scheduled_match_id
                            == latest_attempt_numbers.c.scheduled_match_id,
                            ExperimentMatchAttempt.attempt_no
                            == latest_attempt_numbers.c.attempt_no,
                        ),
                    )
                )
            ).all()
            for attempt in attempt_rows:
                latest_attempt_by_schedule.setdefault(attempt.scheduled_match_id, attempt)
        responses = [
            ExperimentAppointmentResponse(
                scheduled_match_id=scheduled.id,
                batch_id=batch.id,
                batch_title=batch.title,
                round_no=scheduled.round_no,
                match_no=scheduled.match_no,
                topic_id=scheduled.topic_id,
                scheduled_at=scheduled.scheduled_at,
                kind=cast(Literal["FORMAL", "TRAINING"], scheduled.kind),
                status=scheduled.status,
                side=cast(Literal["AFFIRMATIVE", "NEGATIVE"], seat.side),
                seat_no=seat.seat_no,
                room_id=(
                    latest_attempt_by_schedule[scheduled.id].room_id
                    if scheduled.id in latest_attempt_by_schedule
                    else None
                ),
                attempt_id=(
                    latest_attempt_by_schedule[scheduled.id].id
                    if scheduled.id in latest_attempt_by_schedule
                    else None
                ),
            )
            for scheduled, seat, batch in rows
        ]
        next_cursor = self._encode_appointment_cursor(rows[-1][0]) if has_more and rows else None
        return responses, next_cursor

    async def list_appointments(
        self, session: AsyncSession, *, user_id: UUID
    ) -> list[ExperimentAppointmentResponse]:
        # Compatibility helper for internal callers. HTTP callers use the bounded page method;
        # preserve the historical complete-array helper by advancing through bounded pages.
        responses: list[ExperimentAppointmentResponse] = []
        cursor: str | None = None
        seen: set[tuple[datetime | None, int, int, UUID]] = set()
        previous_cursor: tuple[datetime | None, int, int, UUID] | None = None
        page_count = 0
        while True:
            if page_count >= self.EXPERIMENT_HISTORY_MAX_PAGES:
                raise APIError("experiment_history_too_large")
            page, next_cursor = await self.list_appointments_page(
                session,
                user_id=user_id,
                limit=self.EXPERIMENT_PAGE_MAX,
                cursor=cursor,
            )
            page_count += 1
            decoded_cursor = None
            if next_cursor is not None:
                decoded_cursor = self._validate_appointment_history_cursor(
                    previous_cursor, next_cursor, seen
                )
            if len(responses) + len(page) > self.EXPERIMENT_HISTORY_MAX_ROWS:
                raise APIError("experiment_history_too_large")
            responses.extend(page)
            if next_cursor is None:
                return responses
            assert decoded_cursor is not None
            seen.add(decoded_cursor)
            previous_cursor = decoded_cursor
            cursor = next_cursor

    async def _allocate_room_code(self, session: AsyncSession) -> str:
        for _ in range(10):
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            if await session.scalar(select(Room.id).where(Room.code == code)) is None:
                return code
        raise APIError("room_code_collision")

    async def _accept_platform_participation_terms(
        self, session: AsyncSession, *, user_id: UUID, supplied_version: str
    ) -> None:
        current = get_current_human_participation_terms()
        if supplied_version != current.version:
            raise APIError("human_participation_terms_outdated")
        existing = await session.scalar(
            select(UserConsent.id).where(
                UserConsent.user_id == user_id,
                UserConsent.consent_type == "human_participation",
                UserConsent.version == current.version,
            )
        )
        if existing is None:
            session.add(
                UserConsent(
                    user_id=user_id,
                    consent_type="human_participation",
                    version=current.version,
                )
            )

    async def enter_scheduled_match(
        self,
        session: AsyncSession,
        *,
        scheduled_match_id: UUID,
        user_id: UUID,
        user_role: str,
        human_participation_terms_version: str,
    ) -> ExperimentEnterResponse:
        async with session.begin():
            scheduled = await session.scalar(
                select(ScheduledMatch)
                .where(ScheduledMatch.id == scheduled_match_id)
                .with_for_update()
            )
            if scheduled is None:
                raise APIError("experiment_appointment_not_found")
            batch = await session.get(ExperimentBatch, scheduled.batch_id)
            if batch is None or batch.status != "PUBLISHED":
                raise APIError("experiment_unavailable")
            fixed_seat = await session.scalar(
                select(ScheduledSeat).where(
                    ScheduledSeat.scheduled_match_id == scheduled_match_id,
                    ScheduledSeat.user_id == user_id,
                    ScheduledSeat.occupant_kind == "HUMAN",
                )
            )
            is_debater = fixed_seat is not None
            if is_debater and scheduled.kind == "FORMAL":
                pending_prior = await session.scalar(
                    select(ParticipantAnnotationTask.id)
                    .join(
                        ExperimentMatchAttempt,
                        ExperimentMatchAttempt.id
                        == ParticipantAnnotationTask.experiment_attempt_id,
                    )
                    .join(
                        ScheduledMatch,
                        ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id,
                    )
                    .where(
                        ParticipantAnnotationTask.user_id == user_id,
                        ParticipantAnnotationTask.status != "SUBMITTED",
                        ScheduledMatch.kind == "FORMAL",
                        ScheduledMatch.batch_id == scheduled.batch_id,
                        ScheduledMatch.id != scheduled.id,
                    )
                    .limit(1)
                )
                if pending_prior is not None:
                    raise APIError("experiment_annotation_required")
                # Rule-controlled postmatch surveys are a participant gate for
                # subsequent formal matches in the same batch. Only completed
                # matches with an enabled room snapshot participate.
                pending_survey = await session.scalar(
                    select(PostmatchSurveyTask.id)
                    .join(Match, Match.id == PostmatchSurveyTask.match_id)
                    .join(Room, Room.id == Match.room_id)
                    .join(
                        ExperimentMatchAttempt,
                        ExperimentMatchAttempt.room_id == Match.room_id,
                    )
                    .join(
                        ScheduledMatch,
                        ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id,
                    )
                    .where(
                        PostmatchSurveyTask.user_id == user_id,
                        PostmatchSurveyTask.status != "SUBMITTED",
                        Match.status == "FINISHED",
                        ScheduledMatch.kind == "FORMAL",
                        ScheduledMatch.batch_id == scheduled.batch_id,
                        (ScheduledMatch.round_no < scheduled.round_no)
                        | (
                            (ScheduledMatch.round_no == scheduled.round_no)
                            & (ScheduledMatch.match_no < scheduled.match_no)
                        ),
                        Room.format_snapshot["postmatch_questionnaire_enabled"]
                        .as_boolean()
                        .is_(True),
                    )
                    .limit(1)
                )
                if pending_survey is not None:
                    raise APIError("postmatch_survey_required")
            if is_debater:
                await self._accept_platform_participation_terms(
                    session,
                    user_id=user_id,
                    supplied_version=human_participation_terms_version,
                )

            attempt = await session.scalar(
                select(ExperimentMatchAttempt)
                .where(
                    ExperimentMatchAttempt.scheduled_match_id == scheduled_match_id,
                    ExperimentMatchAttempt.status.in_(("CREATED", "WAITING", "RUNNING", "PAUSED")),
                )
                .with_for_update()
            )
            if attempt is None:
                if not is_debater and user_role != "ADMIN":
                    raise APIError("experiment_room_not_created")
                if scheduled.kind == "FORMAL" and (
                    scheduled.effective_attempt_id is not None or scheduled.status == "COMPLETED"
                ):
                    raise APIError("experiment_match_completed")
                latest = await session.scalar(
                    select(ExperimentMatchAttempt)
                    .where(ExperimentMatchAttempt.scheduled_match_id == scheduled_match_id)
                    .order_by(ExperimentMatchAttempt.attempt_no.desc())
                    .limit(1)
                )
                reusable_statuses = {"TERMINATED", "INCOMPLETE"}
                if scheduled.kind == "TRAINING":
                    reusable_statuses.add("COMPLETED")
                if latest is not None and latest.status not in reusable_statuses:
                    raise APIError("experiment_attempt_active")
                if scheduled.kind == "TRAINING":
                    training_attempt_count = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(ExperimentMatchAttempt)
                            .join(
                                ScheduledMatch,
                                ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id,
                            )
                            .where(
                                ScheduledMatch.batch_id == batch.id,
                                ScheduledMatch.kind == "TRAINING",
                                ExperimentMatchAttempt.created_by_user_id == user_id,
                            )
                        )
                        or 0
                    )
                    if training_attempt_count >= batch.training_room_quota:
                        raise APIError("experiment_training_quota_exhausted")
                scheduled_seats = list(
                    (
                        await session.scalars(
                            select(ScheduledSeat)
                            .where(ScheduledSeat.scheduled_match_id == scheduled_match_id)
                            .order_by(ScheduledSeat.side, ScheduledSeat.seat_no)
                        )
                    ).all()
                )
                if len(scheduled_seats) != 8:
                    raise APIError("experiment_schedule_invalid")
                human_user_ids = (
                    [user_id]
                    if scheduled.kind == "TRAINING"
                    else [
                        seat.user_id
                        for seat in scheduled_seats
                        if seat.occupant_kind == "HUMAN" and seat.user_id is not None
                    ]
                )
                conflicting = await session.scalar(
                    select(RoomMember.id)
                    .join(Room, Room.id == RoomMember.room_id)
                    .where(
                        RoomMember.user_id.in_(human_user_ids),
                        RoomMember.left_at.is_(None),
                        Room.status.not_in(("FINISHED", "TERMINATED")),
                    )
                    .limit(1)
                )
                if conflicting is not None:
                    raise APIError("user_active_room_conflict")
                organizer_id = (
                    user_id
                    if scheduled.kind == "TRAINING"
                    else next(
                        seat.user_id
                        for seat in scheduled_seats
                        if seat.side == "AFFIRMATIVE"
                        and seat.occupant_kind == "HUMAN"
                        and seat.user_id is not None
                    )
                )
                assert organizer_id is not None
                topic = await session.get(Topic, scheduled.topic_id)
                if topic is None or topic.status != "ENABLED":
                    raise APIError("topic_unavailable")
                current_snapshots = self._current_batch_snapshots(batch)
                if current_snapshots is not None:
                    rule_snapshot, format_snapshot = current_snapshots
                    candidate_ids = self._snapshot_agent_ids(batch)
                else:
                    _, rule_snapshot = await RoomService().load_rule_snapshot(
                        session, rule_id=batch.rule_id
                    )
                    format_version = (
                        await session.get(FormatVersion, batch.format_version_id)
                        if batch.format_version_id is not None
                        else None
                    )
                    if format_version is None or format_version.status != "PUBLISHED":
                        raise APIError("format_not_published")
                    if format_version.published_snapshot is None:
                        raise APIError("format_not_published")
                    format_snapshot = format_version.published_snapshot
                    candidate_ids = await RoomService().available_agent_ids(
                        session, format_version_id=format_version.id
                    )
                room_seats = scheduled_seats
                if scheduled.kind == "TRAINING":
                    if len(candidate_ids) < 8:
                        raise APIError("agent_capacity_insufficient")
                    selected_agents = secrets.SystemRandom().sample(candidate_ids, 8)
                    room_seats = [
                        ScheduledSeat(
                            side=side,
                            seat_no=seat_no,
                            occupant_kind="AGENT",
                            agent_profile_id=selected_agents[index],
                        )
                        for index, (side, seat_no) in enumerate(
                            (side, seat_no)
                            for side in ("AFFIRMATIVE", "NEGATIVE")
                            for seat_no in range(1, 5)
                        )
                    ]
                room = Room(
                    code=await self._allocate_room_code(session),
                    title=f"{batch.title} 第{scheduled.round_no}轮第{scheduled.match_no}场",
                    label="论文实验",
                    topic_id=topic.id,
                    topic_snapshot={
                        "title": topic.title,
                        "affirmative_text": topic.affirmative_text,
                        "negative_text": topic.negative_text,
                        "source_text": topic.source_text,
                        "cedar_id": topic.cedar_id,
                        "topic_key": topic.topic_key,
                        "version": str(topic.version),
                    },
                    rule_id=batch.rule_id,
                    rule_snapshot=rule_snapshot,
                    organizer_user_id=organizer_id,
                    is_all_agent=False,
                    format_version_id=(
                        batch.format_version_id if current_snapshots is None else None
                    ),
                    format_snapshot=deepcopy(format_snapshot),
                    auto_fill_agents=scheduled.kind == "TRAINING",
                )
                session.add(room)
                await session.flush()
                session.add_all(
                    [
                        RoomMember(
                            room_id=room.id,
                            user_id=participant_id,
                            member_role="DEBATER",
                            online=participant_id == user_id,
                            ready=False,
                        )
                        for participant_id in human_user_ids
                    ]
                )
                if not is_debater:
                    session.add(
                        RoomMember(
                            room_id=room.id,
                            user_id=user_id,
                            member_role="SPECTATOR",
                            online=True,
                            ready=False,
                        )
                    )
                for seat in room_seats:
                    session.add(
                        Seat(
                            room_id=room.id,
                            side=seat.side,
                            seat_no=seat.seat_no,
                            occupant_type=seat.occupant_kind,
                            user_id=seat.user_id,
                            agent_profile_id=seat.agent_profile_id,
                            configured_agent_profile_id=seat.agent_profile_id,
                        )
                    )
                attempt = ExperimentMatchAttempt(
                    scheduled_match_id=scheduled_match_id,
                    attempt_no=(latest.attempt_no + 1) if latest is not None else 1,
                    room_id=room.id,
                    status="WAITING",
                    created_by_user_id=user_id,
                )
                session.add(attempt)
                await session.flush()
            else:
                if attempt.room_id is None:
                    raise APIError("experiment_attempt_invalid")
                room = await session.get(Room, attempt.room_id, with_for_update=True)
                if room is None:
                    raise APIError("experiment_attempt_invalid")
                member = await session.scalar(
                    select(RoomMember).where(
                        RoomMember.room_id == room.id,
                        RoomMember.user_id == user_id,
                    )
                )
                role = "DEBATER" if is_debater else "SPECTATOR"
                if member is None:
                    if role == "SPECTATOR":
                        await RoomService().assert_spectator_capacity(session)
                    member = RoomMember(
                        room_id=room.id,
                        user_id=user_id,
                        member_role=role,
                        online=True,
                        ready=False,
                    )
                    session.add(member)
                else:
                    member.member_role = role
                    member.left_at = None
                    member.online = True
                room.sequence += 1
                await session.flush()
            assert attempt.room_id is not None
            return ExperimentEnterResponse(
                scheduled_match_id=scheduled_match_id,
                attempt_id=attempt.id,
                attempt_no=attempt.attempt_no,
                room_id=attempt.room_id,
                room_code=room.code,
                member_role="DEBATER" if is_debater else "SPECTATOR",
            )

    async def _participant_task(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        user_id: UUID,
        lock: bool = False,
    ) -> ParticipantAnnotationTask:
        query = select(ParticipantAnnotationTask).where(
            ParticipantAnnotationTask.id == task_id,
            ParticipantAnnotationTask.user_id == user_id,
        )
        if lock:
            query = query.with_for_update()
        task = await session.scalar(query)
        if task is None:
            raise APIError("experiment_annotation_not_found")
        return task

    @staticmethod
    def _annotation_json_bytes(value: object) -> int:
        """Return the compact UTF-8 size used for response budget checks."""

        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        )

    @classmethod
    def _raise_annotation_payload_too_large(cls) -> None:
        raise APIError("experiment_annotation_payload_too_large")

    @classmethod
    def _assert_annotation_response_budget(
        cls, responses: Sequence[ParticipantAnnotationTaskResponse | ExpertAnnotationTaskResponse]
    ) -> None:
        total = 0
        for response in responses:
            payload_bytes = cls._annotation_json_bytes(response.model_dump(mode="json"))
            if payload_bytes > cls.ANNOTATION_MAX_TASK_PAYLOAD_BYTES:
                cls._raise_annotation_payload_too_large()
            total += payload_bytes
            if total > cls.ANNOTATION_MAX_PAGE_PAYLOAD_BYTES:
                cls._raise_annotation_payload_too_large()

    @classmethod
    async def _assert_participant_child_budget(
        cls, session: AsyncSession, task_ids: list[UUID]
    ) -> None:
        """Check child row counts and JSON/text sizes before selecting payload columns.

        The follow-up ORM queries intentionally happen only after these scalar
        aggregates. This prevents a malformed task containing a huge JSONB,
        transcript, or questionnaire free-text answer from being materialized
        merely to discover that it is too large.
        """

        item_stats = (
            await session.execute(
                select(
                    ParticipantAnnotationItem.task_id,
                    func.count(ParticipantAnnotationItem.id),
                )
                .where(ParticipantAnnotationItem.task_id.in_(task_ids))
                .group_by(ParticipantAnnotationItem.task_id)
            )
        ).all()
        if any(int(count) > cls.ANNOTATION_MAX_ITEMS_PER_TASK for _task_id, count in item_stats):
            cls._raise_annotation_payload_too_large()

        answer_stats = (
            await session.execute(
                select(
                    ParticipantAnnotationAnswer.task_id,
                    func.count(ParticipantAnnotationAnswer.id),
                    func.coalesce(
                        func.sum(
                            func.octet_length(sa_cast(ParticipantAnnotationAnswer.answer, Text))
                        ),
                        0,
                    ),
                )
                .where(ParticipantAnnotationAnswer.task_id.in_(task_ids))
                .group_by(ParticipantAnnotationAnswer.task_id)
            )
        ).all()
        answer_bytes_by_task: dict[UUID, int] = {}
        for task_id, count, payload_bytes in answer_stats:
            if int(count) > cls.ANNOTATION_MAX_ANSWERS_PER_TASK:
                cls._raise_annotation_payload_too_large()
            answer_bytes_by_task[task_id] = int(payload_bytes or 0)

        opportunity_stats = (
            await session.execute(
                select(
                    ParticipantAnnotationItem.task_id,
                    func.coalesce(
                        func.sum(
                            func.octet_length(sa_cast(FreeDebateOpportunity.frozen_context, Text))
                        ),
                        0,
                    ),
                )
                .join(
                    FreeDebateOpportunity,
                    FreeDebateOpportunity.id == ParticipantAnnotationItem.opportunity_id,
                )
                .where(ParticipantAnnotationItem.task_id.in_(task_ids))
                .group_by(ParticipantAnnotationItem.task_id)
            )
        ).all()
        context_bytes_by_task = {
            task_id: int(payload_bytes or 0) for task_id, payload_bytes in opportunity_stats
        }

        speech_stats = (
            await session.execute(
                select(
                    ParticipantAnnotationItem.task_id,
                    func.coalesce(
                        func.sum(func.octet_length(func.coalesce(Speech.display_text, ""))), 0
                    ),
                )
                .join(Speech, Speech.id == ParticipantAnnotationItem.speech_id)
                .where(ParticipantAnnotationItem.task_id.in_(task_ids))
                .group_by(ParticipantAnnotationItem.task_id)
            )
        ).all()
        speech_bytes_by_task = {
            task_id: int(payload_bytes or 0) for task_id, payload_bytes in speech_stats
        }

        questionnaire_stats = (
            await session.execute(
                select(
                    MatchQuestionnaire.task_id,
                    func.coalesce(
                        func.sum(func.octet_length(func.coalesce(MatchQuestionnaire.q6, ""))),
                        0,
                    ),
                )
                .where(MatchQuestionnaire.task_id.in_(task_ids))
                .group_by(MatchQuestionnaire.task_id)
            )
        ).all()
        questionnaire_bytes_by_task = {
            task_id: int(payload_bytes or 0) for task_id, payload_bytes in questionnaire_stats
        }

        total_bytes = 0
        for task_id in task_ids:
            task_bytes = (
                answer_bytes_by_task.get(task_id, 0)
                + context_bytes_by_task.get(task_id, 0)
                + speech_bytes_by_task.get(task_id, 0)
                + questionnaire_bytes_by_task.get(task_id, 0)
            )
            if task_bytes > cls.ANNOTATION_MAX_TASK_PAYLOAD_BYTES:
                cls._raise_annotation_payload_too_large()
            total_bytes += task_bytes
        if total_bytes > cls.ANNOTATION_MAX_PAGE_PAYLOAD_BYTES:
            cls._raise_annotation_payload_too_large()

    @classmethod
    async def _assert_expert_child_budget(cls, session: AsyncSession, task_ids: list[UUID]) -> None:
        stats = (
            await session.execute(
                select(
                    ExpertAnnotationAnswer.task_id,
                    func.count(ExpertAnnotationAnswer.id),
                    func.coalesce(
                        func.sum(
                            func.octet_length(sa_cast(ExpertAnnotationAnswer.frozen_payload, Text))
                        ),
                        0,
                    ),
                )
                .where(ExpertAnnotationAnswer.task_id.in_(task_ids))
                .group_by(ExpertAnnotationAnswer.task_id)
            )
        ).all()
        total_bytes = 0
        for _task_id, count, payload_bytes in stats:
            if int(count) > cls.ANNOTATION_MAX_ITEMS_PER_TASK:
                cls._raise_annotation_payload_too_large()
            task_bytes = int(payload_bytes or 0)
            if task_bytes > cls.ANNOTATION_MAX_TASK_PAYLOAD_BYTES:
                cls._raise_annotation_payload_too_large()
            total_bytes += task_bytes
        if total_bytes > cls.ANNOTATION_MAX_PAGE_PAYLOAD_BYTES:
            cls._raise_annotation_payload_too_large()

    async def _participant_task_responses(
        self, session: AsyncSession, tasks: list[ParticipantAnnotationTask]
    ) -> list[ParticipantAnnotationTaskResponse]:
        """Materialize a bounded task page with set-based related queries.

        A task contains several JSON payloads, so loading a whole user's history
        in one request is intentionally avoided. The page itself is loaded once,
        then all items, opportunities, speeches, answers, questionnaires and
        attempt metadata are fetched in batches to avoid one query per task.
        """
        if not tasks:
            return []
        task_ids = [task.id for task in tasks]
        await self._assert_participant_child_budget(session, task_ids)
        attempt_ids = [task.experiment_attempt_id for task in tasks]
        attempt_rows = list(
            (
                await session.execute(
                    select(ExperimentMatchAttempt, ScheduledMatch)
                    .join(
                        ScheduledMatch,
                        ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id,
                    )
                    .where(ExperimentMatchAttempt.id.in_(attempt_ids))
                )
            ).all()
        )
        attempts = {attempt.id: (attempt, scheduled) for attempt, scheduled in attempt_rows}
        if len(attempts) != len(set(attempt_ids)):
            raise APIError("experiment_attempt_invalid")

        item_rows = list(
            (
                await session.scalars(
                    select(ParticipantAnnotationItem)
                    .where(ParticipantAnnotationItem.task_id.in_(task_ids))
                    .order_by(ParticipantAnnotationItem.task_id, ParticipantAnnotationItem.position)
                )
            ).all()
        )
        items_by_task: dict[UUID, list[ParticipantAnnotationItem]] = {}
        for item in item_rows:
            items_by_task.setdefault(item.task_id, []).append(item)
        opportunity_ids = {item.opportunity_id for item in item_rows}
        speech_ids = {item.speech_id for item in item_rows}
        opportunity_rows = (
            list(
                (
                    await session.scalars(
                        select(FreeDebateOpportunity).where(
                            FreeDebateOpportunity.id.in_(opportunity_ids)
                        )
                    )
                ).all()
            )
            if opportunity_ids
            else []
        )
        speech_rows = (
            list((await session.scalars(select(Speech).where(Speech.id.in_(speech_ids)))).all())
            if speech_ids
            else []
        )
        opportunities = {row.id: row for row in opportunity_rows}
        speeches = {row.id: row for row in speech_rows}

        answer_rows = list(
            (
                await session.scalars(
                    select(ParticipantAnnotationAnswer).where(
                        ParticipantAnnotationAnswer.task_id.in_(task_ids)
                    )
                )
            ).all()
        )
        answers_by_task_item: dict[tuple[UUID, UUID], dict[str, object]] = {}
        versions_by_task_item: dict[tuple[UUID, UUID], dict[str, int]] = {}
        for answer in answer_rows:
            key = f"stage{answer.stage}.{answer.question_key}"
            lookup = (answer.task_id, answer.opportunity_id)
            answers_by_task_item.setdefault(lookup, {})[key] = answer.answer
            versions_by_task_item.setdefault(lookup, {})[key] = answer.client_version
        questionnaire_rows = list(
            (
                await session.scalars(
                    select(MatchQuestionnaire).where(MatchQuestionnaire.task_id.in_(task_ids))
                )
            ).all()
        )
        questionnaires = {row.task_id: row for row in questionnaire_rows}

        responses: list[ParticipantAnnotationTaskResponse] = []
        for task in tasks:
            _attempt, scheduled = attempts[task.experiment_attempt_id]
            response_items: list[ParticipantAnnotationItemResponse] = []
            for item in items_by_task.get(task.id, []):
                opportunity = opportunities.get(item.opportunity_id)
                speech = speeches.get(item.speech_id)
                if opportunity is None or speech is None:
                    raise APIError("experiment_annotation_invalid")
                revealed = item.subject_kind == "HUMAN_SELF" or item.stage1_locked_at is not None
                lookup = (task.id, item.opportunity_id)
                response_items.append(
                    ParticipantAnnotationItemResponse(
                        id=item.id,
                        opportunity_id=item.opportunity_id,
                        subject_kind=cast(Literal["HUMAN_SELF", "TEAM_AI"], item.subject_kind),
                        speech_id=item.speech_id,
                        position=item.position,
                        stage1_locked=item.stage1_locked_at is not None,
                        revealed=revealed,
                        frozen_context=dict(opportunity.frozen_context),
                        speech_text=speech.display_text if revealed else None,
                        answers=answers_by_task_item.get(lookup, {}),
                        answer_versions=versions_by_task_item.get(lookup, {}),
                    )
                )
            questionnaire = questionnaires.get(task.id)
            responses.append(
                ParticipantAnnotationTaskResponse(
                    id=task.id,
                    experiment_attempt_id=task.experiment_attempt_id,
                    questionnaire_version=task.questionnaire_version,
                    status=cast(Literal["PENDING", "IN_PROGRESS", "SUBMITTED"], task.status),
                    due_at=task.due_at,
                    late=task.late,
                    submitted_at=task.submitted_at,
                    scheduled_match_kind=cast(Literal["FORMAL", "TRAINING"], scheduled.kind),
                    items=response_items,
                    questionnaire=(
                        ParticipantQuestionnaireResponse.model_validate(questionnaire)
                        if questionnaire is not None
                        else None
                    ),
                )
            )
        self._assert_annotation_response_budget(responses)
        return responses

    async def _participant_task_response(
        self, session: AsyncSession, task: ParticipantAnnotationTask
    ) -> ParticipantAnnotationTaskResponse:
        return (await self._participant_task_responses(session, [task]))[0]

    async def list_participant_tasks(
        self, session: AsyncSession, *, user_id: UUID
    ) -> list[ParticipantAnnotationTaskResponse]:
        # Compatibility helper for internal callers that historically expected
        # the complete array. The HTTP route uses the bounded page method below;
        # each compatibility page is still bounded and batch-loaded.
        result: list[ParticipantAnnotationTaskResponse] = []
        cursor: tuple[datetime, UUID] | None = None
        previous_cursor: tuple[datetime, UUID] | None = None
        seen: set[tuple[datetime, UUID]] = set()
        page_count = 0
        while True:
            if page_count >= self.EXPERIMENT_HISTORY_MAX_PAGES:
                raise APIError("experiment_history_too_large")
            page, next_cursor = await self.list_participant_tasks_page(
                session,
                user_id=user_id,
                limit=self.EXPERIMENT_PAGE_MAX,
                cursor=cursor,
            )
            page_count += 1
            if len(result) + len(page) > self.EXPERIMENT_HISTORY_MAX_ROWS:
                raise APIError("experiment_history_too_large")
            result.extend(page)
            if next_cursor is None:
                return result
            self._validate_annotation_history_cursor(previous_cursor, next_cursor, seen)
            seen.add(next_cursor)
            previous_cursor = next_cursor
            cursor = next_cursor

    async def list_participant_tasks_page(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int = ANNOTATION_PAGE_DEFAULT,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> tuple[list[ParticipantAnnotationTaskResponse], tuple[datetime, UUID] | None]:
        bounded_limit = min(max(limit, 1), self.ANNOTATION_PAGE_MAX)
        query = select(ParticipantAnnotationTask).where(
            ParticipantAnnotationTask.user_id == user_id
        )
        if cursor is not None:
            query = query.where(
                or_(
                    ParticipantAnnotationTask.created_at < cursor[0],
                    and_(
                        ParticipantAnnotationTask.created_at == cursor[0],
                        ParticipantAnnotationTask.id < cursor[1],
                    ),
                )
            )
        query = query.order_by(
            ParticipantAnnotationTask.created_at.desc(), ParticipantAnnotationTask.id.desc()
        ).limit(bounded_limit + 1)
        rows = list((await session.scalars(query)).all())
        has_more = len(rows) > bounded_limit
        page_rows = rows[:bounded_limit]
        next_cursor = (
            (page_rows[-1].created_at, page_rows[-1].id) if has_more and page_rows else None
        )
        return await self._participant_task_responses(session, page_rows), next_cursor

    async def participant_audio(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        speech_id: UUID,
        user_id: UUID,
    ) -> tuple[str, str]:
        task = await self._participant_task(session, task_id=task_id, user_id=user_id)
        item = await session.scalar(
            select(ParticipantAnnotationItem).where(
                ParticipantAnnotationItem.task_id == task.id,
                ParticipantAnnotationItem.speech_id == speech_id,
            )
        )
        if item is None:
            raise APIError("experiment_annotation_not_found")
        if item.subject_kind == "TEAM_AI" and item.stage1_locked_at is None:
            raise APIError("experiment_annotation_required")
        speech = await session.get(Speech, speech_id)
        if speech is None or not speech.audio_storage_path:
            raise APIError("experiment_annotation_audio_unavailable")
        return speech.audio_storage_path, speech.speaker_kind

    def _validate_participant_answers(
        self,
        *,
        subject_kind: str,
        payload: ParticipantAnnotationAnswerSaveRequest,
        strict: bool = False,
    ) -> None:
        if subject_kind == "HUMAN_SELF":
            answer_keys = set(payload.answers)
            expected_keys = {"primary_reason", "goals"} if strict else None
            if payload.stage != 1 or (
                answer_keys != expected_keys
                if expected_keys is not None
                else answer_keys not in ({"primary_reason", "goals"}, {"primary_reason", "reasons"})
            ):
                raise APIError("experiment_annotation_answer_invalid")
            primary = payload.answers["primary_reason"]
            goals = payload.answers.get("goals", payload.answers.get("reasons"))
            if not isinstance(primary, str) or not isinstance(goals, list):
                raise APIError("experiment_annotation_answer_invalid")
            goal_values = cast(list[object], goals)
            if (
                primary
                not in (
                    self._HUMAN_PRIMARY_REASONS
                    if strict
                    else self._HUMAN_PRIMARY_REASONS | self._HUMAN_GOALS
                )
                or not goals
                or any(
                    not isinstance(value, str) or value not in self._HUMAN_GOALS
                    for value in goal_values
                )
            ):
                raise APIError("experiment_annotation_answer_invalid")
            return
        if payload.stage == 1:
            expected = {"appropriateness"}
        elif strict or "team_need_match" in payload.answers:
            expected = {"team_need_match"}
        else:
            expected = {"match_categories"}
        if set(payload.answers) != expected:
            raise APIError("experiment_annotation_answer_invalid")
        if payload.stage == 1:
            allowed = (
                self._AI_APPROPRIATENESS
                if strict
                else {
                    "合适",
                    "不合适",
                    "无法判断",
                    *self._AI_APPROPRIATENESS,
                }
            )
            if payload.answers["appropriateness"] not in allowed:
                raise APIError("experiment_annotation_answer_invalid")
            return
        value = payload.answers.get("team_need_match")
        if value is not None:
            if not isinstance(value, str) or value not in self._AI_MATCH:
                raise APIError("experiment_annotation_answer_invalid")
            return
        values = payload.answers.get("match_categories")
        if not isinstance(values, list) or not values:
            raise APIError("experiment_annotation_answer_invalid")
        legacy_values = cast(list[object], values)
        if any(not isinstance(item, str) for item in legacy_values):
            raise APIError("experiment_annotation_answer_invalid")

    async def save_participant_answers(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        item_id: UUID,
        user_id: UUID,
        payload: ParticipantAnnotationAnswerSaveRequest,
    ) -> ParticipantAnnotationTaskResponse:
        async with session.begin():
            task = await self._participant_task(
                session, task_id=task_id, user_id=user_id, lock=True
            )
            if task.status == "SUBMITTED":
                raise APIError("experiment_annotation_locked")
            item = await session.scalar(
                select(ParticipantAnnotationItem)
                .where(
                    ParticipantAnnotationItem.id == item_id,
                    ParticipantAnnotationItem.task_id == task.id,
                )
                .with_for_update()
            )
            if item is None:
                raise APIError("experiment_annotation_not_found")
            if (
                item.subject_kind == "TEAM_AI"
                and payload.stage == 2
                and item.stage1_locked_at is None
            ):
                raise APIError("experiment_annotation_stage_locked")
            if item.stage1_locked_at is not None and payload.stage == 1:
                raise APIError("experiment_annotation_stage_locked")
            self._validate_participant_answers(
                subject_kind=item.subject_kind,
                payload=payload,
                strict=task.questionnaire_version == QUESTIONNAIRE_VERSION,
            )
            for question_key, answer_value in payload.answers.items():
                answer = await session.scalar(
                    select(ParticipantAnnotationAnswer)
                    .where(
                        ParticipantAnnotationAnswer.task_id == task.id,
                        ParticipantAnnotationAnswer.opportunity_id == item.opportunity_id,
                        ParticipantAnnotationAnswer.subject_kind == item.subject_kind,
                        ParticipantAnnotationAnswer.stage == payload.stage,
                        ParticipantAnnotationAnswer.question_key == question_key,
                    )
                    .with_for_update()
                )
                if answer is not None and payload.client_version <= answer.client_version:
                    raise APIError("experiment_annotation_version_conflict")
                if answer is None:
                    answer = ParticipantAnnotationAnswer(
                        task_id=task.id,
                        opportunity_id=item.opportunity_id,
                        subject_kind=item.subject_kind,
                        stage=payload.stage,
                        question_key=question_key,
                        answer=answer_value,
                    )
                    session.add(answer)
                else:
                    answer.answer = answer_value
                answer.client_version = payload.client_version
                answer.response_duration_ms = payload.response_duration_ms
                answer.audio_play_count = payload.audio_play_count
            if payload.lock_stage:
                if payload.stage != 1:
                    raise APIError("experiment_annotation_answer_invalid")
                item.stage1_locked_at = datetime.now(UTC)
                if item.subject_kind == "TEAM_AI":
                    item.revealed_at = item.stage1_locked_at
            task.status = "IN_PROGRESS"
            await session.flush()
            response = await self._participant_task_response(session, task)
        return response

    async def submit_participant_questionnaire(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        user_id: UUID,
        payload: ParticipantQuestionnaireSubmitRequest,
    ) -> ParticipantAnnotationTaskResponse:
        async with session.begin():
            task = await self._participant_task(
                session, task_id=task_id, user_id=user_id, lock=True
            )
            if task.status == "SUBMITTED":
                raise APIError("experiment_annotation_locked")
            questionnaire = await session.scalar(
                select(MatchQuestionnaire)
                .where(MatchQuestionnaire.task_id == task.id)
                .with_for_update()
            )
            values = payload.model_dump()
            values["q6"] = payload.q6.strip() if payload.q6 and payload.q6.strip() else None
            if task.questionnaire_version == QUESTIONNAIRE_VERSION and values["q6"] is not None:
                raise APIError("experiment_annotation_answer_invalid")
            if questionnaire is None:
                questionnaire = MatchQuestionnaire(task_id=task.id, **values)
                session.add(questionnaire)
            else:
                for key, value in values.items():
                    setattr(questionnaire, key, value)
                questionnaire.submitted_at = datetime.now(UTC)
            task.status = "IN_PROGRESS"
            await session.flush()
            response = await self._participant_task_response(session, task)
        return response

    async def submit_participant_task(
        self, session: AsyncSession, *, task_id: UUID, user_id: UUID
    ) -> ParticipantAnnotationTaskResponse:
        async with session.begin():
            task = await self._participant_task(
                session, task_id=task_id, user_id=user_id, lock=True
            )
            if task.status == "SUBMITTED":
                return await self._participant_task_response(session, task)
            response = await self._participant_task_response(session, task)
            for item in response.items:
                required = (
                    {"stage1.primary_reason", "stage1.goals"}
                    if item.subject_kind == "HUMAN_SELF"
                    else {"stage1.appropriateness", "stage2.team_need_match"}
                )
                legacy_required = (
                    {"stage1.primary_reason", "stage1.reasons"}
                    if item.subject_kind == "HUMAN_SELF"
                    else {"stage1.appropriateness", "stage2.match_categories"}
                )
                complete = (
                    required.issubset(item.answers)
                    if task.questionnaire_version == QUESTIONNAIRE_VERSION
                    else (required.issubset(item.answers) or legacy_required.issubset(item.answers))
                )
                if not complete or (item.subject_kind == "TEAM_AI" and not item.stage1_locked):
                    raise APIError("experiment_annotation_incomplete")
            if response.questionnaire is None:
                raise APIError("experiment_questionnaire_incomplete")
            submitted_at = datetime.now(UTC)
            task.status = "SUBMITTED"
            task.submitted_at = submitted_at
            task.late = submitted_at > task.due_at
            completed_count = await session.scalar(
                select(func.count())
                .select_from(ParticipantAnnotationTask)
                .where(
                    ParticipantAnnotationTask.experiment_attempt_id == task.experiment_attempt_id,
                    ParticipantAnnotationTask.status == "SUBMITTED",
                )
            )
            if int(completed_count or 0) >= 6:
                attempt = await session.get(
                    ExperimentMatchAttempt, task.experiment_attempt_id, with_for_update=True
                )
                if attempt is None:
                    raise APIError("experiment_attempt_invalid")
                if attempt.public_at is None:
                    attempt.public_at = submitted_at
                    scheduled = await session.get(ScheduledMatch, attempt.scheduled_match_id)
                    if scheduled is not None and scheduled.kind == "FORMAL":
                        session.add(
                            BackgroundTask(
                                task_type="LEADERBOARD_DAILY",
                                payload={
                                    "reason": "experiment_results_public",
                                    "attempt_id": str(attempt.id),
                                },
                                max_attempts=2,
                            )
                        )
            await session.flush()
            response = await self._participant_task_response(session, task)
        return response

    async def _expert_task_response(
        self, session: AsyncSession, task: ExpertAnnotationTask
    ) -> ExpertAnnotationTaskResponse:
        return (await self._expert_task_responses(session, [task]))[0]

    async def _expert_task_responses(
        self, session: AsyncSession, tasks: list[ExpertAnnotationTask]
    ) -> list[ExpertAnnotationTaskResponse]:
        if not tasks:
            return []
        task_ids = [task.id for task in tasks]
        await self._assert_expert_child_budget(session, task_ids)
        answer_rows = list(
            (
                await session.execute(
                    select(ExpertAnnotationAnswer, FreeDebateOpportunity)
                    .join(
                        FreeDebateOpportunity,
                        FreeDebateOpportunity.id == ExpertAnnotationAnswer.opportunity_id,
                    )
                    .where(ExpertAnnotationAnswer.task_id.in_(task_ids))
                    .order_by(
                        ExpertAnnotationAnswer.task_id,
                        FreeDebateOpportunity.opened_at,
                        FreeDebateOpportunity.sequence_no,
                    )
                )
            ).all()
        )
        answers_by_task: dict[UUID, list[ExpertAnnotationAnswer]] = {}
        for answer, _opportunity in answer_rows:
            answers_by_task.setdefault(answer.task_id, []).append(answer)
        responses = [
            ExpertAnnotationTaskResponse(
                id=task.id,
                batch_id=task.batch_id,
                status=cast(Literal["PENDING", "IN_PROGRESS", "SUBMITTED"], task.status),
                submitted_at=task.submitted_at,
                items=[
                    ExpertAnnotationItemResponse(
                        opportunity_id=answer.opportunity_id,
                        frozen_payload=dict(answer.frozen_payload),
                        q1=answer.q1,
                        q2=dict(answer.q2) if answer.q2 is not None else None,
                        q3=answer.q3,
                        client_version=answer.client_version,
                        saved_at=answer.saved_at,
                        submitted_at=answer.submitted_at,
                    )
                    for answer in answers_by_task.get(task.id, [])
                ],
            )
            for task in tasks
        ]
        self._assert_annotation_response_budget(responses)
        return responses

    async def list_expert_tasks(
        self, session: AsyncSession, *, user_id: UUID
    ) -> list[ExpertAnnotationTaskResponse]:
        result: list[ExpertAnnotationTaskResponse] = []
        cursor: tuple[datetime, UUID] | None = None
        previous_cursor: tuple[datetime, UUID] | None = None
        seen: set[tuple[datetime, UUID]] = set()
        page_count = 0
        while True:
            if page_count >= self.EXPERIMENT_HISTORY_MAX_PAGES:
                raise APIError("experiment_history_too_large")
            page, next_cursor = await self.list_expert_tasks_page(
                session,
                user_id=user_id,
                limit=self.EXPERIMENT_PAGE_MAX,
                cursor=cursor,
            )
            page_count += 1
            if len(result) + len(page) > self.EXPERIMENT_HISTORY_MAX_ROWS:
                raise APIError("experiment_history_too_large")
            result.extend(page)
            if next_cursor is None:
                return result
            self._validate_annotation_history_cursor(previous_cursor, next_cursor, seen)
            seen.add(next_cursor)
            previous_cursor = next_cursor
            cursor = next_cursor

    async def list_expert_tasks_page(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int = ANNOTATION_PAGE_DEFAULT,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> tuple[list[ExpertAnnotationTaskResponse], tuple[datetime, UUID] | None]:
        bounded_limit = min(max(limit, 1), self.ANNOTATION_PAGE_MAX)
        query = select(ExpertAnnotationTask).where(ExpertAnnotationTask.expert_user_id == user_id)
        if cursor is not None:
            query = query.where(
                or_(
                    ExpertAnnotationTask.created_at < cursor[0],
                    and_(
                        ExpertAnnotationTask.created_at == cursor[0],
                        ExpertAnnotationTask.id < cursor[1],
                    ),
                )
            )
        query = query.order_by(
            ExpertAnnotationTask.created_at.desc(), ExpertAnnotationTask.id.desc()
        ).limit(bounded_limit + 1)
        rows = list((await session.scalars(query)).all())
        has_more = len(rows) > bounded_limit
        page_rows = rows[:bounded_limit]
        next_cursor = (
            (page_rows[-1].created_at, page_rows[-1].id) if has_more and page_rows else None
        )
        return await self._expert_task_responses(session, page_rows), next_cursor

    async def expert_audio(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        speech_id: UUID,
        user_id: UUID,
    ) -> tuple[str, str]:
        task = await session.scalar(
            select(ExpertAnnotationTask).where(
                ExpertAnnotationTask.id == task_id,
                ExpertAnnotationTask.expert_user_id == user_id,
            )
        )
        if task is None:
            raise APIError("experiment_expert_task_not_found")
        answer_count, payload_bytes = (
            await session.execute(
                select(
                    func.count(ExpertAnnotationAnswer.id),
                    func.coalesce(
                        func.sum(
                            func.octet_length(sa_cast(ExpertAnnotationAnswer.frozen_payload, Text))
                        ),
                        0,
                    ),
                ).where(ExpertAnnotationAnswer.task_id == task.id)
            )
        ).one()
        if (
            int(answer_count or 0) > self.ANNOTATION_MAX_AUDIO_AUTH_ROWS
            or int(payload_bytes or 0) > self.ANNOTATION_MAX_AUDIO_AUTH_BYTES
        ):
            self._raise_annotation_payload_too_large()
        payload_rows = (
            (
                await session.execute(
                    select(ExpertAnnotationAnswer.frozen_payload).where(
                        ExpertAnnotationAnswer.task_id == task.id
                    )
                )
            )
            .scalars()
            .all()
        )
        allowed_speech_ids: set[str] = set()
        for frozen_payload in payload_rows:
            raw_payload = frozen_payload
            raw_context: object = raw_payload.get("context")
            if not isinstance(raw_context, dict):
                continue
            context = cast(dict[str, object], raw_context)
            raw_history = context.get("history")
            if not isinstance(raw_history, list):
                continue
            for raw_item in cast(list[object], raw_history):
                if not isinstance(raw_item, dict):
                    continue
                history_item = cast(dict[str, object], raw_item)
                candidate_id = history_item.get("speech_id")
                if candidate_id is not None:
                    allowed_speech_ids.add(str(candidate_id))
                    if len(allowed_speech_ids) > self.ANNOTATION_MAX_ITEMS_PER_TASK * 16:
                        self._raise_annotation_payload_too_large()
        if str(speech_id) not in allowed_speech_ids:
            raise APIError("experiment_expert_opportunity_not_found")
        speech = await session.get(Speech, speech_id)
        if speech is None or not speech.audio_storage_path:
            raise APIError("experiment_annotation_audio_unavailable")
        return speech.audio_storage_path, speech.speaker_kind

    async def save_expert_annotation(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        opportunity_id: UUID,
        user_id: UUID,
        payload: ExpertAnnotationSaveRequest,
    ) -> ExpertAnnotationTaskResponse:
        if set(payload.q2) != self._EXPERT_ACTIONS:
            raise APIError("experiment_expert_answer_invalid")
        async with session.begin():
            task = await session.scalar(
                select(ExpertAnnotationTask)
                .where(
                    ExpertAnnotationTask.id == task_id,
                    ExpertAnnotationTask.expert_user_id == user_id,
                )
                .with_for_update()
            )
            if task is None:
                raise APIError("experiment_expert_task_not_found")
            if task.status == "SUBMITTED":
                raise APIError("experiment_expert_task_locked")
            answer = await session.scalar(
                select(ExpertAnnotationAnswer)
                .where(
                    ExpertAnnotationAnswer.task_id == task.id,
                    ExpertAnnotationAnswer.opportunity_id == opportunity_id,
                )
                .with_for_update()
            )
            if answer is None:
                raise APIError("experiment_expert_opportunity_not_found")
            if answer.submitted_at is not None:
                raise APIError("experiment_expert_answer_locked")
            if answer.q1 is not None and payload.client_version <= answer.client_version:
                raise APIError("experiment_annotation_version_conflict")
            answer.q1 = payload.q1
            answer.q2 = dict(payload.q2)
            answer.q3 = payload.q3
            answer.client_version = payload.client_version
            answer.saved_at = datetime.now(UTC)
            if payload.submit:
                answer.submitted_at = answer.saved_at
            task.status = "IN_PROGRESS"
            await session.flush()
            response = await self._expert_task_response(session, task)
        return response

    async def submit_expert_task(
        self, session: AsyncSession, *, task_id: UUID, user_id: UUID
    ) -> ExpertAnnotationTaskResponse:
        async with session.begin():
            task = await session.scalar(
                select(ExpertAnnotationTask)
                .where(
                    ExpertAnnotationTask.id == task_id,
                    ExpertAnnotationTask.expert_user_id == user_id,
                )
                .with_for_update()
            )
            if task is None:
                raise APIError("experiment_expert_task_not_found")
            if task.status == "SUBMITTED":
                return await self._expert_task_response(session, task)
            incomplete_matches = await session.scalar(
                select(func.count())
                .select_from(ScheduledMatch)
                .where(
                    ScheduledMatch.batch_id == task.batch_id,
                    ScheduledMatch.kind == "FORMAL",
                    ScheduledMatch.status != "COMPLETED",
                )
            )
            if int(incomplete_matches or 0) > 0:
                raise APIError("experiment_expert_batch_incomplete")
            incomplete_answers = await session.scalar(
                select(func.count())
                .select_from(ExpertAnnotationAnswer)
                .where(
                    ExpertAnnotationAnswer.task_id == task.id,
                    ExpertAnnotationAnswer.submitted_at.is_(None),
                )
            )
            if int(incomplete_answers or 0) > 0:
                raise APIError("experiment_expert_task_incomplete")
            task.status = "SUBMITTED"
            task.submitted_at = datetime.now(UTC)
            await session.flush()
            response = await self._expert_task_response(session, task)
        return response

    async def batch_progress(
        self, session: AsyncSession, *, batch_id: UUID
    ) -> ExperimentBatchProgressResponse:
        await self.get_batch(session, batch_id)
        scheduled_matches = list(
            (
                await session.scalars(
                    select(ScheduledMatch)
                    .where(ScheduledMatch.batch_id == batch_id)
                    .order_by(ScheduledMatch.round_no, ScheduledMatch.match_no)
                )
            ).all()
        )
        topic_ids = {scheduled.topic_id for scheduled in scheduled_matches}
        topics = (
            {
                topic.id: topic.title
                for topic in await session.scalars(select(Topic).where(Topic.id.in_(topic_ids)))
            }
            if topic_ids
            else {}
        )
        team_ids = {
            team_id
            for scheduled in scheduled_matches
            for team_id in (scheduled.affirmative_team_id, scheduled.negative_team_id)
        }
        teams = (
            {
                team.id: team.team_code
                for team in await session.scalars(
                    select(ExperimentTeam).where(
                        ExperimentTeam.batch_id == batch_id,
                        ExperimentTeam.id.in_(team_ids),
                    )
                )
            }
            if team_ids
            else {}
        )
        rows: list[ExperimentMatchProgressResponse] = []
        for scheduled in scheduled_matches:
            attempt = await session.scalar(
                select(ExperimentMatchAttempt)
                .where(ExperimentMatchAttempt.scheduled_match_id == scheduled.id)
                .order_by(ExperimentMatchAttempt.attempt_no.desc())
                .limit(1)
            )
            completed = 0
            total = 0
            if attempt is not None:
                total = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ParticipantAnnotationTask)
                        .where(ParticipantAnnotationTask.experiment_attempt_id == attempt.id)
                    )
                    or 0
                )
                completed = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ParticipantAnnotationTask)
                        .where(
                            ParticipantAnnotationTask.experiment_attempt_id == attempt.id,
                            ParticipantAnnotationTask.status == "SUBMITTED",
                        )
                    )
                    or 0
                )
            rows.append(
                ExperimentMatchProgressResponse(
                    scheduled_match_id=scheduled.id,
                    round_no=scheduled.round_no,
                    match_no=scheduled.match_no,
                    kind=cast(Literal["FORMAL", "TRAINING"], scheduled.kind),
                    topic_title=topics.get(scheduled.topic_id, "未知辩题"),
                    affirmative_team_code=teams.get(scheduled.affirmative_team_id, "未知队伍"),
                    negative_team_code=teams.get(scheduled.negative_team_id, "未知队伍"),
                    match_status=scheduled.status,
                    attempt_id=attempt.id if attempt is not None else None,
                    attempt_no=attempt.attempt_no if attempt is not None else None,
                    attempt_status=attempt.status if attempt is not None else None,
                    match_id=attempt.match_id if attempt is not None else None,
                    completed_annotations=completed,
                    total_annotations=total,
                    public_at=attempt.public_at if attempt is not None else None,
                )
            )
        formal = [row for row in rows if row.kind == "FORMAL"]
        return ExperimentBatchProgressResponse(
            batch_id=batch_id,
            formal_completed=sum(row.match_status == "COMPLETED" for row in formal),
            formal_total=len(formal),
            matches=rows,
        )

    async def override_result_visibility(
        self,
        session: AsyncSession,
        *,
        attempt_id: UUID,
        actor_user_id: UUID,
        payload: ExperimentResultOverrideRequest,
    ) -> ExperimentMatchAttempt:
        async with session.begin():
            attempt = await session.get(ExperimentMatchAttempt, attempt_id, with_for_update=True)
            if attempt is None or attempt.status != "COMPLETED":
                raise APIError("experiment_attempt_invalid")
            scheduled = await session.get(ScheduledMatch, attempt.scheduled_match_id)
            if (
                scheduled is None
                or scheduled.effective_attempt_id != attempt.id
                or scheduled.kind != "FORMAL"
            ):
                raise APIError("experiment_attempt_invalid")
            existing = await session.scalar(
                select(ExperimentResultOverride).where(
                    ExperimentResultOverride.experiment_attempt_id == attempt.id
                )
            )
            published_at = datetime.now(UTC)
            if existing is None:
                session.add(
                    ExperimentResultOverride(
                        experiment_attempt_id=attempt.id,
                        published_by=actor_user_id,
                        reason=payload.reason,
                        published_at=published_at,
                    )
                )
            attempt.public_at = attempt.public_at or published_at
            pending = await session.scalar(
                select(BackgroundTask.id).where(
                    BackgroundTask.task_type == "LEADERBOARD_DAILY",
                    BackgroundTask.status.in_(("PENDING", "RUNNING")),
                )
            )
            if pending is None:
                session.add(
                    BackgroundTask(
                        task_type="LEADERBOARD_DAILY",
                        payload={
                            "reason": "experiment_result_override",
                            "attempt_id": str(attempt.id),
                        },
                        max_attempts=2,
                    )
                )
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.experiment.result_published",
                target_type="experiment_attempt",
                target_id=str(attempt.id),
                details={"reason": payload.reason},
            )
        return attempt

    async def enqueue_batch_export(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        actor_user_id: UUID,
    ) -> ExperimentJobResponse:
        async with session.begin():
            batch = await session.get(ExperimentBatch, batch_id)
            if batch is None:
                raise APIError("experiment_batch_not_found")
            task = BackgroundTask(
                task_type="EXPERIMENT_BATCH_EXPORT",
                payload={
                    "batch_id": str(batch.id),
                    "schedule_version": batch.schedule_version,
                    "requested_by": str(actor_user_id),
                    "cutoff_at": datetime.now(UTC).isoformat(),
                },
                max_attempts=2,
            )
            session.add(task)
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.experiment.export_requested",
                target_type="experiment_batch",
                target_id=str(batch.id),
                details={"task_id": str(task.id)},
            )
            await session.flush()
            response = ExperimentJobResponse(
                id=task.id,
                task_type="EXPERIMENT_BATCH_EXPORT",
                status=task.status,
                created_at=task.created_at,
            )
        return response

    async def enqueue_retention_dry_run(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        actor_user_id: UUID,
        payload: ExperimentRetentionRequest,
    ) -> ExperimentJobResponse:
        async with session.begin():
            batch = await session.get(ExperimentBatch, batch_id)
            if batch is None:
                raise APIError("experiment_batch_not_found")
            task = BackgroundTask(
                task_type="EXPERIMENT_RETENTION",
                payload={
                    "batch_id": str(batch.id),
                    "requested_by": str(actor_user_id),
                    "dry_run": payload.dry_run,
                },
                max_attempts=1,
            )
            session.add(task)
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.experiment.retention_dry_run_requested",
                target_type="experiment_batch",
                target_id=str(batch.id),
                details={"task_id": str(task.id), "dry_run": True},
            )
            await session.flush()
            response = ExperimentJobResponse(
                id=task.id,
                task_type="EXPERIMENT_RETENTION",
                status=task.status,
                created_at=task.created_at,
            )
        return response

    async def list_batches_page(
        self,
        session: AsyncSession,
        *,
        limit: int = EXPERIMENT_PAGE_DEFAULT,
        cursor: str | None = None,
    ) -> tuple[list[ExperimentBatch], str | None]:
        page_limit = self._page_limit(limit)
        query = select(ExperimentBatch)
        if cursor:
            cursor_at, cursor_id = self._decode_batch_cursor(cursor)
            query = query.where(
                or_(
                    ExperimentBatch.created_at < cursor_at,
                    and_(
                        ExperimentBatch.created_at == cursor_at,
                        ExperimentBatch.id > cursor_id,
                    ),
                )
            )
        rows = list(
            (
                await session.scalars(
                    query.order_by(ExperimentBatch.created_at.desc(), ExperimentBatch.id)
                    .options(
                        load_only(
                            ExperimentBatch.id,
                            ExperimentBatch.code,
                            ExperimentBatch.title,
                            ExperimentBatch.status,
                            ExperimentBatch.schedule_version,
                            ExperimentBatch.rule_id,
                            ExperimentBatch.format_version_id,
                            ExperimentBatch.training_room_quota,
                            ExperimentBatch.created_by,
                            ExperimentBatch.created_at,
                            ExperimentBatch.updated_at,
                            ExperimentBatch.published_at,
                            ExperimentBatch.disabled_at,
                        )
                    )
                    .limit(page_limit + 1)
                )
            ).all()
        )
        has_more = len(rows) > page_limit
        rows = rows[:page_limit]
        return rows, self._encode_batch_cursor(rows[-1]) if has_more and rows else None

    async def list_batches(self, session: AsyncSession) -> list[ExperimentBatch]:
        # Compatibility helper for internal callers. HTTP callers use the bounded page method;
        # preserve the historical complete-array helper by advancing through bounded pages.
        rows: list[ExperimentBatch] = []
        cursor: str | None = None
        seen: set[tuple[datetime, UUID]] = set()
        previous_cursor: tuple[datetime, UUID] | None = None
        page_count = 0
        while True:
            if page_count >= self.EXPERIMENT_HISTORY_MAX_PAGES:
                raise APIError("experiment_history_too_large")
            page, next_cursor = await self.list_batches_page(
                session, limit=self.EXPERIMENT_PAGE_MAX, cursor=cursor
            )
            page_count += 1
            decoded_cursor = None
            if next_cursor is not None:
                decoded_cursor = self._validate_batch_history_cursor(
                    previous_cursor, next_cursor, seen
                )
            if len(rows) + len(page) > self.EXPERIMENT_HISTORY_MAX_ROWS:
                raise APIError("experiment_history_too_large")
            rows.extend(page)
            if next_cursor is None:
                return rows
            assert decoded_cursor is not None
            seen.add(decoded_cursor)
            previous_cursor = decoded_cursor
            cursor = next_cursor

    async def get_batch(self, session: AsyncSession, batch_id: UUID) -> ExperimentBatch:
        batch = await session.get(ExperimentBatch, batch_id)
        if batch is None:
            raise APIError("experiment_batch_not_found")
        return batch

    async def resolve_context(
        self,
        session: AsyncSession,
        *,
        room_id: UUID | None = None,
        match_id: UUID | None = None,
    ) -> ExperimentContext | None:
        if (room_id is None) == (match_id is None):
            raise ValueError("exactly one of room_id or match_id is required")
        query = _context_query()
        if room_id is not None:
            query = query.where(ExperimentMatchAttempt.room_id == room_id)
        else:
            query = query.where(ExperimentMatchAttempt.match_id == match_id)
        row = (await session.execute(query)).one_or_none()
        if row is None:
            return None
        attempt, scheduled, batch = row
        return ExperimentContext(
            batch_id=batch.id,
            batch_status=batch.status,
            scheduled_match_id=scheduled.id,
            scheduled_match_kind=scheduled.kind,
            scheduled_match_status=scheduled.status,
            schedule_version=scheduled.schedule_version,
            attempt_id=attempt.id,
            attempt_no=attempt.attempt_no,
            attempt_status=attempt.status,
            room_id=attempt.room_id,
            match_id=attempt.match_id,
        )


__all__ = ["ExperimentContext", "ExperimentService"]
