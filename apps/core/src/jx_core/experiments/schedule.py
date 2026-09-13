"""Deterministic generation and validation for the six-team experiment schedule."""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

TEAM_COUNT = 6
FORMAL_ROUNDS = 6
MATCHES_PER_ROUND = 3
FORMAL_MATCH_COUNT = FORMAL_ROUNDS * MATCHES_PER_ROUND
TRAINING_ROUND = FORMAL_ROUNDS + 1
SCHEDULE_CSV_COLUMNS = (
    "schedule_version",
    "kind",
    "round_no",
    "match_no",
    "topic_id",
    "affirmative_team_id",
    "negative_team_id",
    *(
        f"{side.lower()}_seat_{seat_no}_{suffix}"
        for side in ("AFFIRMATIVE", "NEGATIVE")
        for seat_no in range(1, 5)
        for suffix in ("kind", "occupant_id")
    ),
)


@dataclass(frozen=True, slots=True)
class TeamSpec:
    team_id: UUID
    member_ids: tuple[UUID, UUID, UUID]
    agent_profile_id: UUID


@dataclass(frozen=True, slots=True)
class SeatSpec:
    side: str
    seat_no: int
    occupant_kind: str
    user_id: UUID | None = None
    agent_profile_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MatchSpec:
    round_no: int
    match_no: int
    topic_id: UUID
    affirmative_team_id: UUID
    negative_team_id: UUID
    seats: tuple[SeatSpec, ...]


@dataclass(frozen=True, slots=True)
class ScheduleIssue:
    code: str
    message: str
    round_no: int | None = None
    match_no: int | None = None
    field: str | int | None = None


def _round_robin(team_ids: tuple[UUID, ...]) -> list[list[tuple[UUID, UUID]]]:
    rotating = list(team_ids)
    rounds: list[list[tuple[UUID, UUID]]] = []
    for _ in range(TEAM_COUNT - 1):
        rounds.append(
            [(rotating[index], rotating[-1 - index]) for index in range(MATCHES_PER_ROUND)]
        )
        rotating = [rotating[0], rotating[-1], *rotating[1:-1]]
    return rounds


def _orient_pairings(
    pairings: list[list[tuple[UUID, UUID]]], team_ids: tuple[UUID, ...]
) -> list[list[tuple[UUID, UUID]]]:
    flat = [pair for round_pairs in pairings for pair in round_pairs]
    affirmative_counts = Counter[UUID]()
    oriented: list[tuple[UUID, UUID]] = []

    def search(index: int) -> bool:
        if index == len(flat):
            return all(affirmative_counts[team_id] == 3 for team_id in team_ids)
        left, right = flat[index]
        games_left = Counter[UUID]()
        for future_left, future_right in flat[index + 1 :]:
            games_left[future_left] += 1
            games_left[future_right] += 1
        for affirmative, negative in ((left, right), (right, left)):
            if affirmative_counts[affirmative] >= 3:
                continue
            affirmative_counts[affirmative] += 1
            feasible = all(
                affirmative_counts[team_id] <= 3
                and affirmative_counts[team_id] + games_left[team_id] >= 3
                for team_id in team_ids
            )
            if feasible:
                oriented.append((affirmative, negative))
                if search(index + 1):
                    return True
                oriented.pop()
            affirmative_counts[affirmative] -= 1
        return False

    if not search(0):
        raise ValueError("unable to orient schedule with three affirmative matches per team")
    return [
        oriented[offset : offset + MATCHES_PER_ROUND]
        for offset in range(0, len(oriented), MATCHES_PER_ROUND)
    ]


def _team_seats(team: TeamSpec, *, side: str, appearance_index: int) -> tuple[SeatSpec, ...]:
    agent_seat = 1 + (appearance_index % 4)
    human_by_seat: dict[int, UUID] = {}
    for seat_no, member_id in zip(
        (seat for seat in range(1, 5) if seat != agent_seat), team.member_ids, strict=True
    ):
        human_by_seat[seat_no] = member_id
    return tuple(
        SeatSpec(
            side=side,
            seat_no=seat_no,
            occupant_kind="AGENT" if seat_no == agent_seat else "HUMAN",
            user_id=human_by_seat.get(seat_no),
            agent_profile_id=team.agent_profile_id if seat_no == agent_seat else None,
        )
        for seat_no in range(1, 5)
    )


