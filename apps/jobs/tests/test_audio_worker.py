from pathlib import Path

import pytest

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
    assert command[-1] == "/tmp/replay.opus"


def test_ffmpeg_command_rejects_empty_source_list() -> None:
    with pytest.raises(AudioProcessingError, match="audio_sources_missing"):
        build_ffmpeg_command([], Path("/tmp/replay.opus"))


def test_cleanup_path_is_limited_to_configured_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "speech.pcm"

    assert resolve_cleanup_path(str(inside), [allowed]) == inside.resolve()
    with pytest.raises(AudioProcessingError, match="file_cleanup_path_invalid"):
        resolve_cleanup_path(str(tmp_path / "outside.pcm"), [allowed])
