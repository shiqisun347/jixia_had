"""Local tool controls use synthetic rows and generated silence, never providers."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import av
import pytest
from scripts.qa.probes import server_spec304_host_fixture as probe

from jx_core.models import HostAudioAsset, Room, Rule, RuleAudioGeneration, VoiceProfile
from jx_core.rules.formal_4v4 import build_formal_4v4_draft
from jx_core.rules.schemas import VoiceProfileCreate

REAL_MEASURE_WELCOME = probe.measure_welcome_preface
# The helper intentionally inspects boundary keys, as the runtime compiler does.
# pyright: reportPrivateUsage=false


def write_opus(path: Path, seconds: int = 6) -> None:
    with av.open(str(path), "w") as output:
        stream = output.add_stream(  # pyright: ignore[reportUnknownMemberType]
            "libopus", rate=48000
        )
        stream.layout = "mono"
        for index in range(seconds * 50):
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate = 48000
            frame.pts = index * 960
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


def draft_loader(value: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    def load(*_: Any) -> dict[str, Any]:
        return value

    return load


def test_short_draft_preserves_actions_and_timings_without_mutating_original() -> None:
    original = cast(dict[str, Any], build_formal_4v4_draft())
    before = deepcopy(original)
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def loader(tag: str, *flags: Any) -> Any:
        calls.append((tag, flags))
        return original

    candidate = probe.short_draft(loader, "owned", False, False, False)
    assert original == before
    expected = deepcopy(before)
    expected["stages"][0]["start_host_text"] = (
        probe.HOST_WELCOME + "\n" + before["stages"][0]["start_host_text"]
    )
    expected["stages"][-2]["end_host_text"] = probe.HOST_CLOSING
    assert candidate == expected
    assert calls == [("owned", (False, False, False))]


@pytest.mark.parametrize("end_boundary", ["start_host_text", "end_host_text", "both", "none"])
def test_closing_targets_actual_end_boundary_without_losing_final_debater_cue(
    end_boundary: str,
) -> None:
    original = cast(dict[str, Any], build_formal_4v4_draft())
    stages: Any = original["stages"]
    if end_boundary in {"start_host_text", "both"}:
        stages[-1]["start_host_text"] = "合法END开场文本。"
    if end_boundary in {"end_host_text", "both"}:
        stages[-1]["end_host_text"] = "合法END末段文本。"
    first_cue, last_cue = stages[0]["start_host_text"], stages[-2]["start_host_text"]
    receipt: dict[str, Any] = {}
    actual = probe.short_draft(draft_loader(original), "owned", boundary_receipt=receipt)
    assert actual["stages"][0]["start_host_text"] == probe.HOST_WELCOME + "\n" + first_cue
    assert actual["stages"][-2]["start_host_text"] == last_cue
    key = (
        "stage-9-end"
        if end_boundary == "none"
        else "stage-10-end"
        if end_boundary in {"end_host_text", "both"}
        else "stage-10-start"
    )
    assert receipt["closing_segment_key"] == key
    assert probe._stage_boundaries(actual["stages"])[-1] == (key, probe.HOST_CLOSING)
    assert probe.short_draft(draft_loader(actual), "owned") == actual


def test_empty_host_rule_adds_boundary_texts_without_touching_speech_timing() -> None:
    original = cast(dict[str, Any], build_formal_4v4_draft())
    stages = cast(list[dict[str, Any]], original["stages"])
    for stage in stages:
        stage["start_host_text"] = stage["end_host_text"] = ""
    candidate = probe.short_draft(draft_loader(original), "owned")
    assert candidate["stages"][0]["start_host_text"] == probe.HOST_WELCOME + "\n"
    assert candidate["stages"][-2]["end_host_text"] == probe.HOST_CLOSING
    for old, new in zip(stages, candidate["stages"], strict=True):
        assert old["actions"] == new["actions"]


@pytest.mark.parametrize(("welcome", "closing"), [(5000, 8000), (8000, 5000), (6500, 6500)])
def test_boundary_gate_accepts_only_inclusive_five_to_eight_seconds(
    welcome: int, closing: int
) -> None:
    probe.verify_boundary_durations(welcome, closing)


@pytest.mark.parametrize(
    ("welcome", "closing"), [(4999, 6000), (8001, 6000), (6000, 4999), (6000, 8001)]
)
def test_boundary_gate_rejects_missing_or_excess_duration(welcome: int, closing: int) -> None:
    with pytest.raises(probe.HostFixtureError, match="duration_out_of_range"):
        probe.verify_boundary_durations(welcome, closing)


def test_host_duration_is_counted_from_actual_decoded_samples(tmp_path: Path) -> None:
    path = tmp_path / "real-synthetic.ogg"
    write_opus(path)
    assert probe.decoded_duration_ms(path) == 6000


@pytest.mark.asyncio
async def test_loader_excludes_host_alias_before_the_eight_voice_limit() -> None:
    voices = [
        SimpleNamespace(
            id=uuid4(),
            name=f"Voice {index}",
            kind="AGENT",
            provider_voice=f"synthetic-{index}",
            rate=1.0,
            chars_per_second=4.0,
            playback_gain=1.0,
            avatar_key="agent-01",
        )
        for index in range(8)
    ]
    observed: list[str] = []
    material = object()

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def scalars(self, query: Any) -> Any:
            observed.append(str(query.compile(compile_kwargs={"literal_binds": True})))
            return SimpleNamespace(all=lambda: voices)

    async def original(_: Any) -> Any:
        return material, [{"kind": "HOST", "provider_voice": "old-host"}]

    actual, selected = await probe.load_resources(
        SimpleNamespace(session_factory=Session), original=original
    )
    assert actual is material
    assert selected[0]["kind"] == "HOST"
    assert selected[0]["provider_voice"] == probe.CONFERENCE_HOST_VOICE
    assert selected[0]["name"] == probe.CONFERENCE_HOST_NAME
    validated_host = VoiceProfileCreate.model_validate(selected[0])
    assert validated_host.avatar_key is None
    assert len(selected) == 9 and all(item["kind"] == "AGENT" for item in selected[1:])
    assert "NOT IN ('longanwen_v3.1', 'longyixuanjun_v3.1')" in observed[0]
    assert "LIMIT 8" in observed[0]


def prepared_fixture(tmp_path: Path) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    root = tmp_path / "jx-full-match-qa-owned" / "host-audio"
    root.mkdir(parents=True)
    host_id, generation_id, rule_id, room_id = (uuid4() for _ in range(4))
    settings = SimpleNamespace(host_audio_storage_dir=str(root), tts_model=probe.TTS_MODEL)
    host = SimpleNamespace(
        id=host_id,
        kind="HOST",
        status="ENABLED",
        name=probe.CONFERENCE_HOST_NAME,
        provider_voice=probe.CONFERENCE_HOST_VOICE,
        rate=1.0,
    )
    generation = SimpleNamespace(
        id=generation_id,
        status="SUCCEEDED",
        review_status="APPROVED",
        is_current=True,
        total_count=2,
        ready_count=2,
        failed_count=0,
    )
    rule = SimpleNamespace(
        id=rule_id,
        name="真实短场QA-owned",
        current_audio_generation_id=generation_id,
        host_voice_profile_id=host_id,
    )
    original_draft = {
        "stages": [
            {
                "position": 1,
                "stage_kind": "FIXED_SPEECH",
                "start_host_text": "请正方一辩开始发言，时间十二秒。",
                "end_host_text": "",
            },
            {
                "position": 2,
                "stage_kind": "END",
                "start_host_text": "原合法END谢幕。",
                "end_host_text": "",
            },
        ]
    }
    boundary_inputs: dict[str, Any] = {}
    actual_draft = probe.short_draft(
        draft_loader(original_draft), "owned", boundary_receipt=boundary_inputs
    )
    assets: list[SimpleNamespace] = []
    for index, (key, content) in enumerate(
        probe._stage_boundaries(actual_draft["stages"]), 1
    ):
        seconds = 12 if index == 1 else 6
        path = root / f"host-{index}.ogg"
        write_opus(path, seconds=seconds)
        assets.append(
            SimpleNamespace(
                id=uuid4(),
                segment_key=key,
                text=content,
                text_hash=hashlib.sha256(content.encode()).hexdigest(),
                input_hash=f"synthetic-input-{index}",
                input_snapshot={
                    "text": content,
                    "text_hash": hashlib.sha256(content.encode()).hexdigest(),
                    "voice_profile_id": str(host_id),
                    "provider_voice": probe.CONFERENCE_HOST_VOICE,
                    "rate": 1.0,
                    "model": probe.TTS_MODEL,
                },
                status="READY",
                voice_profile_id=host_id,
                generation_id=generation_id,
                storage_path=path.name,
                checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
                duration_ms=seconds * 1000,
            )
        )
    snapshot: dict[str, Any] = {
        "stages": actual_draft["stages"],
        "host_identity": {
            "voice_profile_id": str(host_id),
            "name": probe.CONFERENCE_HOST_NAME,
            "provider_voice": probe.CONFERENCE_HOST_VOICE,
            "rate": 1.0,
            "model": probe.TTS_MODEL,
            "generation_id": str(generation_id),
        },
        "host_audio": [
            {
                "segment_key": item.segment_key,
                "voice_profile_id": str(host_id),
                "provider_voice": probe.CONFERENCE_HOST_VOICE,
                "rate": 1.0,
                "model": probe.TTS_MODEL,
                "generation_id": str(generation_id),
                "input_hash": item.input_hash,
                "storage_path": item.storage_path,
                "duration_ms": item.duration_ms,
                "text_hash": item.text_hash,
            }
            for item in assets
        ],
    }
    room = SimpleNamespace(
        id=room_id,
        rule_id=rule_id,
        title="真实短场QA-owned",
        label="服务器隔离合成QA",
        rule_snapshot=snapshot,
        format_snapshot={
            "agents": [{"voice": {"provider_voice": f"synthetic-{index}"}} for index in range(8)]
        },
    )
    rows = {Room: room, Rule: rule, RuleAudioGeneration: generation, VoiceProfile: host}
    controls: dict[str, Any] = {
        "rows": rows, "assets": assets, "snapshot": snapshot, "room": room
    }

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def execute(self, statement: Any) -> Any:
            assert "current_database()" in str(statement)
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {"name": "jx_debate_qa_agent_faults", "address": "127.0.0.1"}
                )
            )

        async def get(self, kind: Any, _: Any) -> Any:
            return rows[kind]

        async def scalars(self, query: Any) -> Any:
            assert query.column_descriptions[0]["entity"] is HostAudioAsset
            return SimpleNamespace(all=lambda: assets)

    return (
        Session,
        settings,
        {
            "room_id": room_id,
            "host_assets": 2,
            "conference_host_boundary_inputs": boundary_inputs,
        },
        controls,
    )


@pytest.fixture(autouse=True)
def synthetic_preface_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the gate's pure fixture is synthetic; adapter tests below opt out."""

    async def measure(_: Any, __: Path) -> dict[str, Any]:
        return {
            "status": "passed",
            "actual_provider": True,
            "provider_voice": probe.CONFERENCE_HOST_VOICE,
            "model": probe.TTS_MODEL,
            "rate": 1.0,
            "text_sha256": hashlib.sha256(probe.HOST_WELCOME.encode()).hexdigest(),
            "decoded_ms": 6000,
            "temporary_audio_removed": True,
        }

    monkeypatch.setattr(probe, "measure_welcome_preface", measure)


