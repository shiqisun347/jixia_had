from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from jx_core.users.avatar import AVATAR_SIZE, AvatarError, AvatarService


def _image_bytes(format_name: str, size: tuple[int, int] = (640, 480)) -> bytes:
    image = Image.new("RGB", size, (200, 80, 120))
    output = BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


def test_avatar_processing_outputs_square_webp_without_trusting_mime() -> None:
    encoded = AvatarService._decode_and_encode(_image_bytes("PNG"))

    with Image.open(BytesIO(encoded)) as image:
        assert image.format == "WEBP"
        assert image.size == (AVATAR_SIZE, AVATAR_SIZE)


def test_avatar_rejects_gif_and_invalid_bytes() -> None:
    with pytest.raises(AvatarError, match="avatar_type_invalid"):
        AvatarService._decode_and_encode(_image_bytes("GIF"))
    with pytest.raises(AvatarError, match="avatar_decode_failed"):
        AvatarService._decode_and_encode(b"not-an-image")


@pytest.mark.asyncio
async def test_avatar_upload_is_bounded_and_path_is_confined(tmp_path: Path) -> None:
    service = AvatarService(tmp_path / "avatars")
    upload = UploadFile(file=BytesIO(b"x" * (2 * 1024 * 1024 + 1)), filename="large.png")

    with pytest.raises(AvatarError, match="avatar_too_large"):
        await service.replace(
            cast(AsyncSession, None),
            user_id=uuid4(),
            upload=upload,
        )

    with pytest.raises(AvatarError, match="avatar_unavailable"):
        service._stored_path("../outside.webp")
