from __future__ import annotations

from collections import Counter
from dataclasses import replace
from uuid import UUID

import pytest

from jx_core.experiments.schedule import (
    MatchSpec,
    TeamSpec,
    generate_formal_schedule,
    generate_training_schedule,
    schedule_from_csv,
    schedule_to_csv,
    validate_formal_schedule,
    validate_training_schedule,
)


def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _teams() -> tuple[TeamSpec, ...]:
    return tuple(
        TeamSpec(
            team_id=_uuid(team_no),
            member_ids=tuple(_uuid(100 + team_no * 10 + offset) for offset in range(3)),  # type: ignore[arg-type]
            agent_profile_id=_uuid(200 + team_no),
        )
        for team_no in range(1, 7)
    )


def _topics() -> tuple[UUID, ...]:
    return tuple(_uuid(300 + index) for index in range(6))


def test_generator_satisfies_every_formal_schedule_constraint() -> None:
    teams = _teams()
    topics = _topics()

    matches = generate_formal_schedule(teams, topics)

    assert len(matches) == 18
    assert validate_formal_schedule(matches, teams=teams, topic_ids=topics) == ()
    assert Counter(match.round_no for match in matches) == Counter(
        {round_no: 3 for round_no in range(1, 7)}
    )
    assert Counter(match.topic_id for match in matches) == Counter(
        {topic_id: 3 for topic_id in topics}
    )


def test_generator_is_deterministic_and_assigns_fixed_complete_seats() -> None:
    teams = _teams()
    topics = _topics()

    first = generate_formal_schedule(teams, topics)
    second = generate_formal_schedule(teams, topics)

    assert first == second
    for match in first:
        assert {(seat.side, seat.seat_no) for seat in match.seats} == {
            (side, seat_no) for side in ("AFFIRMATIVE", "NEGATIVE") for seat_no in range(1, 5)
        }
        assert sum(seat.occupant_kind == "AGENT" for seat in match.seats) == 2
        assert all(
            seat.occupant_kind != "AGENT" or seat.seat_no in {1, 2, 3, 4} for seat in match.seats
        )
    assert any(
        seat.occupant_kind == "AGENT" and seat.seat_no == 1
        for match in first
        for seat in match.seats
    )


def test_training_schedule_uses_distinct_topic_and_every_team_once() -> None:
    teams = _teams()
    formal = generate_formal_schedule(teams, _topics())
    training_topic = _uuid(400)

    training = generate_training_schedule(formal, training_topic)

    assert validate_training_schedule(training, teams=teams, training_topic_id=training_topic) == ()
    assert {match.round_no for match in training} == {7}
    assert Counter(
        team_id
        for match in training
        for team_id in (match.affirmative_team_id, match.negative_team_id)
    ) == Counter({team.team_id: 1 for team in teams})


def test_validators_allow_an_agent_in_the_first_seat() -> None:
    teams = _teams()
    formal = list(generate_formal_schedule(teams, _topics()))
    match = formal[0]
    side_seats = [seat for seat in match.seats if seat.side == "AFFIRMATIVE"]
    agent = next(seat for seat in side_seats if seat.occupant_kind == "AGENT")
    first = next(seat for seat in side_seats if seat.seat_no == 1)
    swapped = tuple(
        replace(
            seat,
            occupant_kind=("AGENT" if seat.seat_no == 1 else "HUMAN"),
            user_id=(agent.user_id if seat.seat_no == 1 else first.user_id),
            agent_profile_id=(agent.agent_profile_id if seat.seat_no == 1 else None),
        )
        if seat.side == "AFFIRMATIVE" and seat.seat_no in {1, agent.seat_no}
        else seat
        for seat in match.seats
    )
    formal[0] = replace(match, seats=swapped)

    assert validate_formal_schedule(tuple(formal), teams=teams, topic_ids=_topics()) == ()


def test_schedule_csv_round_trip_preserves_formal_and_training_rows() -> None:
    teams = _teams()
    formal = generate_formal_schedule(teams, _topics())
    training = generate_training_schedule(formal, _uuid(400))
    rows = tuple(("FORMAL", match) for match in formal) + tuple(
        ("TRAINING", match) for match in training
    )

    exported = schedule_to_csv(rows, schedule_version=3)
    version, imported, issues = schedule_from_csv(exported)

    assert exported.startswith("\ufeffschedule_version,kind,")
    assert version == 3
    assert imported == rows
    assert issues == ()


def test_schedule_csv_reports_header_and_row_errors_without_partial_success() -> None:
    assert schedule_from_csv("kind,topic_id\nFORMAL,nope\n")[2][0].code == "csv_columns"
    valid = schedule_to_csv(
        (("FORMAL", generate_formal_schedule(_teams(), _topics())[0]),),
        schedule_version=1,
    )
    invalid = valid.replace(",HUMAN,", ",ROBOT,", 1)

    _, parsed, issues = schedule_from_csv(invalid)

    assert parsed == ()
    assert issues[0].code == "csv_row_invalid"


def test_generator_rejects_invalid_roster_and_topics() -> None:
    teams = _teams()
    topics = _topics()

    with pytest.raises(ValueError, match="six teams"):
        generate_formal_schedule(teams[:-1], topics)
    with pytest.raises(ValueError, match="six distinct"):
        generate_formal_schedule(teams, (*topics[:-1], topics[0]))
    with pytest.raises(ValueError, match="participant"):
        generate_formal_schedule(
            (replace(teams[0], member_ids=teams[1].member_ids), *teams[1:]), topics
        )


def test_validator_returns_publish_blocking_issues_for_tampered_schedule() -> None:
    teams = _teams()
    topics = _topics()
    matches = list(generate_formal_schedule(teams, topics))
    first = matches[0]
    matches[0] = MatchSpec(
        round_no=first.round_no,
        match_no=first.match_no,
        topic_id=_uuid(999),
        affirmative_team_id=first.affirmative_team_id,
        negative_team_id=first.affirmative_team_id,
        seats=first.seats[:7],
    )

    issues = validate_formal_schedule(tuple(matches), teams=teams, topic_ids=topics)

    assert {issue.code for issue in issues} >= {
        "invalid_teams",
        "team_appearances",
        "side_balance",
        "pairing_pattern",
    }
