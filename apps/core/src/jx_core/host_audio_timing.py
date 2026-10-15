"""Shared bounded budgets for pre-generated host playback, including its retry."""

from __future__ import annotations

HOST_AUDIO_ATTEMPTS = 2
HOST_AUDIO_RETRY_BACKOFF_SECONDS = 0.05
HOST_AUDIO_ATTEMPT_CLEANUP_TIMEOUT_SECONDS = 3.0
HOST_AUDIO_DISPATCH_ALLOWANCE_SECONDS = 30.0


def host_audio_attempt_timeout_seconds(duration_ms: int | None) -> float:
    """Preserve the existing per-attempt timeout for complete asset playback."""
    return max(10.0, max(0, duration_ms or 0) / 1000 + 12.0)


def host_audio_guard_seconds(duration_ms: int | None) -> float:
    """Protect both complete attempts and their bounded cleanup without seeking."""
    return (
        HOST_AUDIO_ATTEMPTS
        * (
            host_audio_attempt_timeout_seconds(duration_ms)
            + HOST_AUDIO_ATTEMPT_CLEANUP_TIMEOUT_SECONDS
        )
        + HOST_AUDIO_RETRY_BACKOFF_SECONDS
        + HOST_AUDIO_DISPATCH_ALLOWANCE_SECONDS
    )
