"""Streaming checksums for artifacts that may be larger than worker memory."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHECKSUM_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path, *, chunk_bytes: int = _CHECKSUM_CHUNK_BYTES) -> str:
    """Return a file's SHA-256 digest without loading the whole file.

    Artifact exports and generated audio are bounded on disk, but they can
    still be much larger than a worker's memory budget.  Hashing in fixed
    chunks keeps the checksum pass from creating a second full-size bytes
    allocation alongside the archive or audio buffer.
    """

    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["sha256_file"]
