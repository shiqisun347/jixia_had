"""One bounded business speech, continuously consumed with one exact replay."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from time import monotonic
from uuid import UUID, uuid4

from ..data_capture.provider import ProviderCallCapture
from .protocol import FunAsrConnection, FunAsrError, FunAsrSentence, SegmentResult

logger = logging.getLogger("jx-core.asr")
PCM_FRAME_BYTES = 3200
PCM_SAMPLES_PER_FRAME = 1600
# Historical queue constants remain import-compatible; input now has a speech budget.
ROTATE_AFTER_FRAMES = 250
HARD_LIMIT_FRAMES = 300
QUEUE_FRAMES = 10
FINALIZATION_SECONDS = 20.0


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
CaptureCallback = Callable[[UUID, ProviderCallCapture], Awaitable[None]]


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
        on_capture: CaptureCallback | None = None,
        start_segment_no: int = 0,
        initial_text: str = "",
        initial_sample_count: int = 0,
        initial_first_interim_latency_ms: int | None = None,
        callback_timeout_seconds: float = 10.0,
        max_audio_duration_ms: int = 180000,
        automatic_retry_limit: int = 1,
        automatic_retries_used: int = 0,
        defer_registration: bool = False,
        input_clock: Callable[[], float] = monotonic,
    ) -> None:
        if callback_timeout_seconds <= 0 or max_audio_duration_ms <= 0:
            raise ValueError("ASR budgets must be positive")
        if automatic_retry_limit not in (0, 1):
            raise ValueError("One automatic ASR replay at most")
        self.speech_id = speech_id
        self._connection = connection
        self._on_interim, self._on_segment = on_interim, on_segment
        self._on_segment_started, self._on_segment_failed = on_segment_started, on_segment_failed
        self._on_capture = on_capture
        self._history = bytearray()
        self._input_changed = asyncio.Event()
        self._closed = False
        self._sample_count = initial_sample_count
        self._initial_sample_count = initial_sample_count
        self._initial_text = initial_text
        self._confirmed_text = initial_text
        self._confirmed_local_samples = 0
        self._max_audio_duration_ms = max_audio_duration_ms
        # The authority supplies remaining input time; prior checkpoint samples
        # affect the result duration, never this new consumer's storage allowance.
        # A fractional remaining budget may end inside a 100ms receiver block.
        # Align storage only; the committed Core deadline still gates input.
        self._max_bytes = (
            (max_audio_duration_ms * 32 + PCM_FRAME_BYTES - 1) // PCM_FRAME_BYTES * PCM_FRAME_BYTES
        )
        self._automatic_retry_limit = automatic_retry_limit
        self._automatic_retries_used = automatic_retries_used
        self._initial_first_interim_latency_ms = initial_first_interim_latency_ms
        self._callback_timeout_seconds = callback_timeout_seconds
        self._ready = asyncio.Event()
        self._registration_admitted = asyncio.Event()
        if not defer_registration:
            self._registration_admitted.set()
        self._input_clock = input_clock
        self._input_deadline_mono: float | None = None
        self._input_sealed = asyncio.Event()
        self._current_segment_no = start_segment_no
        self._completed_results: list[SegmentResult] = []
        self._finalization_deadline: float | None = None
        self._active_task_id: UUID | None = None
        self._provider_tasks: set[asyncio.Task[SegmentResult]] = set()

    async def wait_ready(self, timeout_seconds: float = 10.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=timeout_seconds)

    @property
    def input_admitted(self) -> bool:
        deadline = self._input_deadline_mono
        if deadline is not None and self._input_clock() >= deadline:
            self.seal_input()
        return self._registration_admitted.is_set() and not self._input_sealed.is_set()

    @property
    def input_deadline_mono(self) -> float | None:
        return self._input_deadline_mono

    def install_input_deadline(self, deadline_mono: float) -> None:
        if not math.isfinite(deadline_mono):
            raise ValueError("ASR input deadline must be finite")
        if self._input_deadline_mono not in (None, deadline_mono):
            raise FunAsrError("asr_callback_identity_mismatch")
        self._input_deadline_mono = deadline_mono
        self._cap_finalization_deadline()

    def _cap_finalization_deadline(self) -> None:
        if self._input_deadline_mono is None:
            return
        # The production clock is Core's monotonic clock. Keep the conversion
        # explicit so controlled clocks never become provider/loop timestamps.
        deadline = monotonic() + self._input_deadline_mono - self._input_clock()
        deadline += FINALIZATION_SECONDS
        if self._finalization_deadline is None or deadline < self._finalization_deadline:
            self._finalization_deadline = deadline

    def seal_input(self) -> None:
        """Stop expired input without finishing a not-yet-committed transition."""

        self._cap_finalization_deadline()
        if not self._input_sealed.is_set():
            self._input_sealed.set()
            logger.info(
                "ASR input stopped at committed Core deadline",
                extra={"error_code": "asr_input_deadline", "speech_id": str(self.speech_id)},
            )

    async def wait_input_deadline(self) -> None:
        deadline = self._input_deadline_mono
        if deadline is None:
            raise FunAsrError("asr_input_deadline_missing")
        try:
            await asyncio.wait_for(
                self._input_sealed.wait(), timeout=max(0.0, deadline - self._input_clock())
            )
        except TimeoutError:
            self.seal_input()

    def admit_input(self) -> None:
        self._registration_admitted.set()

    @property
    def has_unconfirmed_audio(self) -> bool:
        return len(self._history) > self._confirmed_local_samples * 2

    @property
    def pending_provider_tasks(self) -> tuple[asyncio.Task[SegmentResult], ...]:
        return tuple(task for task in self._provider_tasks if not task.done())

    @property
    def input_closed(self) -> bool:
        return self._closed

    @property
    def finalization_deadline(self) -> float | None:
        return self._finalization_deadline

    @property
    def confirmed_sample_count(self) -> int:
        return self._initial_sample_count + self._confirmed_local_samples

    @property
    def automatic_retries_used(self) -> int:
        return self._automatic_retries_used

    @property
    def initial_sample_count(self) -> int:
        return self._initial_sample_count

    @property
    def initial_text(self) -> str:
        return self._initial_text

    @property
    def max_audio_duration_ms(self) -> int:
        return self._max_audio_duration_ms

    def pcm_snapshot(self, *, unconfirmed_only: bool = False) -> bytes:
        offset = self._confirmed_local_samples * 2 if unconfirmed_only else 0
        return bytes(self._history[offset:])

    def discard_audio(self) -> None:
        self._history.clear()
        self._input_changed.set()

    def feed_pcm(self, pcm: bytes) -> bool:
        if self._closed or not self.input_admitted:
            return False
        if len(pcm) % 2:
            raise FunAsrError("asr_pcm_invalid")
        if len(self._history) + len(pcm) > self._max_bytes:
            raise AsrQueueFull("asr_pcm_queue_full")
        self._history.extend(pcm)
        self._sample_count += len(pcm) // 2
        self._input_changed.set()
        return True

    async def finish(self, *, deadline_mono: float | None = None) -> None:
        self.close_input(deadline_mono=deadline_mono)

    def close_input(self, *, deadline_mono: float | None = None) -> None:
        self._cap_finalization_deadline()
        deadline = monotonic() + FINALIZATION_SECONDS if deadline_mono is None else deadline_mono
        if self._finalization_deadline is None or deadline < self._finalization_deadline:
            self._finalization_deadline = deadline
        self._closed = True
        self._input_changed.set()

    def _remaining_callback_timeout(self) -> float:
        if self._finalization_deadline is None:
            return self._callback_timeout_seconds
        return max(
            0, min(self._callback_timeout_seconds, self._finalization_deadline - monotonic())
        )

    async def run(self) -> AsrSpeechResult:
        if self._closed and not self._history:
            raise FunAsrError("asr_empty_audio")
        retries = 0
        while True:
            self._current_segment_no += 1
            segment_no, task_id = self._current_segment_no, uuid4()
            offset = self._confirmed_local_samples * 2
            prefix = self._confirmed_text
            try:
                result, sent_samples = await self._recognize_attempt(
                    segment_no, task_id, offset, prefix
                )
            except BaseException as error:
                capture = self._connection.take_capture(task_id)
                await self._emit_capture(task_id, capture)
                if isinstance(error, asyncio.CancelledError):
                    raise
                if self._on_segment_failed is not None:
                    await self._invoke_callback(
                        self._on_segment_failed,
                        self.speech_id,
                        segment_no,
                        task_id,
                        str(getattr(error, "code", "asr_stream_failed")),
                    )
                retryable = isinstance(error, FunAsrError) and error.code in {
                    "asr_start_timeout",
                    "asr_connection_failed",
                    "asr_stream_failed",
                    "asr_send_failed",
                    "asr_audio_timeout",
                    "asr_final_timeout",
                    "asr_task_failed",
                }
                if (
                    retryable
                    and retries < self._automatic_retry_limit
                    and (
                        self._finalization_deadline is None
                        or monotonic() < self._finalization_deadline
                    )
                ):
                    retries += 1
                    self._automatic_retries_used += 1
                    continue
                raise
            capture = self._connection.take_capture(task_id)
            await self._emit_capture(task_id, capture)
            full_text = prefix + result.final_text
            segment = replace(result, final_text=full_text[len(self._initial_text) :])
            self._completed_results.append(segment)
            self._confirmed_text = full_text
            self._confirmed_local_samples = len(self._history) // 2
            await self._invoke_callback(
                self._on_segment, self.speech_id, segment_no, segment, sent_samples
            )
            return self.checkpoint()

    def _require_attempt(self, task_id: UUID) -> None:
        if self._active_task_id != task_id:
            raise asyncio.CancelledError

    async def _recognize_attempt(
        self, segment_no: int, task_id: UUID, offset: int, prefix: str
    ) -> tuple[SegmentResult, int]:
        self._active_task_id = task_id
        stable: dict[int, str] = {}
        registration = asyncio.Event()
        provider_ready = asyncio.Event()
        sent_samples = 0

        async def on_sentence(sentence: FunAsrSentence) -> None:
            await registration.wait()
            self._require_attempt(task_id)
            if sentence.sentence_end:
                stable[sentence.sentence_id] = sentence.text
                end = sentence.end_time_ms * 16
                if 0 < end <= sent_samples:
                    self._confirmed_text = prefix + "".join(stable[k] for k in sorted(stable))
                    self._confirmed_local_samples = offset // 2 + end
            current = "".join(stable[k] for k in sorted(stable) if k < sentence.sentence_id)
            await self._on_interim(self.speech_id, segment_no, prefix + current + sentence.text)

        async def chunks():
            nonlocal sent_samples
            await registration.wait()
            self._require_attempt(task_id)
            cursor = offset
            while True:
                self._input_changed.clear()
                available = len(self._history) - cursor
                if available >= PCM_FRAME_BYTES or (available and self._closed):
                    count = min(PCM_FRAME_BYTES, available)
                    frame = bytes(self._history[cursor : cursor + count])
                    cursor += count
                    sent_samples += count // 2
                    self._require_attempt(task_id)
                    yield frame + b"\x00" * (PCM_FRAME_BYTES - count)
                elif self._closed:
                    return
                else:
                    await self._input_changed.wait()

        async def recognize() -> SegmentResult:
            return await self._connection.recognize_segment(
                chunks(),
                on_sentence=on_sentence,
                on_started=provider_ready.set,
                task_id=task_id,
                task_timeout_seconds=self._max_audio_duration_ms / 1000 + FINALIZATION_SECONDS + 3,
                final_deadline=lambda: self._finalization_deadline,
            )

        operation = asyncio.create_task(recognize(), name=f"asr-session-task-{task_id}")
        self._provider_tasks.add(operation)
        operation.add_done_callback(self._provider_tasks.discard)
        ready_wait = asyncio.create_task(provider_ready.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, ready_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if operation in done:
                await operation
            self._require_attempt(task_id)
            self._ready.set()
            await self._registration_admitted.wait()
            self._require_attempt(task_id)
            if self._on_segment_started is not None:
                await self._invoke_callback(
                    self._on_segment_started, self.speech_id, segment_no, task_id
                )
            self._require_attempt(task_id)
            registration.set()
            result = await operation
            self._require_attempt(task_id)
            if self._sample_count == self._initial_sample_count:
                raise FunAsrError("asr_empty_audio")
            return result, sent_samples
        finally:
            if self._active_task_id == task_id:
                self._active_task_id = None
            if not operation.done():
                operation.cancel()
                operation.add_done_callback(_consume_result)
            ready_wait.cancel()
            await asyncio.gather(ready_wait, return_exceptions=True)

    async def _emit_capture(self, task_id: UUID, capture: ProviderCallCapture | None) -> None:
        if capture is None:
            return
        if self._on_capture is None:
            capture.close()
            return
        try:
            await asyncio.wait_for(
                self._on_capture(task_id, capture), timeout=self._remaining_callback_timeout()
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "ASR provider capture callback failed",
                extra={
                    "error_code": "asr_capture_persist_failed",
                    "exception_type": type(error).__name__,
                },
            )
        finally:
            capture.close()

    async def _invoke_callback(
        self, callback: Callable[..., Awaitable[object]], *args: object
    ) -> None:
        try:
            await asyncio.wait_for(callback(*args), timeout=self._remaining_callback_timeout())
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise FunAsrError("asr_callback_timeout") from error

    def checkpoint(self) -> AsrSpeechResult:
        return AsrSpeechResult(
            speech_id=self.speech_id,
            final_text=self._confirmed_text,
            segments=tuple(self._completed_results),
            audio_duration_ms=self._sample_count // 16,
            first_interim_latency_ms=self._initial_first_interim_latency_ms
            or next(
                (
                    item.first_interim_latency_ms
                    for item in self._completed_results
                    if item.first_interim_latency_ms
                ),
                None,
            ),
            last_segment_no=self._current_segment_no,
        )


def _consume_result(task: asyncio.Task[object]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


__all__ = ["AsrQueueFull", "AsrSpeechResult", "AsrSpeechSession"]
