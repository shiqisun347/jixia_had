"""Remaining wallclock budgets need a bounded whole PCM input block."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.session import AsrQueueFull, AsrSpeechSession


@pytest.mark.parametrize(
    ("budget_ms", "expected_blocks"),
    [(1, 1), (89, 1), (99, 1), (100, 1), (101, 2), (88889, 889), (120000, 1200)],
)
async def test_whole_block_capacity_does_not_change_authority_deadline_or_allow_unbounded_pcm(
    budget_ms: int, expected_blocks: int
) -> None:
    clock = [100.0]
    factory = AsyncMock()
    callback = AsyncMock()
    connection = FunAsrConnection(
        url="wss://synthetic.invalid/asr", api_key="synthetic", socket_factory=factory
    )
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=callback,
        on_segment=callback,
        max_audio_duration_ms=budget_ms,
        input_clock=lambda: clock[0],
    )
    deadline = clock[0] + budget_ms / 1000
    session.install_input_deadline(deadline)
    assert session.max_audio_duration_ms == budget_ms
    pcm = b"\x01\x00" * 1600
    clock[0] = deadline - 0.000001
    for _ in range(expected_blocks):
        assert session.feed_pcm(pcm)
    stored = session.pcm_snapshot()
    assert stored == pcm * expected_blocks
    assert session._max_bytes == expected_blocks * 3200
    assert 0 <= session._max_bytes - budget_ms * 32 < 3200
    assert session.input_deadline_mono == deadline and session.input_admitted
    with pytest.raises(AsrQueueFull, match="asr_pcm_queue_full"):
        session.feed_pcm(b"\x02\x00")
    assert session.pcm_snapshot() == stored
    clock[0] = deadline
    assert not session.feed_pcm(pcm)
    assert session.pcm_snapshot() == stored and not session.input_admitted
    final_deadline = session.finalization_deadline
    assert final_deadline is not None
    await session.finish(deadline_mono=final_deadline + 100)
    assert session.finalization_deadline == final_deadline
    assert session.input_deadline_mono == deadline
    assert not factory.called and not callback.called


def test_confirmed_prefix_does_not_consume_new_fractional_input_capacity() -> None:
    callback: Any = AsyncMock()
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=FunAsrConnection(url="wss://synthetic.invalid/asr", api_key="synthetic"),
        on_interim=callback,
        on_segment=callback,
        max_audio_duration_ms=101,
        initial_sample_count=30_700 * 16,
        initial_text="公开受控前缀",
    )
    assert session.feed_pcm(b"\x01\x00" * 3200)
    assert session.pcm_snapshot() == b"\x01\x00" * 3200
    assert session.initial_sample_count == 491200
    assert session._sample_count == 491200 + 3200
    assert session._initial_text == "公开受控前缀"
    with pytest.raises(AsrQueueFull):
        session.feed_pcm(b"\x01\x00")
