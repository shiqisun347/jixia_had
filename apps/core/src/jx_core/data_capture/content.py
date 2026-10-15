"""Canonical, compressed and content-addressed call payload storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import zlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CallContentBlob, CallContentBlobChunk

CAPTURE_VERSION = 1
SERIALIZATION_VERSION = 1
CONTENT_CHUNK_BYTES = 512 * 1024
CONTENT_COMPRESSION_CONCURRENCY = 4
# Provider captures are currently limited to 2 MiB per side, but content blobs
# are also used by a few legacy call paths. Keep a larger hard ceiling for
# compatible reads while refusing metadata that could make one load allocate
# an unbounded amount of memory.
MAX_CONTENT_BLOB_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_CONTENT_BLOB_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_CONTENT_BLOB_CHUNKS = MAX_CONTENT_BLOB_COMPRESSED_BYTES // CONTENT_CHUNK_BYTES
_compression_slots = asyncio.Semaphore(CONTENT_COMPRESSION_CONCURRENCY)
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "session",
        "session_token",
        "token",
    }
)
ContentKind = Literal["REQUEST", "RESPONSE"]


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in cast(Mapping[object, object], value).items():
            key = str(raw_key)
            if key.lower() in SENSITIVE_KEYS:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize(raw_value)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize(item) for item in cast(Sequence[object], value)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def canonical_payload_bytes(payload: Any) -> bytes:
    """Serialize a structured payload deterministically after key-based redaction."""

    return json.dumps(
        _sanitize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


async def store_content_blob(
    session: AsyncSession,
    *,
    content_kind: ContentKind,
    payload: Any,
) -> UUID:
    """Store or reuse one immutable payload inside the caller's transaction."""

    async with _compression_slots:
        raw, digest, compressed = await asyncio.to_thread(_prepare_content, payload)
    chunks = [
        compressed[offset : offset + CONTENT_CHUNK_BYTES]
        for offset in range(0, len(compressed), CONTENT_CHUNK_BYTES)
    ] or [b""]
    blob_id = uuid4()
    statement = (
        insert(CallContentBlob)
        .values(
            id=blob_id,
            sha256=digest,
            content_kind=content_kind,
            serialization_version=SERIALIZATION_VERSION,
            compression="ZLIB",
            uncompressed_bytes=len(raw),
            compressed_bytes=len(compressed),
            chunk_count=len(chunks),
        )
        .on_conflict_do_nothing(index_elements=("sha256", "serialization_version", "content_kind"))
        .returning(CallContentBlob.id)
    )
    inserted_id = await session.scalar(statement)
    if inserted_id is None:
        existing_id = await session.scalar(
            select(CallContentBlob.id).where(
                CallContentBlob.sha256 == digest,
                CallContentBlob.serialization_version == SERIALIZATION_VERSION,
                CallContentBlob.content_kind == content_kind,
            )
        )
        if existing_id is None:
            raise RuntimeError("call_content_blob_conflict")
        return existing_id

    session.add_all(
        CallContentBlobChunk(blob_id=blob_id, chunk_no=index, payload=chunk)
        for index, chunk in enumerate(chunks)
    )
    return blob_id


def _prepare_content(payload: Any) -> tuple[bytes, str, bytes]:
    raw = canonical_payload_bytes(payload)
    return raw, hashlib.sha256(raw).hexdigest(), zlib.compress(raw, level=6)


async def load_content_blob(session: AsyncSession, blob_id: UUID) -> Any:
    """Load, verify and deserialize one content blob."""

    blob = await session.get(CallContentBlob, blob_id)
    if blob is None:
        raise LookupError("call_content_blob_not_found")
    if (
        blob.compressed_bytes < 0
        or blob.compressed_bytes > MAX_CONTENT_BLOB_COMPRESSED_BYTES
        or blob.uncompressed_bytes < 0
        or blob.uncompressed_bytes > MAX_CONTENT_BLOB_UNCOMPRESSED_BYTES
        or blob.chunk_count <= 0
        or blob.chunk_count > MAX_CONTENT_BLOB_CHUNKS
    ):
        raise ValueError("call_content_blob_too_large")
    chunk_count, chunk_bytes = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.octet_length(CallContentBlobChunk.payload)), 0),
            ).where(CallContentBlobChunk.blob_id == blob_id)
        )
    ).one()
    if chunk_count != blob.chunk_count or chunk_bytes > MAX_CONTENT_BLOB_COMPRESSED_BYTES:
        raise ValueError("call_content_blob_too_large")
    chunks = list(
        (
            await session.scalars(
                select(CallContentBlobChunk)
                .where(CallContentBlobChunk.blob_id == blob_id)
                .order_by(CallContentBlobChunk.chunk_no)
                .limit(MAX_CONTENT_BLOB_CHUNKS + 1)
            )
        ).all()
    )
    if len(chunks) != blob.chunk_count:
        raise ValueError("call_content_blob_incomplete")
    compressed = b"".join(chunk.payload for chunk in chunks)
    if len(compressed) != blob.compressed_bytes:
        raise ValueError("call_content_blob_size_mismatch")
    raw = _decompress_bounded(compressed, max_bytes=MAX_CONTENT_BLOB_UNCOMPRESSED_BYTES)
    if len(raw) != blob.uncompressed_bytes:
        raise ValueError("call_content_blob_size_mismatch")
    if hashlib.sha256(raw).hexdigest() != blob.sha256:
        raise ValueError("call_content_blob_checksum_mismatch")
    return json.loads(raw.decode("utf-8"))


def _decompress_bounded(compressed: bytes, *, max_bytes: int) -> bytes:
    """Decompress a zlib payload without allowing a decompression bomb."""

    if max_bytes <= 0:
        raise ValueError("call_content_blob_too_large")
    decoder = zlib.decompressobj()
    output = bytearray()
    for offset in range(0, len(compressed), CONTENT_CHUNK_BYTES):
        piece = decoder.decompress(
            compressed[offset : offset + CONTENT_CHUNK_BYTES], max_bytes + 1 - len(output)
        )
        output.extend(piece)
        if len(output) > max_bytes or decoder.unconsumed_tail:
            raise ValueError("call_content_blob_too_large")
    output.extend(decoder.flush(max_bytes + 1 - len(output)))
    if len(output) > max_bytes or not decoder.eof:
        raise ValueError("call_content_blob_too_large")
    return bytes(output)
