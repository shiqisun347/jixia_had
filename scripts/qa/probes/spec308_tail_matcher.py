"""Deterministic nonstationary tail codes and lossy-PCM alignment; no I/O/network."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
from numpy.typing import NDArray

PCM = NDArray[np.int16]
RATE = 48_000
FRAME = 960
MARKER_FRAMES = 25


def encoded_source(seed: str) -> tuple[bytes, dict[str, Any]]:
    """Each 20ms symbol changes frequency/phase; PLC cannot extend the suffix code."""
    key = hashlib.sha256(seed.encode()).digest()
    rng = np.random.default_rng(int.from_bytes(key[:8], "big"))
    symbols: list[int] = []
    blocks: list[Any] = []
    previous = -1
    phase = 0.0
    for _ in range(MARKER_FRAMES):
        symbol = int(rng.integers(0, 15))
        if symbol == previous:
            symbol = (symbol + 1) % 15
        previous = symbol
        symbols.append(symbol)
        frequency = 650 + symbol * 125
        samples = np.arange(FRAME) / RATE
        signal = np.sin(phase + 2 * np.pi * frequency * samples)
        # Smooth edges without replacing the changing symbol with steady tone.
        ramp = np.minimum(np.arange(FRAME) / 48, np.arange(FRAME)[::-1] / 48)
        signal *= np.minimum(ramp, 1.0)
        blocks.append(np.rint(signal * 11000).astype("<i2"))
        phase = float((phase + 2 * np.pi * frequency * FRAME / RATE) % (2 * np.pi))
    marker = np.concatenate(blocks).astype("<i2")
    body = np.rint(np.sin(np.arange(RATE) * 2 * np.pi * 391 / RATE) * 6000).astype("<i2")
    source = np.concatenate([np.zeros(RATE // 5, dtype="<i2"), body, marker]).astype("<i2")
    return source.tobytes(), {
        "seed_sha256": hashlib.sha256(seed.encode()).hexdigest(),
        "symbols": symbols,
        "marker_samples": len(marker),
        "content_samples": len(source),
        "marker_start_sample": len(source) - len(marker),
    }


def ncc(left: Any, right: Any) -> float:
    if len(left) != len(right) or not len(left):
        return 0.0
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    a -= a.mean()
    b -= b.mean()
    scale = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / scale) if scale > 1 else 0.0


def locate(reference: Any, received: Any) -> tuple[int, float]:
    """FFT normalized valid cross-correlation, retaining the sample lag."""
    ref = np.asarray(reference, dtype=np.float64)
    data = np.asarray(received, dtype=np.float64)
    if len(data) < len(ref) or len(ref) < FRAME:
        return -1, 0.0
    ref = ref - ref.mean()
    size = 1 << (len(ref) + len(data) - 2).bit_length()
    full = np.fft.irfft(np.fft.rfft(data, size) * np.fft.rfft(ref[::-1], size), size)
    numerator = full[len(ref) - 1 : len(data)]
    sums = np.concatenate(([0.0], np.cumsum(data)))
    squares = np.concatenate(([0.0], np.cumsum(data * data)))
    energy = squares[len(ref) :] - squares[: -len(ref)]
    energy -= (sums[len(ref) :] - sums[: -len(ref)]) ** 2 / len(ref)
    denominator = np.sqrt(np.maximum(energy, 0) * np.dot(ref, ref))
    scores = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1)
    index = int(np.argmax(scores))
    return index, round(float(scores[index]), 6)


def compare_tail(source: bytes, received: bytes, *, synthetic: bool) -> dict[str, Any]:
    reference = np.frombuffer(source, dtype="<i2")
    data = np.frombuffer(received, dtype="<i2")
    endpoint = len(reference)
    if not synthetic:
        # Ignore provider silence, preserving the complete final audible frame.
        audible = [
            min(start + FRAME, len(reference))
            for start in range(0, len(reference), FRAME)
            if np.sqrt(
                np.mean(
                    reference[start : min(start + FRAME, len(reference))].astype(np.float64) ** 2
                )
            )
            >= 35
        ]
        if not audible:
            return {"passed": False, "reason": "source_has_no_audible_speech"}
        endpoint = audible[-1]
    length = MARKER_FRAMES * FRAME if synthetic else min(endpoint, RATE)
    target = reference[endpoint - length : endpoint]
    offset, correlation = locate(target, data)
    symbols = (
        [
            ncc(
                target[start : start + FRAME],
                data[offset + start : offset + min(start + FRAME, len(target))],
            )
            for start in range(max(0, len(target) - 6 * FRAME), len(target), FRAME)
        ]
        if offset >= 0
        else []
    )
    complete = offset >= 0 and offset + len(target) <= len(data)
    suffix = min(symbols, default=0.0)
    # Fixed before real runs. Raw artifacts permit independent review of every score.
    passed = complete and correlation >= 0.60 and suffix >= 0.45
    return {
        "passed": bool(passed),
        "tail_correlation": correlation,
        "last_six_frame_correlations": [round(v, 6) for v in symbols],
        "minimum_suffix_correlation": round(suffix, 6),
        "matched_tail_samples": len(target),
        "reference_audible_end_sample": endpoint,
        "source_samples": len(reference),
        "source_silent_tail_ignored_samples": len(reference) - endpoint,
        "received_match_start_sample": offset,
        "received_match_end_sample": offset + len(target) if offset >= 0 else None,
        "received_samples": len(data),
        "thresholds": {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45},
    }


def self_check() -> dict[str, Any]:
    source, _ = encoded_source("owned-marker-a")
    wrong, _ = encoded_source("owned-marker-b")
    samples = np.frombuffer(source, dtype="<i2")
    good = np.concatenate(
        [np.zeros(413, dtype="<i2"), samples, np.zeros(517, dtype="<i2")]
    ).tobytes()
    noisy = (
        (
            np.frombuffer(good, dtype="<i2").astype(np.float64) * 0.75
            + np.random.default_rng(42).normal(0, 100, len(good) // 2)
        )
        .astype("<i2")
        .tobytes()
    )
    cut = samples[: -8 * FRAME]
    plc = np.concatenate([cut, np.tile(cut[-FRAME:], 8)]).astype("<i2").tobytes()
    partial = np.rint(np.sin(np.arange(479) * 2 * np.pi * 1777 / RATE) * 15000).astype("<i2")
    partial_source = np.concatenate([samples, partial]).astype("<i2").tobytes()
    values = {
        "unaltered": compare_tail(source, good, synthetic=True),
        "gain_noise": compare_tail(source, noisy, synthetic=True),
        "wrong_seed": compare_tail(wrong, good, synthetic=True),
        "cut_last_160ms": compare_tail(source, cut.tobytes(), synthetic=True),
        "plc_repeated_last_20ms": compare_tail(source, plc, synthetic=True),
        "speech_partial_479_complete": compare_tail(
            partial_source, partial_source, synthetic=False
        ),
        "speech_partial_479_cut": compare_tail(partial_source, source, synthetic=False),
    }
    passed = (
        values["unaltered"]["passed"]
        and values["gain_noise"]["passed"]
        and values["speech_partial_479_complete"]["passed"]
        and values["speech_partial_479_complete"]["reference_audible_end_sample"]
        == len(partial_source) // 2
        and not any(
            values[key]["passed"]
            for key in (
                "wrong_seed",
                "cut_last_160ms",
                "plc_repeated_last_20ms",
                "speech_partial_479_cut",
            )
        )
    )
    return {
        "status": "passed" if passed else "failed",
        "scope": "synthetic matcher only; no RTC/provider",
        "cases": values,
    }
