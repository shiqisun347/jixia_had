from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from jx_jobs.leaderboard_worker import _accumulate_leaderboard_row


def _totals() -> dict[tuple[str, object], dict[str, object]]:
    return defaultdict(
        lambda: {
            "points": 0,
            "wins": 0,
            "matches": 0,
            "score_sum": 0.0,
            "score_count": 0,
            "name": "",
            "match_outcomes": {},
        }
    )


def test_leaderboard_stream_aggregation_uses_sum_and_count() -> None:
    participant_id = uuid4()
    match_id = uuid4()
    totals = _totals()
    row = {
        "match_id": match_id,
        "result": {
            "winner": "AFFIRMATIVE",
            "participants": [{"participant_id": "mp-1", "score": 8.5}],
        },
        "match_participant_id": "mp-1",
        "participant_id": participant_id,
        "kind": "HUMAN",
        "display_name": "测试选手",
        "side": "AFFIRMATIVE",
    }

    _accumulate_leaderboard_row(totals, row)
    _accumulate_leaderboard_row(
        totals,
        {
            **row,
            "match_id": uuid4(),
            "result": {
                "winner": "NEGATIVE",
                "participants": [{"participant_id": "mp-1", "score": 7}],
            },
        },
    )

    bucket = totals[("HUMAN", participant_id)]
    assert bucket["score_sum"] == 15.0
    assert bucket["score_count"] == 2
    assert bucket["points"] == 55
    assert "scores" not in bucket
