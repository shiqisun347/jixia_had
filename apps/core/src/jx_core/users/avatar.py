"""Bounded local avatar processing and durable file publication."""

from __future__ import annotations

import os
import tempfile
import warnings
from contextlib import suppress
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import UploadFile
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from .avatar_catalog import is_avatar_key

MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_PIXELS = 25_000_000
AVATAR_SIZE = 256
SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class AvatarError(ValueError):
    def __init__(
        self,
        code: Literal[
            "avatar_too_large",
            "avatar_type_invalid",
            "avatar_decode_failed",
            "avatar_unavailable",
        ],
    ) -> None:
        super().__init__(code)
        self.code = code


class AvatarService:
    """Process avatars outside the web root and update the user row atomically."""

    def __init__(self, storage_dir: str | Path) -> None:
        self.root = Path(storage_dir).expanduser().resolve()

    def ensure_storage(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise AvatarError("avatar_unavailable") from None
        if not self.root.is_dir():
            raise AvatarError("avatar_unavailable")

    async def replace(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        upload: UploadFile,
    ) -> User:
        self.ensure_storage()
        payload = await upload.read(MAX_AVATAR_BYTES + 1)
        if len(payload) > MAX_AVATAR_BYTES:
            raise AvatarError("avatar_too_large")
        encoded = self._decode_and_encode(payload)

        temporary_path: Path | None = None
        final_path: Path | None = None
        old_path: Path | None = None
        try:
            async with database_session.begin():
                user = (
                    await database_session.execute(
                        select(User).where(User.id == user_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if user is None or user.status != "ACTIVE":
                    raise AvatarError("avatar_unavailable")
                old_path = self._stored_path(user.avatar_path)
                new_version = user.avatar_version + 1
                final_path = self.root / f"{user.id}-{new_version}-{uuid4().hex}.webp"
                temporary_path = self._write_temporary(encoded)
                os.replace(temporary_path, final_path)
                temporary_path = None
                user.avatar_path = final_path.name
                user.avatar_version = new_version
                await database_session.flush()
        except Exception:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
            if final_path is not None and old_path != final_path:
                with suppress(OSError):
                    final_path.unlink(missing_ok=True)
            raise

        if old_path is not None:
            with suppress(OSError):
                old_path.unlink(missing_ok=True)
        return user

    async def delete(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
    ) -> User:
        old_path: Path | None = None
        async with database_session.begin():
            user = (
                await database_session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or user.status != "ACTIVE":
                raise AvatarError("avatar_unavailable")
            old_path = self._stored_path(user.avatar_path)
            user.avatar_path = None
            user.avatar_version += 1
            await database_session.flush()
        if old_path is not None:
            with suppress(OSError):
                old_path.unlink(missing_ok=True)
        return user

    def read_path(self, relative_path: str | None) -> Path | None:
        path = self._stored_path(relative_path)
        if path is None or not path.is_file():
            return None
        return path

    @staticmethod
    @lru_cache(maxsize=16)
    def preset_bytes(avatar_key: str) -> bytes:
        if not is_avatar_key(avatar_key, "HUMAN"):
            raise AvatarError("avatar_unavailable")
        preset_path = (
            Path(__file__).resolve().parents[1] / "assets" / "avatars" / f"{avatar_key}.webp"
        )
        try:
            payload = preset_path.read_bytes()
        except OSError:
            raise AvatarError("avatar_unavailable") from None
        if not payload:
            raise AvatarError("avatar_unavailable")
        return payload

    @staticmethod
    @lru_cache(maxsize=1)
    def default_bytes() -> bytes:
        image = Image.new("RGB", (AVATAR_SIZE, AVATAR_SIZE), (38, 43, 72))
        output = BytesIO()
        image.save(output, format="WEBP", quality=85, method=4)
        return output.getvalue()

    def _stored_path(self, relative_path: str | None) -> Path | None:
        if not relative_path:
            return None
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise AvatarError("avatar_unavailable") from None
        return candidate

    def _write_temporary(self, encoded: bytes) -> Path:
        with tempfile.NamedTemporaryFile(
            dir=self.root,
            prefix=".avatar-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            return Path(handle.name)

    @staticmethod
    def _decode_and_encode(payload: bytes) -> bytes:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload)) as source:
                    if source.format not in SUPPORTED_FORMATS:
                        raise AvatarError("avatar_type_invalid")
                    if source.width * source.height > MAX_AVATAR_PIXELS:
                        raise AvatarError("avatar_decode_failed")
                    if getattr(source, "n_frames", 1) != 1:
                        raise AvatarError("avatar_type_invalid")
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    mode = "RGBA" if "A" in oriented.getbands() else "RGB"
                    normalized = oriented.convert(mode)
                    fitted = ImageOps.fit(
                        normalized,
                        (AVATAR_SIZE, AVATAR_SIZE),
                        method=Image.Resampling.LANCZOS,
                    )
                    output = BytesIO()
                    fitted.save(output, format="WEBP", quality=85, method=4)
                    return output.getvalue()
        except AvatarError:
            raise
        except (
            OSError,
            SyntaxError,
            ValueError,
            MemoryError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            raise AvatarError("avatar_decode_failed") from None


__all__ = [
    "AVATAR_SIZE",
    "MAX_AVATAR_BYTES",
    "MAX_AVATAR_PIXELS",
    "AvatarError",
    "AvatarService",
]
