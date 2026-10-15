import asyncio
from pathlib import Path
from uuid import UUID

import pytest

import jx_jobs.audio_worker as audio_worker
from jx_jobs.audio_worker import (
    AudioProcessingError,
    AudioSource,
    build_ffmpeg_command,
    resolve_cleanup_path,
)


def test_ffmpeg_command_normalizes_pcm_and_opus_without_shell() -> None:
    command = build_ffmpeg_command(
        [
            AudioSource(Path("/tmp/human.pcm"), "pcm_s16le_16000_mono"),
            AudioSource(Path("/tmp/agent.ogg"), "ogg_opus"),
        ],
        Path("/tmp/replay.opus"),
    )

    assert command[0] == "ffmpeg"
    assert ["-f", "s16le", "-ar", "16000", "-ac", "1"] == command[6:12]
    assert "concat=n=2:v=0:a=1[out]" in command[command.index("-filter_complex") + 1]
    assert command[command.index("-fs") + 1] == str(audio_worker.POSTMATCH_AUDIO_MAX_OUTPUT_BYTES)
    assert command[-1] == "/tmp/replay.opus"


def test_ffmpeg_command_rejects_empty_source_list() -> None:
    with pytest.raises(AudioProcessingError, match="audio_sources_missing"):
        build_ffmpeg_command([], Path("/tmp/replay.opus"))


def test_ffmpeg_command_rejects_too_many_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_worker, "POSTMATCH_AUDIO_MAX_SOURCES", 1)
    with pytest.raises(AudioProcessingError, match="audio_sources_too_many"):
        build_ffmpeg_command(
            [
                AudioSource(Path("/tmp/first.ogg"), "ogg_opus"),
                AudioSource(Path("/tmp/second.ogg"), "ogg_opus"),
            ],
            Path("/tmp/replay.opus"),
        )


def test_ffmpeg_command_rejects_an_unbounded_source_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_worker, "POSTMATCH_AUDIO_MAX_PATH_CHARS", 8)
    with pytest.raises(AudioProcessingError, match="audio_source_path_too_long"):
        build_ffmpeg_command(
            [AudioSource(Path("/tmp/too-long.ogg"), "ogg_opus")],
            Path("/tmp/out"),
        )


def test_cleanup_path_is_limited_to_configured_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "speech.pcm"

    assert resolve_cleanup_path(str(inside), [allowed]) == inside.resolve()
    with pytest.raises(AudioProcessingError, match="file_cleanup_path_invalid"):
        resolve_cleanup_path(str(tmp_path / "outside.pcm"), [allowed])


def test_cleanup_path_rejects_resolution_errors_and_long_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    def fail_resolve(self: Path) -> Path:
        del self
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(AudioProcessingError, match="file_cleanup_path_invalid"):
        resolve_cleanup_path(str(allowed / "loop"), [allowed])

    monkeypatch.undo()
    monkeypatch.setattr(audio_worker, "POSTMATCH_AUDIO_MAX_PATH_CHARS", 8)
    with pytest.raises(AudioProcessingError, match="file_cleanup_path_too_long"):
        resolve_cleanup_path(str(allowed / "file.ogg"), [allowed])


def test_audio_source_path_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "audio"
    root.mkdir()
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(b"audio")
    escaped = root / "escaped.ogg"
    escaped.symlink_to(outside)

    with pytest.raises(AudioProcessingError, match="audio_speech_path_invalid"):
        audio_worker._resolve_audio_source_path(
            "../outside.ogg", root, error_code="audio_speech_path_invalid"
        )
    with pytest.raises(AudioProcessingError, match="audio_speech_path_invalid"):
        audio_worker._resolve_audio_source_path(
            str(escaped), root, error_code="audio_speech_path_invalid"
        )


def test_postmatch_input_budget_covers_the_90_minute_rule_ceiling() -> None:
    raw_pcm_bytes = 90 * 60 * 32_000
    # Leave at least 64 MiB for Agent/host Ogg files and filesystem variance.
    assert audio_worker.POSTMATCH_AUDIO_MAX_INPUT_BYTES >= raw_pcm_bytes + 64 * 1024 * 1024


@pytest.mark.asyncio
async def test_ffmpeg_cancellation_kills_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        returncode: int | None = None

        def __init__(self) -> None:
            self.kill_called = False
            self.wait_called = False
            self.running = asyncio.Event()

        def kill(self) -> None:
            self.kill_called = True

        async def wait(self) -> int:
            if not self.kill_called:
                await self.running.wait()
            self.wait_called = True
            self.returncode = -9
            return -9

    process = FakeProcess()
    captured: dict[str, object] = {}

    async def fake_exec(*args: object, **kwargs: object) -> FakeProcess:
        captured.update(kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    task = asyncio.create_task(audio_worker._run_ffmpeg(["ffmpeg", "-version"]))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.kill_called
    assert process.wait_called
    assert captured["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_snapshot_guard_uses_logical_json_bytes_before_fetching_payload() -> None:
    class FakeMappings:
        def __init__(self, row: dict[str, object] | None) -> None:
            self.row = row

        def mappings(self) -> "FakeMappings":
            return self

        def one_or_none(self) -> dict[str, object] | None:
            return self.row

        def __iter__(self):
            return iter(())

    class FakeSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement: object, params: object) -> FakeMappings:
            del params
            self.statements.append(str(statement))
            return FakeMappings(
                {"snapshot_bytes": audio_worker.POSTMATCH_AUDIO_MAX_SNAPSHOT_BYTES + 1}
            )

    class SessionContext:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        async def __aenter__(self) -> FakeSession:
            return self.session

        async def __aexit__(self, *_args: object) -> None:
            return None

    session = FakeSession()

    def factory() -> SessionContext:
        return SessionContext(session)

    with pytest.raises(AudioProcessingError, match="audio_runtime_snapshot_too_large"):
        await audio_worker._load_sources(
            factory, match_id=UUID(int=0), host_storage_root=Path("/tmp/host")
        )
    assert "octet_length(runtime_snapshot::text)" in session.statements[0]