def generate_formal_schedule(
    teams: tuple[TeamSpec, ...], topic_ids: tuple[UUID, ...]
) -> tuple[MatchSpec, ...]:
    """Generate six deterministic rounds and all eight fixed seats per match."""

    if len(teams) != TEAM_COUNT:
        raise ValueError("exactly six teams are required")
    if len(topic_ids) != FORMAL_ROUNDS or len(set(topic_ids)) != FORMAL_ROUNDS:
        raise ValueError("exactly six distinct formal topics are required")
    team_ids = tuple(team.team_id for team in teams)
    if len(set(team_ids)) != TEAM_COUNT:
        raise ValueError("team ids must be unique")
    all_members = [member_id for team in teams for member_id in team.member_ids]
    if len(set(all_members)) != TEAM_COUNT * 3:
        raise ValueError("each participant must belong to exactly one team")
    agent_ids = [team.agent_profile_id for team in teams]
    if len(set(agent_ids)) != TEAM_COUNT:
        raise ValueError("each team must use a distinct fixed agent")

    first_five = _round_robin(team_ids)
    # The final round is an explicit rematch of round one. Orientation is solved
    # across all rounds so every team receives exactly three matches per side.
    pairings = [*first_five, list(first_five[0])]
    oriented = _orient_pairings(pairings, team_ids)
    teams_by_id = {team.team_id: team for team in teams}
    appearances = Counter[UUID]()
    matches: list[MatchSpec] = []
    for round_index, round_pairs in enumerate(oriented):
        for match_index, (affirmative_id, negative_id) in enumerate(round_pairs):
            affirmative_appearance = appearances[affirmative_id]
            negative_appearance = appearances[negative_id]
            appearances[affirmative_id] += 1
            appearances[negative_id] += 1
            matches.append(
                MatchSpec(
                    round_no=round_index + 1,
                    match_no=match_index + 1,
                    topic_id=topic_ids[round_index],
                    affirmative_team_id=affirmative_id,
                    negative_team_id=negative_id,
                    seats=(
                        *_team_seats(
                            teams_by_id[affirmative_id],
                            side="AFFIRMATIVE",
                            appearance_index=affirmative_appearance,
                        ),
                        *_team_seats(
                            teams_by_id[negative_id],
                            side="NEGATIVE",
                            appearance_index=negative_appearance,
                        ),
                    ),
                )
            )
    issues = validate_formal_schedule(tuple(matches), teams=teams, topic_ids=topic_ids)
    if issues:
        raise ValueError("generated schedule failed validation: " + issues[0].code)
    return tuple(matches)


def generate_training_schedule(
    formal_matches: tuple[MatchSpec, ...], training_topic_id: UUID
) -> tuple[MatchSpec, ...]:
    """Create one fixed-roster training match per team from the first formal round."""

    first_round = sorted(
        (match for match in formal_matches if match.round_no == 1),
        key=lambda match: match.match_no,
    )
    if len(first_round) != MATCHES_PER_ROUND:
        raise ValueError("formal schedule must contain three first-round matches")
    return tuple(
        MatchSpec(
            round_no=TRAINING_ROUND,
            match_no=index,
            topic_id=training_topic_id,
            affirmative_team_id=match.affirmative_team_id,
            negative_team_id=match.negative_team_id,
            seats=match.seats,
        )
        for index, match in enumerate(first_round, start=1)
    )


def validate_training_schedule(
    matches: tuple[MatchSpec, ...], *, teams: tuple[TeamSpec, ...], training_topic_id: UUID
) -> tuple[ScheduleIssue, ...]:
    issues: list[ScheduleIssue] = []
    team_ids = {team.team_id for team in teams}
    appearances = Counter[UUID]()
    if len(matches) != MATCHES_PER_ROUND:
        issues.append(ScheduleIssue("training_match_count", "训练赛必须恰好包含 3 场比赛"))
    for match in matches:
        location = {"round_no": match.round_no, "match_no": match.match_no}
        if match.round_no != TRAINING_ROUND:
            issues.append(ScheduleIssue("training_round", "训练赛必须位于第 7 轮", **location))
        if match.topic_id != training_topic_id:
            issues.append(ScheduleIssue("training_topic", "训练赛必须使用独立训练辩题", **location))
        if (
            match.affirmative_team_id == match.negative_team_id
            or match.affirmative_team_id not in team_ids
            or match.negative_team_id not in team_ids
        ):
            issues.append(ScheduleIssue("training_teams", "训练赛双方队伍无效", **location))
            continue
        appearances.update((match.affirmative_team_id, match.negative_team_id))
        if len(match.seats) != 8 or len({(seat.side, seat.seat_no) for seat in match.seats}) != 8:
            issues.append(ScheduleIssue("training_seats", "训练赛必须包含完整固定席位", **location))
    if any(appearances[team_id] != 1 for team_id in team_ids):
        issues.append(ScheduleIssue("training_appearances", "每支队伍必须参加 1 场训练赛"))
    return tuple(issues)


