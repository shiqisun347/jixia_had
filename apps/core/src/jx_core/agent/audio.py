"""Incremental Ogg/Opus decoding without an FFmpeg realtime subprocess."""

from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, cast

import av
import numpy as np
from av.audio.resampler import AudioResampler
from numpy.typing import NDArray


class _AudioFrame(Protocol):
    def to_ndarray(self) -> NDArray[np.int16]: ...


def apply_pcm16_gain(pcm: bytes, gain: float, *, peak_headroom: float = 0.97) -> bytes:
    """Apply one offline-calibrated fixed gain with a hard peak guard."""
    if not pcm or abs(gain - 1.0) < 0.0005:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    values = samples.astype(np.float32) / 32768.0
    transformed = np.clip(values * gain, -peak_headroom, peak_headroom)
    return (transformed * 32767.0).astype(np.int16).tobytes()


def apply_ogg_opus_gain(
    encoded: bytes,
    gain: float,
    *,
    sample_rate: int = 48_000,
    bit_rate: int = 32_000,
) -> bytes:
    """Apply the runtime PCM gain to a short offline Ogg/Opus preview."""
    if not encoded or abs(gain - 1.0) < 0.0005:
        return encoded
    source = io.BytesIO(encoded)
    target = io.BytesIO()
    with av.open(source, mode="r", format="ogg") as input_container:
        with av.open(target, mode="w", format="ogg") as output_container:
            stream = output_container.add_stream(  # pyright: ignore[reportUnknownMemberType]
                "libopus", rate=sample_rate
            )
            stream.layout = "mono"
            stream.bit_rate = bit_rate
            resampler = AudioResampler(format="s16", layout="mono", rate=sample_rate)

            def encode_frame(converted: object) -> None:
                frame = cast(_AudioFrame, converted)
                samples = frame.to_ndarray()
                if samples.ndim == 2:
                    samples = samples[0]
                pcm = np.asarray(samples, dtype=np.int16).tobytes()
                gained = np.frombuffer(apply_pcm16_gain(pcm, gain), dtype=np.int16)
                output_frame = av.AudioFrame.from_ndarray(
                    gained.reshape(1, -1), format="s16", layout="mono"
                )
                output_frame.sample_rate = sample_rate
                for packet in stream.encode(output_frame):
                    output_container.mux(packet)

            for decoded in input_container.decode(input_container.streams.audio[0]):
                for converted in resampler.resample(decoded):
                    encode_frame(converted)
            for converted in resampler.resample(None):
                encode_frame(converted)
            for packet in stream.encode(None):
                output_container.mux(packet)
    return target.getvalue()


class _Resampler(Protocol):
    def resample(self, frame: object | None) -> list[_AudioFrame]: ...


class GrowingSpool(io.RawIOBase):
    """A blocking read-only view over a file that is appended by the TTS task."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("rb")
        self._condition = threading.Condition()
        self._closed = False
        self._written = 0

    def append_notice(self, written: int) -> None:
        with self._condition:
            self._written = max(self._written, written)
            self._condition.notify_all()

    def finish(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        requested = 32_768 if size < 0 else max(1, size)
        while True:
            data = self._file.read(requested)
            if data:
                return data
            with self._condition:
                if self._closed:
                    return b""
                self._condition.wait(timeout=0.25)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._file.close()
        super().close()


class IncrementalOpusDecoder:
    """Decode an appended Ogg/Opus spool into bounded 20ms PCM frames."""

    def __init__(self, *, sample_rate: int = 48_000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._pcm: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=16)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._reader: GrowingSpool | None = None
        self._error: BaseException | None = None
        self._done = asyncio.Event()

    async def start(self, path: Path) -> None:
        if self._thread is not None:
            raise RuntimeError("decoder_already_started")
        self._loop = asyncio.get_running_loop()
        self._reader = GrowingSpool(path)
        self._thread = threading.Thread(target=self._decode, name="jx-opus-decoder", daemon=True)
        self._thread.start()

    def notify_written(self, byte_count: int) -> None:
        if self._reader is not None:
            self._reader.append_notice(byte_count)

    def finish_input(self) -> None:
        if self._reader is not None:
            self._reader.finish()

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._pcm.get()
            try:
                if item is None:
                    if self._error is not None:
                        raise self._error
                    return
                yield item
            finally:
                self._pcm.task_done()

    async def wait_done(self) -> None:
        await self._done.wait()

    async def close(self) -> None:
        self.finish_input()
        await self.wait_done()
        if self._reader is not None:
            self._reader.close()

    def _decode(self) -> None:
        assert self._reader is not None
        assert self._loop is not None
        try:
            with av.open(self._reader, mode="r", format="ogg") as container:
                stream = container.streams.audio[0]
                resampler = cast(
                    _Resampler,
                    AudioResampler(format="s16", layout="mono", rate=self.sample_rate),
                )
                pending = bytearray()
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        samples = converted.to_ndarray()
                        if samples.ndim == 2:
                            samples = samples[0]
                        pending.extend(np.asarray(samples, dtype=np.int16).tobytes())
                        frame_bytes = self.sample_rate // 50 * self.channels * 2
                        while len(pending) >= frame_bytes:
                            chunk = bytes(pending[:frame_bytes])
                            del pending[:frame_bytes]
                            asyncio.run_coroutine_threadsafe(
                                self._pcm.put(chunk), self._loop
                            ).result()
                for converted in resampler.resample(None):
                    samples = converted.to_ndarray()
                    if samples.ndim == 2:
                        samples = samples[0]
                    pending.extend(np.asarray(samples, dtype=np.int16).tobytes())
                if pending:
                    asyncio.run_coroutine_threadsafe(
                        self._pcm.put(bytes(pending)), self._loop
                    ).result()
        except BaseException as error:
            if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                self._error = error
        finally:
            asyncio.run_coroutine_threadsafe(self._pcm.put(None), self._loop).result()
            self._loop.call_soon_threadsafe(self._done.set)


__all__ = [
    "GrowingSpool",
    "IncrementalOpusDecoder",
    "apply_ogg_opus_gain",
    "apply_pcm16_gain",
]
