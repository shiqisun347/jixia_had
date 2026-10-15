from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import av
import pytest
from scripts.qa.probes.server_postmatch_archive_gate import (
    ArchiveGateError,
    decoded_samples,
    verify_lengths,
    verify_owned_archive,
)

from jx_jobs.audio_worker import AudioSource


def write_opus(path: Path) -> None:
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        for index in range(5):
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate = 48000
            frame.pts = index * 960
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


def test_raw_pcm_samples_are_counted_at_replay_rate(tmp_path: Path) -> None:
    path = tmp_path / "human.pcm"
    path.write_bytes(bytes(16000 * 2))
    assert decoded_samples(AudioSource(path, "pcm_s16le_16000_mono")) == 48000


@pytest.mark.parametrize("size", [0, 3])
def test_invalid_raw_pcm_cannot_pass_decode(size: int, tmp_path: Path) -> None:
    path = tmp_path / "human.pcm"
    path.write_bytes(bytes(size))
    with pytest.raises(ArchiveGateError, match="qa_archive_pcm_size_invalid"):
        decoded_samples(AudioSource(path, "pcm_s16le_16000_mono"))


def test_opus_gate_decodes_all_frames_and_trims_encoder_padding(tmp_path: Path) -> None:
    path = tmp_path / "agent.ogg"
    write_opus(path)
    assert decoded_samples(AudioSource(path, "ogg_opus")) == 4800


def test_invalid_ogg_cannot_pass_decode(tmp_path: Path) -> None:
    path = tmp_path / "agent.ogg"
    path.write_bytes(b"not an audio file")
    with pytest.raises(av.error.FFmpegError):
        decoded_samples(AudioSource(path, "ogg_opus"))


def test_replay_length_rejects_a_missing_host_or_speech_segment() -> None:
    assert verify_lengths(480000, 479952, 25) == 48
    with pytest.raises(ArchiveGateError, match="qa_archive_duration_mismatch"):
        verify_lengths(480000, 384000, 25)


@pytest.mark.asyncio
async def test_storage_escape_stops_before_database_or_worker(tmp_path: Path) -> None:
    root = tmp_path / "jx-full-match-qa-owned"
    settings = SimpleNamespace(
        host_audio_storage_dir=str(root / "host"),
        agent_audio_storage_dir=str(root / "speech"),
        match_audio_storage_dir=str(tmp_path / "foreign"),
    )

    def unused_factory():
        pytest.fail("unowned storage must not query or claim a task")

    receipt = await verify_owned_archive(unused_factory, settings, uuid4(), owned_root=root)
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "qa_archive_storage_not_owned"
    assert receipt["worker_calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_name", "address"),
    [
        ("foreign_database", "127.0.0.1"),
        ("jx_debate_qa_agent_faults", "10.0.0.2"),
        ("jx_debate_qa_agent_faults", None),
        ("jx_debate_qa_agent_faults", "127.0.0.1/32"),
    ],
)
async def test_foreign_database_stops_before_match_query_or_claim(
    tmp_path: Path, database_name: str, address: str | None
) -> None:
    root = tmp_path / "jx-full-match-qa-owned"
    settings = SimpleNamespace(
        host_audio_storage_dir=str(root / "host"),
        agent_audio_storage_dir=str(root / "speech"),
        match_audio_storage_dir=str(root / "output"),
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            assert "current_database()" in str(statement)
            return self

        def mappings(self):
            return self

        def one(self):
            return {"name": database_name, "address": address}

    receipt = await verify_owned_archive(Session, settings, uuid4(), owned_root=root)
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "qa_archive_database_not_owned"
    assert receipt["worker_calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
async def test_owned_host_passes_identity_but_cannot_archive_unfinished_match(
    tmp_path: Path, address: str
) -> None:
    root = tmp_path / "jx-full-match-qa-owned"
    settings = SimpleNamespace(
        host_audio_storage_dir=str(root / "host"),
        agent_audio_storage_dir=str(root / "speech"),
        match_audio_storage_dir=str(root / "output"),
    )

    class Session:
        def __init__(self):
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement, *_params):
            if "current_database()" in str(statement):
                self.row = {"name": "jx_debate_qa_agent_faults", "address": address}
            else:
                assert "SELECT status, runtime_snapshot FROM matches" in str(statement)
                self.row = {"status": "RUNNING"}
            return self

        def mappings(self):
            return self

        def one(self):
            return self.row

    receipt = await verify_owned_archive(Session, settings, uuid4(), owned_root=root)
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "qa_archive_match_not_finished"
    assert receipt["worker_calls"] == 0
