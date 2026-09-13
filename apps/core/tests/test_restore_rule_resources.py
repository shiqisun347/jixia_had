from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts/ops/restore_rule_resources.py"
_SPEC = importlib.util.spec_from_file_location("restore_rule_resources", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

C1_C6 = _MODULE.C1_C6
EXPECTED_COUNTS = _MODULE.EXPECTED_COUNTS
_normalized_topic_values = _MODULE._normalized_topic_values
_report = _MODULE._report


def _rows() -> dict[str, list[dict[str, object]]]:
    rows = {name: [{} for _ in range(count)] for name, count in EXPECTED_COUNTS.items()}
    rows["topics"] = [
        {"title": title}
        for title in C1_C6
    ] + [{"title": f"其他辩题 {index}"} for index in range(4)]
    return rows


def test_restore_report_accepts_exact_unique_whitelist_counts() -> None:
    report = _report(_rows())

    assert report["count_mismatches"] == {}
    assert report["conflicts"] == {
        "duplicate_normalized_topics": 0,
        "missing_confirmed_topics": [],
    }
    assert "agents" in report["excluded"]
    assert "matches" in report["excluded"]


def test_restore_report_rejects_count_mismatch_and_missing_confirmed_topic() -> None:
    rows = _rows()
    rows["models"].clear()
    rows["topics"] = rows["topics"][1:]

    report = _report(rows)

    assert report["count_mismatches"]["models"] == {"expected": 1, "actual": 0}
    assert report["count_mismatches"]["topics"] == {"expected": 10, "actual": 9}
    assert report["conflicts"]["missing_confirmed_topics"] == [
        "过程还是结果更能体现奋斗的价值"
    ]


def test_confirmed_topic_normalization_overrides_backup_metadata() -> None:
    source = {
        "id": "source-id",
        "topic_key": "legacy-code",
        "version": 9,
        "title": "过程还是结果更能体现奋斗的价值",
        "affirmative_text": "旧正方",
        "negative_text": "旧反方",
        "source_text": "旧来源",
        "cedar_id": None,
        "status": "ENABLED",
    }

    values = _normalized_topic_values(source)

    assert "id" not in values
    assert values["topic_key"] == "C1"
    assert values["version"] == 1
    assert values["affirmative_text"] == "过程比结果更能体现奋斗的价值"
    assert values["negative_text"] == "结果比过程更能体现奋斗的价值"
    assert values["source_text"] == "2019 华语辩论世界杯，山东大学 vs 马来亚大学"
