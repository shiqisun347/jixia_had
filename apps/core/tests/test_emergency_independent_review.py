"""Independent Spec 304 review controls; no database, provider or media."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

import jx_core.matches.emergency as emergency_module
from jx_core.agent.llm import LlmProviderError, LlmStreamResult
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.matches.domain import MatchDomainError
from jx_core.models import ExternalCall, Match, ModelProfile, Room
from test_admin_emergency_coordinator import fixture, settled


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [True, False])
async def test_same_operation_cannot_bind_a_different_http_target(pending: bool) -> None:
    actor, _, coordinator, payload = fixture()
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(*_: Any) -> str:
        entered.set()
        await release.wait()
        return "仅用于本次管理员占位的受控正文。"

    coordinator._generate = generate
    await actor.start()
    try:
        await coordinator.start_skip(actor.state.match_id, actor_user_id=uuid4(), payload=payload)
        await entered.wait()
        if not pending:
            release.set()
            await settled(coordinator)
        with pytest.raises(MatchDomainError, match="match_state_conflict"):
            await coordinator.start_skip(
                actor.state.match_id,
                actor_user_id=uuid4(),
                payload={**payload, "expected_action_key": "2:1"},
            )
    finally:
        release.set()
        await coordinator.close()
        await actor.close()


@pytest.mark.asyncio
async def test_cancel_does_not_wait_for_an_unavailable_diagnostic_finalizer() -> None:
    actor, _, coordinator, payload = fixture()
    entered, finalizer_entered, release = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def generate(*_: Any) -> str:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            # Represents the actual _generate finally block opening a session
            # and waiting to persist ExternalCall while the DB is unavailable.
            finalizer_entered.set()
            await release.wait()
        return "不应提交。"

    coordinator._generate = generate
    await actor.start()
    cancel_task: asyncio.Task[None] | None = None
    try:
        await coordinator.start_skip(actor.state.match_id, actor_user_id=uuid4(), payload=payload)
        await entered.wait()
        cancel_task = asyncio.create_task(coordinator.cancel(actor.state.match_id))
        await finalizer_entered.wait()
        # A deliberately loose local upper bound permits the existing 3 s
        # shutdown budget and a separate 2 s operation failure commit budget.
        done, _ = await asyncio.wait({cancel_task}, timeout=5.5)
        assert done, "cancel waits without a bound on diagnostic cleanup"
        assert (actor.state.emergency_operation or {}).get("status") != "PENDING"
    finally:
        release.set()
        if cancel_task is not None:
            await asyncio.gather(cancel_task, return_exceptions=True)
        await coordinator.close()
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["initial", "terminal", "cancel", "held_terminal"])
async def test_db_diagnostic_failure_releases_provider_capture_reservation(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    actor, _, coordinator, _ = fixture()
    agent_id, model_id = uuid4(), uuid4()
    released: list[bool] = []
    release_diagnostic = asyncio.Event()
    capture = ProviderCallCapture(
        reservation=cast(Any, SimpleNamespace(release=lambda: released.append(True)))
    )
    coordinator._capture_writer = cast(Any, SimpleNamespace(new_capture=lambda: capture))
    coordinator._settings = cast(
        Any,
        SimpleNamespace(llm_key_encryption_key=SimpleNamespace(get_secret_value=lambda: "fake")),
    )
    match = SimpleNamespace(room_id=uuid4(), context_version=1)
    room = SimpleNamespace(
        format_snapshot={
            "agents": [
                {
                    "id": str(agent_id),
                    "model": {
                        "id": str(model_id),
                        "base_url": "https://synthetic.invalid",
                        "model_id": "synthetic-model",
                    },
                }
            ]
        },
        topic_snapshot={"title": "受控主题", "affirmative_text": "受控正方立场"},
    )
    model = SimpleNamespace(
        id=model_id,
        api_key_ciphertext=b"fake",
        api_key_nonce=b"fake",
        base_url="https://synthetic.invalid",
        model_id="synthetic-model",
        max_concurrency=1,
    )

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        def begin(self) -> Session:
            return self

        async def get(self, kind: Any, _: Any) -> Any:
            if kind is ExternalCall:
                if boundary == "held_terminal":
                    try:
                        await release_diagnostic.wait()
                    except asyncio.CancelledError:
                        await release_diagnostic.wait()
                    return SimpleNamespace()
                raise RuntimeError("synthetic terminal diagnostic failure")
            return {Match: match, Room: room, ModelProfile: model}[kind]

        async def scalars(self, _: Any) -> Any:
            return SimpleNamespace(all=lambda: [])

        def add(self, _: Any) -> None:
            if boundary == "initial":
                raise RuntimeError("synthetic initial diagnostic failure")

    async def stream_chat(**_: Any) -> LlmStreamResult:
        if boundary == "cancel":
            raise asyncio.CancelledError
        return LlmStreamResult("仅供控制测试的管理员占位。", 1, 2, 10)

    async def close() -> None:
        return None

    monkeypatch.setattr(emergency_module, "decrypt_secret", lambda *_: "fake")
    monkeypatch.setattr(
        emergency_module,
        "OpenAIStreamingClient",
        lambda **_: SimpleNamespace(stream_chat=stream_chat, close=close),
    )
    coordinator._session_factory = cast(Any, Session)
    operation = {
        "id": str(uuid4()),
        "context_version": 1,
        "agent_profile_id": str(agent_id),
        "side": "AFFIRMATIVE",
        "seat_no": 3,
        "action_key": "1:1",
    }
    try:
        if boundary == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await coordinator._generate(actor.state.match_id, operation)
        elif boundary == "held_terminal":
            started_at = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await coordinator._generate(actor.state.match_id, operation)
            assert asyncio.get_running_loop().time() - started_at < 2.5
        else:
            with pytest.raises(RuntimeError, match=f"synthetic {boundary}"):
                await coordinator._generate(actor.state.match_id, operation)
        assert released == [True], "failed diagnostic persistence leaks the capture reservation"
    finally:
        release_diagnostic.set()
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_missing_frozen_target_agent_cannot_select_a_different_agents_model() -> None:
    actor, _, coordinator, _ = fixture()
    coordinator._settings = cast(
        Any,
        SimpleNamespace(llm_key_encryption_key=SimpleNamespace(get_secret_value=lambda: "fake")),
    )

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, kind: Any, _: Any) -> Any:
            if kind is Match:
                return SimpleNamespace(room_id=uuid4(), context_version=1)
            if kind is Room:
                return SimpleNamespace(
                    format_snapshot={
                        "agents": [{"id": str(uuid4()), "model": {"id": str(uuid4())}}]
                    }
                )
            raise AssertionError("a different frozen Agent model was selected")

    coordinator._session_factory = cast(Any, Session)
    with pytest.raises(LlmProviderError, match="model_profile_unavailable"):
        await coordinator._generate(
            actor.state.match_id,
            {"id": str(uuid4()), "context_version": 1, "agent_profile_id": str(uuid4())},
        )


@pytest.mark.asyncio
async def test_begin_budget_retires_late_committed_operation_without_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, manager, coordinator, payload = fixture()
    manager.begin_gate = asyncio.Event()
    calls: list[bool] = []

    async def generate(*_: Any) -> str:
        calls.append(True)
        return "不应发起。"

    coordinator._generate = generate
    monkeypatch.setattr(emergency_module, "OPERATION_TIMEOUT_SECONDS", 0.03)
    await actor.start()
    try:
        with pytest.raises(MatchDomainError, match="placeholder_generation_failed"):
            await coordinator.start_skip(
                actor.state.match_id, actor_user_id=uuid4(), payload=payload
            )
        assert manager.begin_entered.is_set()
        assert coordinator._detached
        manager.begin_gate.set()
        async with asyncio.timeout(1):
            while coordinator._detached:
                await asyncio.sleep(0)
        assert not calls
        assert (actor.state.emergency_operation or {}).get("status") == "CANCELLED"
    finally:
        manager.begin_gate.set()
        await coordinator.close()
        await actor.close()


@pytest.mark.asyncio
async def test_transient_failure_commit_does_not_leave_pending_operation() -> None:
    actor, manager, coordinator, payload = fixture()
    original_submit = manager.submit
    failures: list[bool] = []

    async def submit(match_id: Any, command: Any) -> Any:
        if command.type == "speech.skip.fail" and not failures:
            failures.append(True)
            raise RuntimeError("synthetic transient diagnostic commit failure")
        return await original_submit(match_id, command)

    async def generate(*_: Any) -> str:
        raise LlmProviderError("llm_provider_failed")

    manager.submit = submit
    coordinator._generate = generate
    await actor.start()
    try:
        await coordinator.start_skip(actor.state.match_id, actor_user_id=uuid4(), payload=payload)
        await settled(coordinator)
        assert failures == [True]
        assert (actor.state.emergency_operation or {}).get("status") == "FAILED"
    finally:
        await coordinator.close()
        await actor.close()


def test_human_placeholder_uses_explicit_model_and_rejects_ambiguous_legacy_models() -> None:
    first, second = {"id": str(uuid4())}, {"id": str(uuid4())}
    agents = [{"id": str(uuid4()), "model": item} for item in (first, second)]
    selected, _ = emergency_module._select_frozen_model(
        {"agents": agents, "emergency_model": second}, {"agent_profile_id": None}
    )
    assert selected == second
    with pytest.raises(LlmProviderError, match="model_profile_unavailable"):
        emergency_module._select_frozen_model(
            {"agents": agents, "judge": {"model": first}}, {"agent_profile_id": None}
        )
    same_models = [{"id": str(uuid4()), "model": first} for _ in range(2)]
    selected, _ = emergency_module._select_frozen_model(
        {"agents": same_models}, {"agent_profile_id": None}
    )
    assert selected == first
    for invalid in ({"model": {}}, {"model": {"id": None}}, "invalid-record"):
        with pytest.raises(LlmProviderError, match="model_profile_unavailable"):
            emergency_module._select_frozen_model(
                {"agents": [*same_models, invalid]}, {"agent_profile_id": None}
            )


def test_agent_placeholder_preserves_own_frozen_model_and_parameter_overrides() -> None:
    agent_id = str(uuid4())
    own_model = {"id": str(uuid4()), "generation_params": {"temperature": 0.75, "top_p": 0.9}}
    selected, params = emergency_module._select_frozen_model(
        {
            "agents": [
                {
                    "id": agent_id,
                    "model": own_model,
                    "generation_params": {"temperature": 0.4},
                }
            ],
            "emergency_model": {"id": str(uuid4())},
        },
        {"agent_profile_id": agent_id},
    )
    assert selected == own_model
    assert params == {"temperature": 0.4, "top_p": 0.9}
