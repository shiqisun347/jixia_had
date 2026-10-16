"""Local controls for the isolated App driver; no servers/provider calls."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "spec307_app_driver", Path(__file__).parents[1] / "probes/server_spec307_app_match_probe.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
probe = cast(Any, module)


def test_failure_projection_retains_exact_asr_counters_without_private_audio_or_text() -> None:
    match_id, speech_id = uuid4(), uuid4()
    session = SimpleNamespace(
        speech_id=speech_id,
        _history=bytearray(b"private-audio" * 8),
        _sample_count=48,
        _initial_sample_count=0,
        _confirmed_local_samples=5,
        _max_audio_duration_ms=120000,
        _max_bytes=3840000,
        _automatic_retries_used=1,
        _current_segment_no=2,
        _closed=True,
        _finalization_deadline=probe.monotonic() + 4,
        _provider_tasks=set(),
        _confirmed_text="private-transcript",
    )
    envelope = SimpleNamespace(
        match_id=match_id,
        speech_id=speech_id,
        attempt_no=1,
        generation_id=None,
        connection_epoch=7,
        context_version=3,
    )
    receiver = SimpleNamespace(
        _speech=session,
        _retained_speech=None,
        _retrying_session=None,
        _callback_fenced_session=None,
        _speech_envelope=envelope,
        _speech_task=None,
        _track_tasks=set(),
        _subscribed_tracks={},
        _recording_file=io.BytesIO(b"recording"),
        _recording_speech_id=speech_id,
        _recording_error=None,
        _closed=False,
        _close_requested=False,
        _connection=SimpleNamespace(
            _model="qwen-audio-3.1-asr-flash-streaming", _retirement_epoch=2
        ),
    )
    receiver._recording_file.seek(5)
    core = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                match_runtime_manager=SimpleNamespace(
                    _speech_runtime=SimpleNamespace(_receivers={match_id: receiver})
                )
            )
        )
    )
    result = probe.asr_failure_projection(core, match_id, None)
    data = json.dumps(result)
    assert "private-audio" not in data and "private-transcript" not in data
    assert result["sessions"][0]["max_bytes"] == 3840000
    assert result["sessions"][0]["history_bytes"] == 104
    assert result["sessions"][0]["automatic_retries_used"] == 1
    assert result["recording_bytes"] == 5
    assert result["envelope"]["connection_epoch"] == 7


def test_original_external_exception_message_is_never_projected() -> None:
    assert probe.safe_failure(ValueError("https://secret.invalid/?token=secret")) == {
        "error_code": "qa_external_failure",
        "exception_class": "ValueError",
    }
    assert (
        probe.safe_failure(probe.ProbeError("qa_match_unexpected_failure"))["error_code"]
        == "qa_match_unexpected_failure"
    )


@pytest.mark.asyncio
async def test_failed_diagnostic_collection_still_preserves_original_error(
    tmp_path: Path,
) -> None:
    clients = SimpleNamespace(
        match_id=uuid4(), snapshot=AsyncMock(side_effect=ValueError("private"))
    )
    core = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        )
    )
    result = await probe.capture_failure_diagnostics(
        core,
        clients,
        None,
        probe.ProbeError("qa_match_unexpected_failure"),
        tmp_path,
        "normal",
        last_snapshot={"status": "ERROR", "sequence": 20, "display_text": "private"},
    )
    data = json.loads((tmp_path / "normal-failure.json").read_text())
    assert result["error_code"] == "qa_match_unexpected_failure"
    assert data["original_error"]["error_code"] == "qa_match_unexpected_failure"
    assert data["last_snapshot"]["status"] == "ERROR"
    assert data["last_snapshot"]["sequence"] == 20
    assert "private" not in json.dumps(data)
    assert data["http_error_class"] == "ValueError"
    assert data["db_error_class"] == "AttributeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("http_failure", [False, True])
async def test_diagnostic_reads_only_owned_rows_and_hashes_formal_text(
    tmp_path: Path,
    http_failure: bool,
) -> None:
    match_id, speech_id = uuid4(), uuid4()
    calls: list[tuple[str, Any]] = []

    class Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def scalar(self, query: Any) -> str:
            assert str(query) == "SHOW transaction_read_only"
            return "on"

        async def execute(self, query: Any, params: Any = None) -> Any:
            sql = str(query)
            calls.append((sql, params))
            rows: list[dict[str, Any]] = []
            if "FROM matches" in sql:
                rows = [{"status": "ERROR", "sequence": 20}]
            elif "FROM match_events" in sql:
                rows = [{"event_type": "match.error", "sequence": 20, "error_code": "asr_failed"}]
            elif "FROM external_calls" in sql:
                rows = [
                    {
                        "model": "qwen-audio-3.1-asr-flash-streaming",
                        "status": "FAILED",
                        "error_code": "asr_pcm_queue_full",
                        "attempt_no": 1,
                    }
                ]
            elif "FROM speeches" in sql:
                rows = [{"id": speech_id, "status": "FINALIZING", "display_text": "private-text"}]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    core = SimpleNamespace(
        factory=Session,
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        ),
    )
    clients = SimpleNamespace(
        match_id=match_id,
        snapshot=AsyncMock(side_effect=ValueError("private HTTP error"))
        if http_failure
        else AsyncMock(
            return_value={"status": "ERROR", "sequence": 20, "cookie": "private-cookie"}
        ),
    )
    summary = await probe.capture_failure_diagnostics(
        core,
        clients,
        None,
        probe.ProbeError("qa_match_unexpected_failure"),
        tmp_path,
        "long-replay",
        {"sent_samples": 99, "raw": "private-token"},
    )
    path = tmp_path / "long-replay-failure.json"
    result = json.loads(path.read_text())
    assert summary["diagnostic_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["match"][0]["sequence"] == 20
    assert result["asr_calls"][0]["error_code"] == "asr_pcm_queue_full"
    assert result["speeches"][0]["text_sha256"] == hashlib.sha256(b"private-text").hexdigest()
    assert result["counters"] == {"sent_samples": 99}
    if http_failure:
        assert result["http_error_class"] == "ValueError"
    assert "private-" not in json.dumps(result)
    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    assert all(params == {"id": match_id} for query, params in calls if "SELECT" in query)
    assert all(
        "LIMIT 64" in query
        for query, _ in calls
        if any(
            table in query
            for table in ("FROM match_events", "FROM external_calls", "FROM speeches")
        )
    )


@pytest.mark.asyncio
async def test_db_failure_does_not_drop_available_http_snapshot(tmp_path: Path) -> None:
    core = SimpleNamespace(
        factory=Mock(side_effect=RuntimeError("private database error")),
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        ),
    )
    clients = SimpleNamespace(
        match_id=uuid4(),
        snapshot=AsyncMock(return_value={"status": "ERROR", "sequence": 20, "raw": "private"}),
    )
    await probe.capture_failure_diagnostics(
        core, clients, None, probe.ProbeError("qa_match_unexpected_failure"), tmp_path, "normal"
    )
    data = json.loads((tmp_path / "normal-failure.json").read_text())
    assert data["snapshot"]["sequence"] == 20
    assert data["db_error_class"] == "RuntimeError"
    assert "private" not in json.dumps(data)


@pytest.mark.asyncio
async def test_late_sql_failure_keeps_earlier_event_rows(tmp_path: Path) -> None:
    class Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def scalar(self, _: Any) -> str:
            return "on"

        async def execute(self, query: Any, _: Any = None) -> Any:
            if "FROM external_calls" in str(query):
                raise RuntimeError("private SQL failure")
            rows = [{"sequence": 20, "event_type": "match.error"}]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    core = SimpleNamespace(
        factory=Session,
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        ),
    )
    clients = SimpleNamespace(
        match_id=uuid4(), snapshot=AsyncMock(return_value={"status": "ERROR", "sequence": 20})
    )
    await probe.capture_failure_diagnostics(
        core, clients, None, probe.ProbeError("qa_match_unexpected_failure"), tmp_path, "normal"
    )
    data = json.loads((tmp_path / "normal-failure.json").read_text())
    assert data["snapshot"]["status"] == "ERROR"
    assert data["events"][0]["sequence"] == 20
    assert data["db_error_class"] == "RuntimeError"
    assert "asr_calls" not in data


@pytest.mark.asyncio
async def test_both_diagnostic_channels_share_one_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_timeout = asyncio.timeout
    entered: set[str] = set()
    observed: list[float] = []

    def timeout(seconds: float) -> Any:
        observed.append(seconds)
        return original_timeout(0.001)

    async def blocked_http() -> dict[str, Any]:
        entered.add("http")
        await asyncio.sleep(10)
        return {}

    class BlockedSession:
        async def __aenter__(self) -> Any:
            entered.add("db")
            await asyncio.sleep(10)
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(probe.asyncio, "timeout", timeout)
    core = SimpleNamespace(
        factory=BlockedSession,
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        ),
    )
    clients = SimpleNamespace(match_id=uuid4(), snapshot=blocked_http)
    baseline = set(asyncio.all_tasks())
    summary = await probe.capture_failure_diagnostics(
        core,
        clients,
        None,
        probe.ProbeError("qa_match_unexpected_failure"),
        tmp_path,
        "normal",
        last_snapshot={"status": "ERROR", "sequence": 20},
    )
    data = json.loads((tmp_path / "normal-failure.json").read_text())
    assert entered == {"http", "db"}
    assert observed == [3]
    assert data["collection_error_class"] == "TimeoutError"
    assert data["http_error_class"] == data["db_error_class"] == "CancelledError"
    assert data["last_snapshot"]["sequence"] == 20
    assert summary["error_code"] == "qa_match_unexpected_failure"
    assert not [task for task in asyncio.all_tasks() - baseline if not task.done()]


@pytest.mark.asyncio
async def test_scenario_captures_before_terminate_and_preserves_original_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.qa.probes.server_spec307_clients import ConferenceClients

    clients = SimpleNamespace(
        match_id=uuid4(),
        prepare_and_connect=AsyncMock(side_effect=probe.ProbeError("qa_match_unexpected_failure")),
        snapshot=AsyncMock(return_value={"status": "ERROR", "sequence": 20}),
        close=AsyncMock(side_effect=ValueError("private cleanup error")),
    )
    monkeypatch.setattr(ConferenceClients, "create", AsyncMock(return_value=clients))
    core = SimpleNamespace(
        base_url="http://127.0.0.1:8027",
        factory=None,
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        ),
    )

    async def retire(_: Any) -> bool:
        captured = json.loads((tmp_path / "normal-failure.json").read_text())
        assert captured["snapshot"]["status"] == "ERROR"
        assert captured["original_error"]["error_code"] == "qa_match_unexpected_failure"
        return True

    monkeypatch.setattr(probe, "retire_failed_owned_case", retire)
    monkeypatch.setattr(probe, "emit", Mock())
    with pytest.raises(probe.ProbeError, match="qa_match_unexpected_failure"):
        await probe.run_scenario(
            "normal",
            core,
            SimpleNamespace(database_url_value="unused"),
            uuid4(),
            "unused",
            {},
            None,
            307,
            tmp_path,
        )
    clients.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_collection_deadline_preserves_original_and_writes_partial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_timeout = asyncio.timeout
    observed: list[float] = []

    def short_timeout(seconds: float) -> Any:
        observed.append(seconds)
        return original_timeout(0.001)

    async def blocked() -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    monkeypatch.setattr(probe.asyncio, "timeout", short_timeout)
    core = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=None))
        )
    )
    clients = SimpleNamespace(match_id=uuid4(), snapshot=blocked)
    summary = await probe.capture_failure_diagnostics(
        core,
        clients,
        SimpleNamespace(speech_id=uuid4()),
        probe.ProbeError("qa_match_unexpected_failure"),
        tmp_path,
        "long-replay",
        last_snapshot={"status": "ERROR", "sequence": 20},
    )
    result = json.loads((tmp_path / "long-replay-failure.json").read_text())
    assert observed == [3]
    assert result["collection_error_class"] == "TimeoutError"
    assert result["runtime"]["human_turn"]["sent_samples"] is None
    assert summary["error_code"] == "qa_match_unexpected_failure"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["natural", "active", "owner", "stuck"])
async def test_cleanup_observation_only_accepts_naturally_retired_owners(mode: str) -> None:
    now = [0.0]
    observations: list[float] = []

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    def clock() -> float:
        return now[0]

    def snapshot(_: Any, __: Any) -> dict[str, Any]:
        observations.append(now[0])
        return {
            "pending": [
                {
                    "coroutine": "Connection.close",
                    "cancelling": 0,
                    "retired": mode != "active",
                    "input_closed": mode != "active",
                }
            ]
            if now[0] < 2 or mode == "stuck"
            else [],
            "owners": {"asr_receivers": int(mode == "owner")},
        }

    result = await probe.observe_retired_cleanup(
        set(), None, snapshot=snapshot, clock=clock, sleep=sleep
    )
    assert result["initial_pending_tasks"] == 1
    assert result["tasks_cancelled_by_observer"] == 0
    assert result["status"] == ("passed" if mode == "natural" else "failed")
    assert observations == ([0, 0.1, 1, 2.1, 5] if mode == "stuck" else [0, 0.1, 1, 2.1])
    assert result["pending_tasks"] == int(mode == "stuck")


def candidate(root: Path) -> None:
    members: dict[str, str] = {}
    for number in range(100):
        relative = f"apps/core/src/module{number}.py"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"VALUE = {number}\n")
        members[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "candidate-manifest.json").write_text(json.dumps({"members": members}))


def test_audit_verifies_every_member_and_rejects_changed_source(tmp_path: Path) -> None:
    candidate(tmp_path)
    manifest, before = probe.audit_sources(tmp_path)
    assert len(before) == 100 and len(manifest) == 64
    (tmp_path / "apps/core/src/module99.py").write_text("CHANGED = True\n")
    with pytest.raises(probe.ProbeError, match="qa_candidate_source_changed"):
        probe.audit_sources(tmp_path)


@pytest.mark.parametrize("relative", [".env", "apps/core/api.md", "../outside.py"])
def test_audit_refuses_secret_or_escape_member(tmp_path: Path, relative: str) -> None:
    candidate(tmp_path)
    path = tmp_path / "candidate-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["members"][relative] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(probe.ProbeError, match="qa_manifest_path_invalid"):
        probe.audit_sources(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", [False, True])
async def test_ack_binds_match_and_revision(monkeypatch: pytest.MonkeyPatch, wrong: bool) -> None:
    reader = asyncio.StreamReader()
    monkeypatch.setattr(probe, "_control_reader", reader)
    match_id = str(uuid4())
    reader.feed_data(
        json.dumps(
            {
                "protocol": probe.PROTOCOL,
                "event": "witness_ack",
                "match_id": str(uuid4()) if wrong else match_id,
                "revision": 3,
            }
        ).encode()
        + b"\n"
    )
    if wrong:
        with pytest.raises(probe.ProbeError, match="qa_witness_ack_mismatch"):
            await probe.acknowledge(match_id, 3)
    else:
        await probe.acknowledge(match_id, 3)


def test_emit_uses_only_dedicated_control_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = io.StringIO()
    monkeypatch.setattr(probe, "CONTROL_STDOUT", channel)
    probe.emit("progress", stage="ready", count=1)
    assert json.loads(channel.getvalue()) == {
        "protocol": probe.PROTOCOL,
        "event": "progress",
        "stage": "ready",
        "count": 1,
    }


@pytest.mark.parametrize("wrong_speech", [False, True])
def test_fault_aborts_only_exact_owned_socket_without_protocol_close(wrong_speech: bool) -> None:
    match_id, speech_id = uuid4(), uuid4()
    transport = SimpleNamespace(abort=Mock(), is_closing=lambda: False)
    connection = SimpleNamespace(_socket=SimpleNamespace(transport=transport), close=AsyncMock())
    receiver = SimpleNamespace(
        match_id=match_id, _speech=SimpleNamespace(speech_id=speech_id), _connection=connection
    )
    runtime = SimpleNamespace(_receivers={match_id: receiver})
    app = SimpleNamespace(
        state=SimpleNamespace(match_runtime_manager=SimpleNamespace(_speech_runtime=runtime))
    )
    if wrong_speech:
        with pytest.raises(probe.ProbeError, match="qa_asr_speech_owner_changed"):
            probe.abort_owned_asr_socket(app, match_id, str(uuid4()))
        transport.abort.assert_not_called()
    else:
        receipt = probe.abort_owned_asr_socket(app, match_id, str(speech_id))
        assert receipt["protocol_close_called"] is False
        transport.abort.assert_called_once()
    connection.close.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_attempts_all_exact_owners_when_core_close_fails() -> None:
    core = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError))
    livekit = SimpleNamespace(close=Mock(return_value=None))
    postgres = SimpleNamespace(close=Mock(return_value=True))
    cleanup, errors = await probe.close_owned(core, livekit, postgres)
    assert cleanup == {"core_closed": False, "livekit_closed": True, "postgres_closed": True}
    assert errors == ["RuntimeError"]
    livekit.close.assert_called_once()
    postgres.close.assert_called_once()


@pytest.mark.asyncio
async def test_projection_hashes_exact_public_text_without_stripping() -> None:
    speech = str(uuid4())
    clients: Any = SimpleNamespace(
        admin=object(),
        match_id=uuid4(),
        request=AsyncMock(
            return_value={"speeches": [{"id": speech, "display_text": " 有效文字\n"}]}
        ),
    )
    result = await probe.expected_projection(clients)
    assert result == {
        "speeches": [
            {
                "speech_id": speech,
                "text_sha256": hashlib.sha256(" 有效文字\n".encode()).hexdigest(),
            }
        ],
        "finished": False,
    }


@pytest.mark.asyncio
async def test_human_turn_waits_for_real_authoritative_end_without_finish_command() -> None:
    speech_id, user = str(uuid4()), uuid4()
    frames: list[bytes] = []

    async def capture(frame: Any) -> None:
        frames.append(bytes(frame.data))

    person: Any = SimpleNamespace(
        user_id=user,
        side="AFFIRMATIVE",
        seat=1,
        publisher=SimpleNamespace(
            source=SimpleNamespace(capture_frame=capture, wait_for_playout=AsyncMock())
        ),
    )
    initial = {
        "status": "RUNNING",
        "current_speech_id": speech_id,
        "action_state": "HUMAN_SPEAKING",
        "current_action": {
            "action_kind": "HUMAN_SPEECH",
            "stage_position": 1,
            "action_position": 1,
        },
        "current_speaker_user_id": str(user),
        "current_speaker_side": "AFFIRMATIVE",
        "current_speaker_seat_no": 1,
        "speech_remaining_ms": 1000,
    }

    async def snapshot(_person: Any) -> dict[str, Any]:
        return {
            **initial,
            "action_state": "SPEECH_FINALIZING" if len(frames) >= 4 else "HUMAN_SPEAKING",
        }

    clients: Any = SimpleNamespace(command=AsyncMock(return_value=initial), snapshot=snapshot)

    def completed(_samples: int) -> str:
        return "公开文本"

    material: Any = SimpleNamespace(pcm=b"\x01\x00" * 48000, completed_text=completed)
    turn = probe.HumanTurn(clients, person, material)
    result = await turn.run()
    assert result["sent_samples"] > 0 and result["sent_samples"] < 48000
    assert result["manually_finished"] is False
    clients.command.assert_awaited_once_with(person, "speech.start")


@pytest.mark.parametrize("drop_middle", [False, True])
def test_complete_block_anchors_detect_omission_even_when_text_nonempty(drop_middle: bool) -> None:
    arguments = [
        ("公开预算可以解释资源如何分配", "居民可以查看道路维护与学校建设的具体支出"),
        ("听取少数人的意见能够发现问题", "公交线路调整需要照顾医院附近的老年乘客"),
        ("记录修正结果能够检验政策效果", "公园建设需要核对多年后的使用情况与维护成本"),
    ]
    texts = [principle + "。例如，" + example + "。" for principle, example in arguments]
    blocks = [
        {"index": index, "end_sample": (index + 1) * 100, "text": text}
        for index, text in enumerate(texts)
    ]

    def completed(_samples: int) -> str:
        return "".join(texts)

    material = SimpleNamespace(blocks=blocks, completed_text=completed)
    recognized = "".join(text for index, text in enumerate(texts) if not drop_middle or index != 1)
    result = probe.quality_coverage(material, 300, recognized, arguments)
    assert result["passed"] is not drop_middle
    assert result["missing_blocks"] == ([1] if drop_middle else [])


def test_partial_unsent_block_is_excluded_from_quality_qualification() -> None:
    arguments = [
        ("预算应当公开透明", "公布每项支出便于居民核对"),
        ("数据应当可以核实", "提供相同时间范围便于比较"),
    ]
    first = "预算应当公开透明，例如公布每项支出便于居民核对。"
    material = SimpleNamespace(
        blocks=[{"index": 0, "end_sample": 100}, {"index": 1, "end_sample": 200}],
        completed_text=lambda _sample: first,  # pyright: ignore[reportUnknownLambdaType]
    )
    result = probe.quality_coverage(material, 150, first + "数据", arguments)
    assert result["passed"] and len(result["blocks"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["RUNNING", "ERROR", "FINISHED", "TERMINATED"])
async def test_failed_owned_case_uses_normal_terminate_only_when_nonterminal(status: str) -> None:
    clients = SimpleNamespace(
        settings=SimpleNamespace(
            app_env="development",
            database_url_value="postgresql+asyncpg://qa@127.0.0.1/jx_debate_qa_agent_faults",
        ),
        base_url="http://127.0.0.1:8027",
        match_id=uuid4(),
        snapshot=AsyncMock(return_value={"status": status}),
        control=AsyncMock(),
    )
    retired = await probe.retire_failed_owned_case(clients)
    assert retired is (status not in {"FINISHED", "TERMINATED"})
    if retired:
        clients.control.assert_awaited_once_with("terminate")
    else:
        clients.control.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_cleanup_refuses_production_before_any_request() -> None:
    clients = SimpleNamespace(
        settings=SimpleNamespace(
            app_env="production",
            database_url_value="postgresql+asyncpg://qa@127.0.0.1/jx_debate_qa_agent_faults",
        ),
        base_url="http://127.0.0.1:8027",
        match_id=uuid4(),
        snapshot=AsyncMock(),
        control=AsyncMock(),
    )
    with pytest.raises(probe.ProbeError, match="qa_failure_cleanup_not_owned"):
        await probe.retire_failed_owned_case(clients)
    clients.snapshot.assert_not_awaited()
    clients.control.assert_not_awaited()


def short_reference_fixture() -> tuple[Any, dict[str, Any], list[tuple[str, str]]]:
    text = "评价要有标准，例如公开预算。"

    def completed(sent: int) -> str:
        return text if sent >= 1_000_000 else ""

    material = SimpleNamespace(
        blocks=[{"index": 0, "end_sample": 1_000_000}], completed_text=completed
    )
    context = {
        "action_kind": "FREE_DEBATE",
        "action_key": "7:0",
        "finish_reason": "TIME_LIMIT",
        "initial_budget_ms": 14708,
        "free_side_remaining_ms": 14708,
        "wall_ms": 15026,
        "recorded_duration_ms": 14800,
        "manually_finished": False,
        "media_interrupted": False,
        "owner_verified": True,
        "asr_succeeded": True,
        "recording_verified": True,
        "event_chain_verified": True,
        "natural_finish_verified": True,
    }
    return material, context, [("评价要有标准", "公开预算")]


def test_lawful_short_free_reference_is_explicit_na_not_quality_pass() -> None:
    material, context, arguments = short_reference_fixture()
    result = probe.quality_coverage(
        material, 696000, "我是反方一辩，评价要有标准。", arguments, context=context
    )
    assert result["status"] == "not_evaluable" and result["passed"] is None
    assert result["qualification_accepted"] is True
    assert result["prefix_cer_diagnostic"] is None and result["edit_distance"] is None
    assert result["blocks"] == result["missing_blocks"] == []


@pytest.mark.parametrize(
    "bad",
    [
        "fixed",
        "long180",
        "no_context",
        "early",
        "empty",
        "asr_failed",
        "wrong_owner",
        "recording",
        "events",
        "not_natural",
        "under_sent",
        "no_samples",
        "bank_not_short",
        "media_interrupted",
    ],
)
def test_partial_reference_cannot_hide_flow_or_long_input_failure(bad: str) -> None:
    material, context, arguments = short_reference_fixture()
    sent, recognized = 696000, "公开合成识别内容"
    if bad == "fixed":
        context["action_kind"] = "HUMAN_SPEECH"
    elif bad == "long180":
        context.update(initial_budget_ms=180000, free_side_remaining_ms=180000, wall_ms=180000)
    elif bad == "no_context":
        pass
    elif bad == "early":
        context["finish_reason"] = "EARLY"
    elif bad == "empty":
        recognized = "  ，。 "
    elif bad == "asr_failed":
        context["asr_succeeded"] = False
    elif bad == "wrong_owner":
        context["owner_verified"] = False
    elif bad == "recording":
        context["recording_verified"] = False
    elif bad == "events":
        context["event_chain_verified"] = False
    elif bad == "not_natural":
        context["natural_finish_verified"] = False
    elif bad == "under_sent":
        sent = 48000
    elif bad == "no_samples":
        sent = 0
    elif bad == "bank_not_short":
        context["free_side_remaining_ms"] = 90000
    else:
        context["media_interrupted"] = True
    result = probe.quality_coverage(
        material, sent, recognized, arguments, context=None if bad == "no_context" else context
    )
    assert result["status"] == "failed" and result["passed"] is False
    assert result["qualification_accepted"] is False


def test_exact_complete_block_boundary_stays_strict() -> None:
    material, context, arguments = short_reference_fixture()
    context.update(
        initial_budget_ms=20833,
        free_side_remaining_ms=20833,
        wall_ms=20833,
        recorded_duration_ms=20833,
    )
    before = probe.quality_coverage(
        material, 999999, "评价要有标准，例如公开预算。", arguments, context=context
    )
    assert before["status"] == "not_evaluable" and before["passed"] is None
    exact = probe.quality_coverage(
        material, 1_000_000, "评价要有标准，例如公开预算。", arguments, context=context
    )
    assert exact["status"] == "passed" and exact["passed"] is True and len(exact["blocks"]) == 1
    missing = probe.quality_coverage(
        material, 1_000_000, "其他内容完全不同。", arguments, context=context
    )
    assert missing["status"] == "failed" and missing["passed"] is False
