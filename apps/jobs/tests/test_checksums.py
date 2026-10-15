from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jx_jobs.checksums import sha256_file


def test_sha256_file_streams_in_fixed_chunks(tmp_path, monkeypatch) -> None:
    path = tmp_path / "artifact.bin"
    payload = b"0123456789" * 5
    path.write_bytes(payload)

    def fail_read_bytes(_path: Path) -> bytes:
        raise AssertionError("whole-file read must not be used for checksums")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    assert sha256_file(path, chunk_bytes=3) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_rejects_non_positive_chunk_size(tmp_path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"payload")

    with pytest.raises(ValueError, match="chunk_bytes must be positive"):
        sha256_file(path, chunk_bytes=0)
