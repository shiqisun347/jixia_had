from uuid import UUID, uuid4

import pytest

from jx_core.auth.errors import AuthError
from jx_core.rooms.roster import default_agent_assignments


def _pool() -> tuple[list[dict[str, object]], list[UUID]]:
    voices = (
        ("huozhuoshi_v3.1", "睿断 Agent"),
        ("longwan_v3.1", "执锋 Agent"),
        ("xiaxiaochen_v3.1", "明辨 Agent"),
        ("anmingyuan_v3.1", "博闻 Agent"),
        *((f"spare-{index}", f"备用{index}") for index in range(4)),
    )
    ids = [uuid4() for _ in voices]
    return [
        {"id": str(agent_id), "name": name, "voice": {"provider_voice": voice}}
        for agent_id, (voice, name) in zip(ids, voices, strict=True)
    ], ids


def test_conference_seats_follow_actual_voice_identity_not_pool_order() -> None:
    agents, ids = _pool()
    assignments = default_agent_assignments(agents[::-1], ids, conference=True, all_agent=False)
    assert assignments == {
        ("AFFIRMATIVE", 2): ids[1],
        ("AFFIRMATIVE", 4): ids[3],
        ("NEGATIVE", 2): ids[0],
        ("NEGATIVE", 4): ids[2],
    }


@pytest.mark.parametrize("defect", ["disabled", "wrong_voice", "wrong_name", "old_zhifeng_voice"])
def test_conference_does_not_silently_substitute_an_unavailable_identity(defect: str) -> None:
    agents, ids = _pool()
    if defect == "disabled":
        ids = ids[1:]
    elif defect == "wrong_voice":
        agents[0]["voice"] = {"provider_voice": "yuxiaoyun_v3.1"}
    elif defect == "old_zhifeng_voice":
        agents[1]["voice"] = {"provider_voice": "longhua_v3.1"}
    else:
        agents[0]["name"] = "另一个人设"
    with pytest.raises(AuthError) as raised:
        default_agent_assignments(agents, ids, conference=True, all_agent=False)
    assert raised.value.code == "conference_agents_unavailable"


def test_flexible_and_all_agent_rooms_still_fill_eight_unique_seats() -> None:
    agents, ids = _pool()
    for all_agent in (False, True):
        assignments = default_agent_assignments(agents, ids, conference=False, all_agent=all_agent)
        assert len(assignments) == len(set(assignments.values())) == 8
        assert assignments[("AFFIRMATIVE", 2)] == ids[1]
        assert assignments[("NEGATIVE", 4)] == ids[2]


def test_flexible_custom_pool_preserves_partial_and_empty_compositions() -> None:
    agent_id = uuid4()
    agents = [{"id": str(agent_id), "name": "自定义", "voice": {"provider_voice": "custom"}}]
    assignments = default_agent_assignments(agents, [agent_id], conference=False, all_agent=False)
    assert len(assignments) == 1
    assert default_agent_assignments(agents, [], conference=False, all_agent=False) == {}
