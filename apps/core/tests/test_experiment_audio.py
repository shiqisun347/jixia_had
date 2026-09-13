from pathlib import Path

from jx_core.experiments.routes import _byte_range, _wav_chunks, _wav_header


def test_byte_range_supports_full_explicit_and_suffix_ranges() -> None:
    assert _byte_range(None, 100) == (0, 99)
    assert _byte_range("bytes=10-19", 100) == (10, 19)
    assert _byte_range("bytes=95-", 100) == (95, 99)
    assert _byte_range("bytes=-5", 100) == (95, 99)


def test_byte_range_rejects_invalid_or_multiple_ranges() -> None:
    assert _byte_range("items=0-1", 100) is None
    assert _byte_range("bytes=100-101", 100) is None
    assert _byte_range("bytes=8-2", 100) is None
    assert _byte_range("bytes=0-1,5-6", 100) is None


def test_wav_chunks_project_pcm_as_seekable_wav(tmp_path: Path) -> None:
    pcm = bytes(range(64))
    path = tmp_path / "speech.pcm"
    path.write_bytes(pcm)
    header = _wav_header(len(pcm))
    complete = b"".join(_wav_chunks(path, header, 0, len(header) + len(pcm) - 1))

    assert complete[:4] == b"RIFF"
    assert complete[8:12] == b"WAVE"
    assert complete[44:] == pcm
    assert b"".join(_wav_chunks(path, header, 42, 49)) == complete[42:50]
