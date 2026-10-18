"""Synthetic local media for protocol tests; contains no recordings or event identities."""

import json
import math
import shutil
import struct
import wave
from pathlib import Path


def prepare_static(source_root: Path, target_root: Path) -> Path:
    shutil.copytree(source_root / "static", target_root / "static")
    assets = target_root / "static/assets"
    avatars = assets / "avatars"
    avatars.mkdir()
    shutil.copyfile(assets / "human-01.webp", avatars / "aff-1-000000000001.webp")
    folder = assets / "greetings"
    folder.mkdir()
    greetings = []
    for index, pid in enumerate(("aff-2", "aff-4", "neg-2", "neg-4")):
        duration, rate = 2, 24000
        samples = b"".join(
            struct.pack("<h", int(1200 * math.sin(2 * math.pi * (220 + index * 110) * n / rate)))
            for n in range(duration * rate)
        )
        with wave.open(str(folder / f"{pid}.wav"), "wb") as output:
            output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            output.writeframes(samples)
        greetings.append(
            {
                "id": pid,
                "name": f"合成 Agent {index + 1}",
                "voice": "synthetic-tone",
                "text": "仅用于本地协议测试的合成音，不是真实语音。",
                "url": f"/assets/greetings/{pid}.wav",
                "duration": duration,
            }
        )
    (folder / "manifest.json").write_text(json.dumps({"greetings": greetings}, ensure_ascii=False))
    return target_root
