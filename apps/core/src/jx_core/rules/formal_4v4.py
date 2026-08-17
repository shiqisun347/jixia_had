"""Canonical finite draft for the formal 4v4 debate rule."""

from __future__ import annotations

from typing import cast


def _fixed(
    name: str, side: str, seat_no: int, duration: int, start: str, end: str = ""
) -> dict[str, object]:
    return {
        "name": name,
        "stage_kind": "FIXED_SPEECH",
        "duration_seconds": 0,
        "start_host_text": start,
        "end_host_text": end,
        "parameters": {},
        "actions": [
            {
                "action_kind": "SPEECH",
                "side": side,
                "seat_no": seat_no,
                "duration_seconds": duration,
                "parameters": {},
            }
        ],
    }


def build_formal_4v4_draft() -> dict[str, object]:
    stages: list[dict[str, object]] = [
        _fixed(
            "正方一辩立论",
            "AFFIRMATIVE",
            1,
            180,
            "欢迎来到本场四对四辩论。比赛将依次进行立论、陈词、自由辩论与总结陈词。现在进入立论环节，请正方一辩开始发言，时间三分钟。",
        ),
        _fixed(
            "反方一辩立论",
            "NEGATIVE",
            1,
            180,
            "感谢正方一辩。请反方一辩开始立论，时间三分钟。",
        ),
        _fixed(
            "正方二辩陈词",
            "AFFIRMATIVE",
            2,
            90,
            "感谢反方一辩。现在进入二辩陈词环节，请正方二辩开始发言，时间一分三十秒。",
        ),
        _fixed(
            "反方二辩陈词",
            "NEGATIVE",
            2,
            90,
            "感谢正方二辩。请反方二辩开始陈词，时间一分三十秒。",
        ),
        _fixed(
            "正方三辩陈词",
            "AFFIRMATIVE",
            3,
            90,
            "感谢反方二辩。现在进入三辩陈词环节，请正方三辩开始发言，时间一分三十秒。",
        ),
        _fixed(
            "反方三辩陈词",
            "NEGATIVE",
            3,
            90,
            "感谢正方三辩。请反方三辩开始陈词，时间一分三十秒。",
        ),
        {
            "name": "自由辩论",
            "stage_kind": "FREE_DEBATE",
            "duration_seconds": 180,
            "start_host_text": (
                "感谢反方三辩。现在进入自由辩论环节，双方各有三分钟，正方先发言，单次发言不超过三十秒。"
            ),
            "end_host_text": "",
            "parameters": {"max_speech_seconds": 30, "starting_side": "AFFIRMATIVE"},
            "actions": [],
        },
        _fixed(
            "反方四辩总结",
            "NEGATIVE",
            4,
            180,
            "自由辩论结束，感谢双方辩手。现在进入总结陈词环节，请反方四辩开始总结，时间三分钟。",
        ),
        _fixed(
            "正方四辩总结",
            "AFFIRMATIVE",
            4,
            180,
            "感谢反方四辩。请正方四辩开始总结，时间三分钟。",
            "本场辩论到此结束，感谢各位辩手。",
        ),
        {
            "name": "比赛结束",
            "stage_kind": "END",
            "duration_seconds": 0,
            "start_host_text": "",
            "end_host_text": "",
            "parameters": {},
            "actions": [],
        },
    ]
    return {
        "name": "4v4 正式辩论赛",
        "description": "标准四对四赛制：立论、二三辩陈词、双方各三分钟自由辩论、四辩总结。",
        "side_size": 4,
        "stages": stages,
    }


def expected_formal_4v4_host_copy() -> list[tuple[str, str, str]]:
    stages = cast(list[dict[str, object]], build_formal_4v4_draft()["stages"])
    return [
        (
            str(stage["name"]),
            str(stage.get("start_host_text", "")),
            str(stage.get("end_host_text", "")),
        )
        for stage in stages
    ]
