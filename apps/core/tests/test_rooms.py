from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from jx_core.auth.errors import ERROR_DEFINITIONS, AuthError
from jx_core.rooms.schemas import RoomCreateRequest
from jx_core.rooms.service import (
    _room_code,
    ensure_formal_human_agent_mix,
    ensure_supported_side_size,
    ensure_unique_agent_ids,
    normalize_room_code,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("jx8k2m", "JX8K2M"), ("  JX8K2M  ", "JX8K2M"), ("234567", "234567")],
)
def test_room_code_normalization(value: str, expected: str) -> None:
    assert normalize_room_code(value) == expected


@pytest.mark.parametrize("value", ["", "ABC", "JX8K2M7", "JX8 2M", "JX8I2M", "JX8O2M", "12A456"])
def test_room_code_rejects_invalid_or_ambiguous_values(value: str) -> None:
    with pytest.raises(AuthError) as raised:
        normalize_room_code(value)
    assert raised.value.code == "room_code_invalid"


def test_start_with_empty_seat_has_actionable_error() -> None:
    status, message = ERROR_DEFINITIONS["room_seats_incomplete"]
    assert status == 409
    assert message == "请先为所有席位安排人类或 Agent"


def test_unseated_debater_and_agent_capacity_errors_are_actionable() -> None:
    assert ERROR_DEFINITIONS["room_debater_unseated"] == (
        409,
        "仍有辩手未选择席位，请先选席或切换为观众",
    )
    assert ERROR_DEFINITIONS["agent_capacity_insufficient"] == (
        409,
        "当前成员组成所需的可用 Agent 数量不足",
    )


def test_room_creation_rejects_legacy_seat_and_fill_controls() -> None:
    payload = {
        "title": "测试房间",
        "label": "训练赛",
        "rule_id": str(uuid4()),
        "custom_topic_title": "辩题",
        "affirmative_text": "正方",
        "negative_text": "反方",
        "human_participation_terms_version": "human-participation-v1",
    }
    RoomCreateRequest.model_validate(payload)
    with pytest.raises(ValueError):
        RoomCreateRequest.model_validate({**payload, "fill_empty_with_agents": False})
    with pytest.raises(ValueError):
        RoomCreateRequest.model_validate(
            {**payload, "organizer_seat": {"side": "AFFIRMATIVE", "seat_no": 1}}
        )


def test_room_agent_ids_must_be_unique() -> None:
    agent_id = uuid4()
    ensure_unique_agent_ids([agent_id, uuid4()])
    with pytest.raises(AuthError) as raised:
        ensure_unique_agent_ids([agent_id, agent_id])
    assert raised.value.code == "agent_duplicate_in_room"


def test_new_room_creation_supports_only_4v4_rules() -> None:
    ensure_supported_side_size(4)
    for side_size in (1, 2, 3, 5):
        with pytest.raises(AuthError) as raised:
            ensure_supported_side_size(side_size)
        assert raised.value.code == "rule_unavailable"


@pytest.mark.parametrize(
    ("affirmative_humans", "negative_humans"),
    [(a, n) for a in range(5) for n in range(5)],
)
def test_formal_4v4_allows_any_complete_member_composition(
    affirmative_humans: int, negative_humans: int
) -> None:
    seats = [
        SimpleNamespace(
            side=side,
            seat_no=index + 1,
            occupant_type=kind,
            user_id=uuid4() if kind == "HUMAN" else None,
            agent_profile_id=uuid4() if kind == "AGENT" else None,
        )
        for side, count in (("AFFIRMATIVE", affirmative_humans), ("NEGATIVE", negative_humans))
        for index, kind in enumerate(["HUMAN"] * count + ["AGENT"] * (4 - count))
    ]
    ensure_formal_human_agent_mix(seats)  # type: ignore[arg-type]


def test_formal_4v4_rejects_incomplete_or_empty_seats() -> None:
    seats = [
        SimpleNamespace(
            side=side, seat_no=index + 1, occupant_type="EMPTY", user_id=None, agent_profile_id=None
        )
        for side in ("AFFIRMATIVE", "NEGATIVE")
        for index in range(4)
    ]
    with pytest.raises(AuthError) as raised:
        ensure_formal_human_agent_mix(seats)  # type: ignore[arg-type]
    assert raised.value.code == "room_seats_incomplete"


def test_room_creation_exposes_composition_controls() -> None:
    payload = {
        "title": "测试房间",
        "label": "训练赛",
        "rule_id": str(uuid4()),
        "custom_topic_title": "辩题",
        "affirmative_text": "正方",
        "negative_text": "反方",
        "human_participation_terms_version": "human-participation-v1",
        "auto_fill_agents": False,
    }
    assert RoomCreateRequest.model_validate(payload).auto_fill_agents is False


def test_new_room_codes_are_numeric_and_legacy_codes_still_resolve() -> None:
    assert _room_code().isdigit()
    assert len(_room_code()) == 6
    assert normalize_room_code(" 123456 ") == "123456"
    assert normalize_room_code(" jx8k2m ") == "JX8K2M"