@pytest.mark.asyncio
async def test_prepared_gate_verifies_same_identity_files_and_actual_decoded_boundaries(
    tmp_path: Path,
) -> None:
    factory, settings, values, controls = prepared_fixture(tmp_path)
    before = deepcopy(controls["snapshot"])
    result = await probe.verify_prepared(factory, settings, values)
    assert result["status"] == "passed"
    assert result["welcome_preface_ms"] == result["closing_decoded_ms"] == 6000
    assert result["welcome_whole_asset_decoded_ms"] == 12_000
    assert "welcome_decoded_ms" not in result
    assert result["original_first_host_prompt_preserved"]
    assert result["original_first_host_prompt_present"]
    assert result["welcome_segment_key"] == "stage-1-start"
    assert result["closing_segment_key"] == "stage-2-start"
    assert result["all_assets_frozen"] and result["host_reserved_absent_from_agents"]
    assert result["explicit_qa_configuration"] and not result["production_host_modified"]
    assert values["conference_host_fixture"] == result
    assert controls["snapshot"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["model", "voice", "generation", "agent_alias", "checksum", "duration"]
)
async def test_prepared_gate_cannot_pass_mixed_or_inaccurate_assets(
    tmp_path: Path, fault: str
) -> None:
    factory, settings, values, controls = prepared_fixture(tmp_path)
    if fault == "agent_alias":
        controls["room"].format_snapshot["agents"][0]["voice"]["provider_voice"] = (
            "longyixuanjun_v3.1"
        )
    elif fault == "generation":
        controls["snapshot"]["host_audio"][0]["generation_id"] = str(uuid4())
    elif fault == "checksum":
        controls["assets"][0].checksum = "invalid"
    elif fault == "duration":
        controls["assets"][0].duration_ms = 1000
        controls["snapshot"]["host_audio"][0]["duration_ms"] = 1000
    else:
        controls["assets"][0].input_snapshot["model" if fault == "model" else "provider_voice"] = (
            "different"
        )
    with pytest.raises(probe.HostFixtureError):
        await probe.verify_prepared(factory, settings, values)
    assert "conference_host_fixture" not in values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["missing", "cue", "order", "hash", "preface_long", "preface_voice"]
)
async def test_preface_and_original_cue_have_distinct_strict_proofs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    factory, settings, values, controls = prepared_fixture(tmp_path)
    if fault == "missing":
        values.pop("conference_host_boundary_inputs")
    elif fault == "cue":
        controls["snapshot"]["stages"][0]["start_host_text"] = probe.HOST_WELCOME
    elif fault == "order":
        controls["snapshot"]["stages"].reverse()
    elif fault == "hash":
        values["conference_host_boundary_inputs"]["original_first_prompt_sha256"] = "wrong"
    else:
        original = probe.measure_welcome_preface

        async def invalid(*args: Any) -> dict[str, Any]:
            receipt = await original(*args)
            receipt["decoded_ms" if fault == "preface_long" else "provider_voice"] = (
                8001 if fault == "preface_long" else "another-voice"
            )
            return receipt

        monkeypatch.setattr(probe, "measure_welcome_preface", invalid)
    with pytest.raises(probe.HostFixtureError):
        await probe.verify_prepared(factory, settings, values)
    assert "conference_host_fixture" not in values


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "constructor", "provider"])
async def test_independent_preface_uses_jobs_adapter_and_removes_temporary_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import jx_jobs.tts as jobs_tts  # pyright: ignore[reportMissingTypeStubs]

    root = tmp_path / "jx-full-match-qa-owned" / "host-audio"
    root.mkdir(parents=True)
    arguments: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    closed: list[bool] = []

    class Client:
        def __init__(self, **values: Any) -> None:
            arguments.append(values)
            if failure == "constructor":
                raise RuntimeError("synthetic constructor failure")

        async def synthesize_to_file(self, **values: Any) -> None:
            calls.append(values)
            path = values["output_path"]
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            write_opus(path)
            if failure == "provider":
                raise RuntimeError("synthetic provider failure")

        async def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(jobs_tts, "DashScopeTTSClient", Client)
    settings = SimpleNamespace(
        tts_ws_url="wss://synthetic.invalid",
        tts_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic"),
        tts_workspace_id="synthetic-workspace",
        tts_model=probe.TTS_MODEL,
    )
    if failure == "none":
        result = await REAL_MEASURE_WELCOME(settings, root)
        assert result["decoded_ms"] == 6000 and result["temporary_audio_removed"]
        assert result["actual_provider"] and result["model"] == probe.TTS_MODEL
        assert calls[0]["text"] == probe.HOST_WELCOME
        assert calls[0]["voice"] == probe.CONFERENCE_HOST_VOICE and calls[0]["rate"] == 1.0
        assert arguments[0]["model"] == probe.TTS_MODEL
    else:
        with pytest.raises(probe.HostFixtureError, match="measurement_failed"):
            await REAL_MEASURE_WELCOME(settings, root)
    assert closed == ([] if failure == "constructor" else [True])
    assert list(root.parent.iterdir()) == [root]


@pytest.mark.asyncio
async def test_unowned_storage_stops_before_any_database_or_file_access(tmp_path: Path) -> None:
    def unused() -> Any:
        pytest.fail("unowned fixture must stop before DB access")

    with pytest.raises(probe.HostFixtureError, match="not_owned"):
        await probe.verify_prepared(
            unused,
            SimpleNamespace(
                host_audio_storage_dir=str(tmp_path / "foreign"), tts_model=probe.TTS_MODEL
            ),
            {},
        )