def schedule_to_csv(matches: tuple[tuple[str, MatchSpec], ...], *, schedule_version: int) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SCHEDULE_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for kind, match in matches:
        row: dict[str, object] = {
            "schedule_version": schedule_version,
            "kind": kind,
            "round_no": match.round_no,
            "match_no": match.match_no,
            "topic_id": str(match.topic_id),
            "affirmative_team_id": str(match.affirmative_team_id),
            "negative_team_id": str(match.negative_team_id),
        }
        for seat in match.seats:
            prefix = f"{seat.side.lower()}_seat_{seat.seat_no}"
            row[f"{prefix}_kind"] = seat.occupant_kind
            row[f"{prefix}_occupant_id"] = str(seat.user_id or seat.agent_profile_id)
        writer.writerow(row)
    return "\ufeff" + output.getvalue()


def schedule_from_csv(
    value: str,
) -> tuple[int | None, tuple[tuple[str, MatchSpec], ...], tuple[ScheduleIssue, ...]]:
    reader = csv.DictReader(io.StringIO(value.removeprefix("\ufeff")))
    if reader.fieldnames != list(SCHEDULE_CSV_COLUMNS):
        return None, (), (ScheduleIssue("csv_columns", "CSV 列名或顺序与 v2.1 模板不一致"),)
    parsed: list[tuple[str, MatchSpec]] = []
    issues: list[ScheduleIssue] = []
    versions: set[int] = set()
    for row_index, row in enumerate(reader, start=2):
        try:
            version = int(row["schedule_version"])
            kind = row["kind"]
            if kind not in {"FORMAL", "TRAINING"}:
                raise ValueError("kind")
            versions.add(version)
            seats: list[SeatSpec] = []
            for side in ("AFFIRMATIVE", "NEGATIVE"):
                for seat_no in range(1, 5):
                    prefix = f"{side.lower()}_seat_{seat_no}"
                    occupant_kind = row[f"{prefix}_kind"]
                    occupant_id = UUID(row[f"{prefix}_occupant_id"])
                    if occupant_kind not in {"HUMAN", "AGENT"}:
                        raise ValueError(f"{prefix}_kind")
                    seats.append(
                        SeatSpec(
                            side=side,
                            seat_no=seat_no,
                            occupant_kind=occupant_kind,
                            user_id=occupant_id if occupant_kind == "HUMAN" else None,
                            agent_profile_id=occupant_id if occupant_kind == "AGENT" else None,
                        )
                    )
            parsed.append(
                (
                    kind,
                    MatchSpec(
                        round_no=int(row["round_no"]),
                        match_no=int(row["match_no"]),
                        topic_id=UUID(row["topic_id"]),
                        affirmative_team_id=UUID(row["affirmative_team_id"]),
                        negative_team_id=UUID(row["negative_team_id"]),
                        seats=tuple(seats),
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            issues.append(
                ScheduleIssue(
                    "csv_row_invalid",
                    f"第 {row_index} 行包含无效字段",
                    field=str(error).strip("'") or None,
                )
            )
    if len(versions) != 1 or next(iter(versions), 0) < 1:
        issues.append(ScheduleIssue("csv_schedule_version", "CSV 必须包含同一个正整数版本号"))
    return next(iter(versions), None), tuple(parsed), tuple(issues)


def validate_formal_schedule(
    matches: tuple[MatchSpec, ...],
    *,
    teams: tuple[TeamSpec, ...],
    topic_ids: tuple[UUID, ...],
) -> tuple[ScheduleIssue, ...]:
    """Return every publish-blocking schedule violation in stable order."""

    issues: list[ScheduleIssue] = []
    team_ids = {team.team_id for team in teams}
    expected_topics = set(topic_ids)
    if len(teams) != TEAM_COUNT:
        issues.append(ScheduleIssue("team_count", "正式实验必须恰好包含 6 支队伍"))
    if len(matches) != FORMAL_MATCH_COUNT:
        issues.append(ScheduleIssue("match_count", "正式实验必须恰好包含 18 场比赛"))

    slots = Counter((match.round_no, match.match_no) for match in matches)
    for slot, count in sorted(slots.items()):
        if count != 1:
            issues.append(ScheduleIssue("duplicate_slot", "同一轮次场次不可重复", slot[0], slot[1]))

    appearances = Counter[UUID]()
    affirmative = Counter[UUID]()
    pair_counts: Counter[frozenset[UUID]] = Counter()
    rounds_by_team: Counter[tuple[int, UUID]] = Counter()
    team_by_member = {member_id: team.team_id for team in teams for member_id in team.member_ids}
    agent_to_team = {team.agent_profile_id: team.team_id for team in teams}

    for match in matches:
        location = {"round_no": match.round_no, "match_no": match.match_no}
        side_ids = (match.affirmative_team_id, match.negative_team_id)
        if side_ids[0] == side_ids[1] or any(team_id not in team_ids for team_id in side_ids):
            issues.append(
                ScheduleIssue("invalid_teams", "比赛双方必须是不同的批次队伍", **location)
            )
            continue
        if match.topic_id not in expected_topics:
            issues.append(ScheduleIssue("invalid_topic", "比赛使用了未批准的正式辩题", **location))
        pair_counts[frozenset(side_ids)] += 1
        affirmative[side_ids[0]] += 1
        for team_id in side_ids:
            appearances[team_id] += 1
            rounds_by_team[(match.round_no, team_id)] += 1

        if len(match.seats) != 8:
            issues.append(ScheduleIssue("seat_count", "每场比赛必须恰好有 8 个席位", **location))
            continue
        positions = Counter((seat.side, seat.seat_no) for seat in match.seats)
        if len(positions) != 8 or any(count != 1 for count in positions.values()):
            issues.append(
                ScheduleIssue("seat_positions", "每侧 1 至 4 辩必须各出现一次", **location)
            )
            continue
        for seat in match.seats:
            expected_team = side_ids[0] if seat.side == "AFFIRMATIVE" else side_ids[1]
            if seat.occupant_kind == "HUMAN" and seat.user_id is not None:
                if team_by_member.get(seat.user_id) != expected_team:
                    issues.append(
                        ScheduleIssue("foreign_member", "真人席位不属于该侧固定队伍", **location)
                    )
            elif seat.occupant_kind == "AGENT" and seat.agent_profile_id is not None:
                if agent_to_team.get(seat.agent_profile_id) != expected_team:
                    issues.append(
                        ScheduleIssue("foreign_agent", "Agent 席位不属于该侧固定队伍", **location)
                    )
            else:
                issues.append(
                    ScheduleIssue("invalid_occupant", "席位类型与占用者引用不一致", **location)
                )

    for team in teams:
        if appearances[team.team_id] != 6:
            issues.append(ScheduleIssue("team_appearances", "每支队伍必须参加 6 场正式赛"))
        if affirmative[team.team_id] != 3:
            issues.append(ScheduleIssue("side_balance", "每支队伍必须正反方各 3 场"))

    if sorted(pair_counts.values()) != [1] * 12 + [2] * 3:
        issues.append(ScheduleIssue("pairing_pattern", "排表必须为 15 场单循环加 3 场配对重赛"))
    if any(count != 1 for count in rounds_by_team.values()):
        issues.append(ScheduleIssue("round_collision", "同一队伍不得在同一轮重复参赛"))
    for round_no in range(1, FORMAL_ROUNDS + 1):
        round_matches = [match for match in matches if match.round_no == round_no]
        if len(round_matches) != MATCHES_PER_ROUND:
            issues.append(ScheduleIssue("round_size", "每轮必须恰好包含 3 场比赛", round_no))
        if round_matches and any(
            match.topic_id != topic_ids[round_no - 1] for match in round_matches
        ):
            issues.append(ScheduleIssue("round_topic", "同一轮必须使用该轮指定辩题", round_no))
    return tuple(issues)


__all__ = [
    "FORMAL_MATCH_COUNT",
    "SCHEDULE_CSV_COLUMNS",
    "TRAINING_ROUND",
    "MatchSpec",
    "ScheduleIssue",
    "SeatSpec",
    "TeamSpec",
    "generate_formal_schedule",
    "generate_training_schedule",
    "schedule_from_csv",
    "schedule_to_csv",
    "validate_formal_schedule",
    "validate_training_schedule",
]
