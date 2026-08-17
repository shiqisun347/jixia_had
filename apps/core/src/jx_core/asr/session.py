"""One business speech split into bounded sequential Fun-ASR tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from .protocol import FunAsrConnection, FunAsrSentence, SegmentResult

PCM_FRAME_BYTES = 3200
PCM_SAMPLES_PER_FRAME = 1600
ROTATE_AFTER_FRAMES = 250
HARD_LIMIT_FRAMES = 300
QUEUE_FRAMES = 10


class AsrQueueFull(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AsrSpeechResult:
    speech_id: UUID
    final_text: str
    segments: tuple[SegmentResult, ...]
    audio_duration_ms: int
    first_interim_latency_ms: int | None
    last_segment_no: int


InterimCallback = Callable[[UUID, int, str], Awaitable[None]]
SegmentCallback = Callable[[UUID, int, SegmentResult, int], Awaitable[None]]
SegmentStartedCallback = Callable[[UUID, int, UUID], Awaitable[None]]
SegmentFailedCallback = Callable[[UUID, int, UUID, str], Awaitable[None]]


class AsrSpeechSession:
    def __init__(
        self,
        *,
        speech_id: UUID,
        connection: FunAsrConnection,
        on_interim: InterimCallback,
        on_segment: SegmentCallback,
        on_segment_started: SegmentStartedCallback | None = None,
        on_segment_failed: SegmentFailedCallback | None = None,
        start_segment_no: int = 0,
        initial_text: str = "",
        initial_sample_count: int = 0,
        initial_first_interim_latency_ms: int | None = None,
    ) -> None:
        self.speech_id = speech_id
        self._connection = connection
        self._on_interim = on_interim
        self._on_segment = on_segment
        self._on_segment_started = on_segment_started
        self._on_segment_failed = on_segment_failed
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=QUEUE_FRAMES)
        self._pending = bytearray()
        self._closed = False
        self._sample_count = initial_sample_count
        self._start_segment_no = start_segment_no
        self._initial_text = initial_text
        self._initial_first_interim_latency_ms = initial_first_interim_latency_ms
        self._ready = asyncio.Event()

    async def wait_ready(self, timeout_seconds: float = 10.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=timeout_seconds)

    def feed_pcm(self, pcm: bytes) -> None:
        if self._closed:
            return
        self._pending.extend(pcm)
        while len(self._pending) >= PCM_FRAME_BYTES:
            frame = bytes(self._pending[:PCM_FRAME_BYTES])
            del self._pending[:PCM_FRAME_BYTES]
            try:
                self._queue.put_nowait(frame)
            except asyncio.QueueFull as error:
                raise AsrQueueFull("asr_pcm_queue_full") from error

    async def finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pending:
            self._pending.extend(b"\x00" * (PCM_FRAME_BYTES - len(self._pending)))
            await self._queue.put(bytes(self._pending))
            self._pending.clear()
        await self._queue.put(None)

    async def run(self) -> AsrSpeechResult:
        segment_no = self._start_segment_no
        results: list[SegmentResult] = []
        speech_finished = False
        while not speech_finished:
            segment_no += 1
            segment_state = {"frames_sent": 0, "last_sentence_end_frame": 0}
            stable_prefix = self._initial_text + "".join(item.final_text for item in results)
            segment_stable: dict[int, str] = {}

            async def on_sentence(
                sentence: FunAsrSentence,
                *,
                current_segment: int = segment_no,
                prefix: str = stable_prefix,
                stable: dict[int, str] = segment_stable,
                state: dict[str, int] = segment_state,
            ) -> None:
                if sentence.sentence_end:
                    state["last_sentence_end_frame"] = state["frames_sent"]
                    stable[sentence.sentence_id] = sentence.text
                stable_current = "".join(
                    stable[key] for key in sorted(stable) if key < sentence.sentence_id
                )
                await self._on_interim(
                    self.speech_id,
                    current_segment,
                    f"{prefix}{stable_current}{sentence.text}",
                )

            async def chunks(state: dict[str, int] = segment_state):
                nonlocal speech_finished
                while True:
                    item = await self._queue.get()
                    try:
                        if item is None:
                            speech_finished = True
                            return
                        state["frames_sent"] += 1
                        self._sample_count += PCM_SAMPLES_PER_FRAME
                        yield item
                        if state["frames_sent"] >= HARD_LIMIT_FRAMES or (
                            state["frames_sent"] >= ROTATE_AFTER_FRAMES
                            and state["last_sentence_end_frame"] >= ROTATE_AFTER_FRAMES
                        ):
                            return
                    finally:
                        self._queue.task_done()

            task_id = uuid4()
            if self._on_segment_started is not None:
                await self._on_segment_started(self.speech_id, segment_no, task_id)
            try:
                result = await self._connection.recognize_segment(
                    chunks(),
                    on_sentence=on_sentence,
                    on_started=self._ready.set if not results else None,
                    task_id=task_id,
                )
            except Exception as error:
                if self._on_segment_failed is not None:
                    code = getattr(error, "code", "asr_stream_failed")
                    await self._on_segment_failed(self.speech_id, segment_no, task_id, str(code))
                raise
            results.append(result)
            await self._on_segment(
                self.speech_id,
                segment_no,
                result,
                segment_state["frames_sent"] * PCM_SAMPLES_PER_FRAME,
            )
        first_interim = self._initial_first_interim_latency_ms or next(
            (item.first_interim_latency_ms for item in results if item.first_interim_latency_ms),
            None,
        )
        return AsrSpeechResult(
            speech_id=self.speech_id,
            final_text=self._initial_text + "".join(item.final_text for item in results),
            segments=tuple(results),
            audio_duration_ms=self._sample_count // 16,
            first_interim_latency_ms=first_interim,
            last_segment_no=segment_no,
        )


__all__ = ["AsrQueueFull", "AsrSpeechResult", "AsrSpeechSession"]
