from __future__ import annotations

import asyncio

import pytest

from jx_core.agent.llm import LlmCapacityLimiter, LlmProviderError


async def _wait_for_capacity_waiters(semaphore: asyncio.Semaphore, count: int = 1) -> None:
    async with asyncio.timeout(0.5):
        while len(semaphore._waiters or ()) < count:  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_model_wait_cancellation_returns_all_global_capacity() -> None:
    limiter = LlmCapacityLimiter(global_limit=4)
    holder = await limiter.acquire("busy", 1)
    waiters = [asyncio.create_task(limiter.acquire("busy", 1)) for _ in range(3)]
    recovered: list[tuple[asyncio.Semaphore, asyncio.Semaphore]] = []
    try:
        await _wait_for_capacity_waiters(holder[1], 3)
        for waiter in waiters:
            waiter.cancel()
        for waiter in waiters:
            with pytest.raises(asyncio.CancelledError):
                await waiter
        limiter.release(holder)
        holder = None

        # Public acquisition proves all four slots remain usable after cancel.
        for index in range(4):
            recovered.append(await limiter.acquire(f"recovered-{index}", 1, 0.05))
    finally:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        if holder is not None:
            limiter.release(holder)
        for lease in recovered:
            limiter.release(lease)


@pytest.mark.asyncio
async def test_global_wait_cancellation_does_not_steal_capacity() -> None:
    limiter = LlmCapacityLimiter(global_limit=1)
    holder = await limiter.acquire("holder", 1)
    waiter = asyncio.create_task(limiter.acquire("waiting", 1))
    try:
        # The waiter owns its model slot while blocked on the full global pool.
        await _wait_for_capacity_waiters(holder[0])
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        limiter.release(holder)
    recovered = await limiter.acquire("waiting", 1, 0.05)
    limiter.release(recovered)


@pytest.mark.asyncio
async def test_model_timeout_returns_global_slot_for_other_models() -> None:
    limiter = LlmCapacityLimiter(global_limit=2)
    holder = await limiter.acquire("busy", 1)
    other = None
    try:
        with pytest.raises(LlmProviderError, match="llm_capacity_full"):
            await limiter.acquire("busy", 1, 0.02)
        other = await limiter.acquire("other", 1, 0.05)
    finally:
        if other is not None:
            limiter.release(other)
        limiter.release(holder)


@pytest.mark.asyncio
async def test_busy_model_waiters_do_not_block_available_other_models() -> None:
    limiter = LlmCapacityLimiter(global_limit=3)
    holder = await limiter.acquire("busy", 1)
    waiters = [asyncio.create_task(limiter.acquire("busy", 1)) for _ in range(2)]
    other = None
    try:
        await _wait_for_capacity_waiters(holder[1], 2)
        other = await limiter.acquire("idle", 1, timeout_seconds=0.05)
        assert all(not waiter.done() for waiter in waiters)
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        if other is not None:
            limiter.release(other)
        limiter.release(holder)


@pytest.mark.asyncio
async def test_global_wait_timeout_returns_the_acquired_model_slot() -> None:
    limiter = LlmCapacityLimiter(global_limit=1)
    holder = await limiter.acquire("holder", 1)
    try:
        with pytest.raises(LlmProviderError, match="llm_capacity_full"):
            await limiter.acquire("waiting", 1, timeout_seconds=0.02)
    finally:
        limiter.release(holder)
    recovered = await limiter.acquire("waiting", 1, timeout_seconds=0.05)
    limiter.release(recovered)


@pytest.mark.asyncio
async def test_capacity_queue_uses_one_deadline_across_global_and_model_waits() -> None:
    limiter = LlmCapacityLimiter(global_limit=2)
    busy = await limiter.acquire("busy", 1)
    blocker = await limiter.acquire("blocker", 1)
    released = asyncio.Event()
    waiter = asyncio.create_task(limiter.acquire("busy", 1, timeout_seconds=0.3))
    replacement = None

    async def release_model_but_keep_global_full() -> None:
        nonlocal replacement
        await _wait_for_capacity_waiters(busy[1])
        await asyncio.sleep(0.2)
        limiter.release(busy)
        # Claim the released global slot before the woken model waiter resumes.
        replacement = await limiter.acquire("replacement", 1, timeout_seconds=0.05)
        released.set()

    releaser = asyncio.create_task(release_model_but_keep_global_full())
    try:
        await released.wait()
        await _wait_for_capacity_waiters(blocker[0])
        # 0.1 s remains in the original budget. A fresh 0.3 s global budget
        # would still be waiting when this observation deadline expires.
        with pytest.raises(LlmProviderError, match="llm_capacity_full"):
            await asyncio.wait_for(waiter, timeout=0.2)
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, releaser, return_exceptions=True)
        if replacement is not None:
            limiter.release(replacement)
        else:
            limiter.release(busy)
        limiter.release(blocker)
    recovered = await limiter.acquire("busy", 1, 0.05)
    limiter.release(recovered)


@pytest.mark.asyncio
async def test_registry_lock_wait_is_within_capacity_deadline() -> None:
    limiter = LlmCapacityLimiter(global_limit=1)
    async with limiter._lock:  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(LlmProviderError, match="llm_capacity_full"):
            await asyncio.wait_for(limiter.acquire("waiting", 1, 0.02), timeout=0.2)
    recovered = await limiter.acquire("waiting", 1, 0.05)
    limiter.release(recovered)


@pytest.mark.asyncio
async def test_model_release_racing_cancellation_preserves_both_limits() -> None:
    limiter = LlmCapacityLimiter(global_limit=2)
    for _ in range(10):
        holder = await limiter.acquire("busy", 1)
        waiter = asyncio.create_task(limiter.acquire("busy", 1))
        await _wait_for_capacity_waiters(holder[1])
        # Wake the model waiter and cancel it before its acquire returns.
        limiter.release(holder)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        recovered = await limiter.acquire("busy", 1, 0.05)
        other = None
        try:
            other = await limiter.acquire("other", 1, 0.05)
        finally:
            if other is not None:
                limiter.release(other)
            limiter.release(recovered)


@pytest.mark.asyncio
async def test_global_release_racing_cancellation_returns_model_and_global_slots() -> None:
    limiter = LlmCapacityLimiter(global_limit=1)
    for _ in range(10):
        holder = await limiter.acquire("holder", 1)
        waiter = asyncio.create_task(limiter.acquire("waiting", 1))
        await _wait_for_capacity_waiters(holder[0])
        limiter.release(holder)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        recovered = await limiter.acquire("waiting", 1, timeout_seconds=0.05)
        limiter.release(recovered)


@pytest.mark.asyncio
async def test_model_and_global_hard_limits_remain_independent() -> None:
    limiter = LlmCapacityLimiter(global_limit=3)
    model_leases = [await limiter.acquire("busy", 2) for _ in range(2)]
    other = None
    recovered = None
    try:
        # A free global slot cannot bypass the busy model's own limit of two.
        with pytest.raises(LlmProviderError, match="llm_capacity_full"):
            await limiter.acquire("busy", 2, timeout_seconds=0.02)
        other = await limiter.acquire("other", 1, timeout_seconds=0.05)
        # A free model slot cannot bypass the full global limit of three.
        with pytest.raises(LlmProviderError, match="llm_capacity_full"):
            await limiter.acquire("overflow", 1, timeout_seconds=0.02)
        limiter.release(other)
        other = None
        recovered = await limiter.acquire("overflow", 1, timeout_seconds=0.05)
    finally:
        if recovered is not None:
            limiter.release(recovered)
        if other is not None:
            limiter.release(other)
        for lease in model_leases:
            limiter.release(lease)
