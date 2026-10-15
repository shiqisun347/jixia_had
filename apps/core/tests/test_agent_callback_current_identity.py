"""Agent callbacks use the registered attempt, never wall-clock row ordering."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from jx_core.agent.llm import LlmProviderError
from jx_core.agent.runtime import AgentRun, AgentRuntime
from jx_core.config import Settings
from jx_core.runtime_identity import CallbackEnvelope

# These controls register only ownership metadata; no provider or media starts.
# pyright: reportPrivateUsage=false


@pytest.fixture
def registered_agent() -> tuple[AgentRuntime, AgentRun, CallbackEnvelope]:
    runtime = AgentRuntime(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://synthetic.invalid/unused"),
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )
    envelope = CallbackEnvelope(
        match_id=uuid4(),
        speech_id=None,
        attempt_no=2,
        generation_id=uuid4(),
        connection_epoch=None,
        context_version=7,
        opportunity_id=uuid4(),
        opportunity_generation=3,
    )
    run = AgentRun(
        match_id=envelope.match_id,
        action_key="7:1",
        agent_profile_id=uuid4(),
        duration_ms=30_000,
        generation_id=envelope.generation_id,
        callback_envelope=envelope,
    )
    runtime._runs[run.match_id] = run
    return runtime, run, envelope


@pytest.mark.parametrize("phase", ["preparing", "speaking", "finalizing"])
def test_registered_attempt_is_current_through_its_owned_phases(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], phase: str
) -> None:
    runtime, run, envelope = registered_agent
    if phase != "preparing":
        run.speech_id = uuid4()
        envelope = replace(envelope, speech_id=run.speech_id)
        run.callback_envelope = envelope
    run.finalizing = phase == "finalizing"
    assert runtime.agent_callback_is_current(
        envelope=replace(envelope),
        action_key=run.action_key,
        agent_profile_id=run.agent_profile_id,
    )
    assert runtime._runs == {run.match_id: run}
    assert run.callback_envelope is envelope
    assert not run.media_fenced


@pytest.mark.parametrize(
    "field,value",
    [
        ("match_id", uuid4()),
        ("speech_id", uuid4()),
        ("attempt_no", 1),
        ("generation_id", uuid4()),
        ("generation_id", None),
        ("connection_epoch", 1),
        ("context_version", 6),
        ("opportunity_id", uuid4()),
        ("opportunity_id", None),
        ("opportunity_generation", 2),
        ("opportunity_generation", None),
    ],
)
def test_changed_callback_identity_cannot_borrow_the_current_attempt(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope],
    field: str,
    value: object,
) -> None:
    runtime, run, envelope = registered_agent
    changed = replace(envelope, **{field: value})
    assert not runtime.agent_callback_is_current(
        envelope=changed,
        action_key=run.action_key,
        agent_profile_id=run.agent_profile_id,
    )
    assert runtime._runs[run.match_id] is run
    assert run.callback_envelope is envelope


@pytest.mark.parametrize(
    "retirement",
    ["removed", "fenced", "runtime_closed", "match_closed", "generation_changed", "speech_changed"],
)
def test_retired_or_reassigned_attempt_is_rejected_without_mutating_its_owner(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], retirement: str
) -> None:
    runtime, run, envelope = registered_agent
    if retirement == "removed":
        runtime._runs.pop(run.match_id)
    elif retirement == "fenced":
        run.media_fenced = True
    elif retirement == "runtime_closed":
        runtime._closed = True
    elif retirement == "match_closed":
        runtime._remember_closed_match_locked(run.match_id)
    elif retirement == "generation_changed":
        run.generation_id = uuid4()
    elif retirement == "speech_changed":
        run.speech_id = uuid4()
    before = dict(runtime._runs)
    assert not runtime.agent_callback_is_current(
        envelope=envelope, action_key=run.action_key, agent_profile_id=run.agent_profile_id
    )
    assert runtime._runs == before
    assert run.callback_envelope is envelope


@pytest.mark.parametrize("wrong_target", ["action", "agent"])
def test_current_generation_cannot_target_another_action_or_agent(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], wrong_target: str
) -> None:
    runtime, run, envelope = registered_agent
    assert not runtime.agent_callback_is_current(
        envelope=envelope,
        action_key="8:1" if wrong_target == "action" else run.action_key,
        agent_profile_id=uuid4() if wrong_target == "agent" else run.agent_profile_id,
    )


@pytest.mark.parametrize("replacement_attempt", [1, 2])
def test_resume_generation_wins_even_with_same_context_and_lower_or_equal_attempt(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], replacement_attempt: int
) -> None:
    runtime, old, old_envelope = registered_agent
    envelope = replace(old_envelope, generation_id=uuid4(), attempt_no=replacement_attempt)
    current = AgentRun(
        match_id=old.match_id,
        action_key=old.action_key,
        agent_profile_id=old.agent_profile_id,
        duration_ms=old.duration_ms,
        generation_id=envelope.generation_id,
        callback_envelope=envelope,
    )
    runtime._runs[old.match_id] = current
    assert not runtime.agent_callback_is_current(
        envelope=old_envelope,
        action_key=old.action_key,
        agent_profile_id=old.agent_profile_id,
    )
    assert runtime.agent_callback_is_current(
        envelope=envelope,
        action_key=current.action_key,
        agent_profile_id=current.agent_profile_id,
    )
    assert runtime._runs[old.match_id] is current
    assert current.callback_envelope is envelope
    assert not current.media_fenced


def test_generation_without_its_original_envelope_is_not_current(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope],
) -> None:
    runtime, run, envelope = registered_agent
    run.callback_envelope = None
    assert not runtime.agent_callback_is_current(
        envelope=envelope, action_key=run.action_key, agent_profile_id=run.agent_profile_id
    )


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "logical_call",
        "context",
        "opportunity",
        "opportunity_generation",
        "attempt",
        "generation",
        "speech",
        "fenced",
        "removed",
        "closed",
        "match_closed",
        "action",
        "agent",
        "connection_epoch",
    ],
)
def test_configuration_failure_uses_its_frozen_startup_and_unique_logical_call(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], change: str
) -> None:
    runtime, run, original = registered_agent
    envelope = replace(original, generation_id=None)
    run.generation_id = None
    run.callback_envelope = envelope
    logical_call_id, action_key, agent_id = (
        run.logical_call_id,
        run.action_key,
        run.agent_profile_id,
    )
    if change == "logical_call":
        logical_call_id = uuid4()
    elif change == "context":
        envelope = replace(envelope, context_version=8)
    elif change == "opportunity":
        envelope = replace(envelope, opportunity_id=uuid4())
    elif change == "opportunity_generation":
        envelope = replace(envelope, opportunity_generation=4)
    elif change == "attempt":
        envelope = replace(envelope, attempt_no=1)
    elif change == "generation":
        envelope = replace(envelope, generation_id=uuid4())
    elif change == "speech":
        envelope = replace(envelope, speech_id=uuid4())
    elif change == "fenced":
        run.media_fenced = True
    elif change == "removed":
        runtime._runs.pop(run.match_id)
    elif change == "closed":
        runtime._closed = True
    elif change == "match_closed":
        runtime._remember_closed_match_locked(run.match_id)
    elif change == "action":
        action_key = "8:1"
    elif change == "agent":
        agent_id = uuid4()
    elif change == "connection_epoch":
        envelope = replace(envelope, connection_epoch=1)
    assert runtime.agent_startup_callback_is_current(
        envelope=envelope,
        logical_call_id=logical_call_id,
        action_key=action_key,
        agent_profile_id=agent_id,
    ) is (change == "none")


def test_old_startup_cannot_borrow_a_same_context_same_attempt_replacement(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope],
) -> None:
    runtime, old, original = registered_agent
    envelope = replace(original, generation_id=None)
    old.generation_id = None
    old.callback_envelope = envelope
    current = AgentRun(
        match_id=old.match_id,
        action_key=old.action_key,
        agent_profile_id=old.agent_profile_id,
        duration_ms=old.duration_ms,
        callback_envelope=envelope,
    )
    runtime._runs[old.match_id] = current
    assert current.logical_call_id != old.logical_call_id
    for logical_call_id, expected in [
        (old.logical_call_id, False),
        (current.logical_call_id, True),
    ]:
        assert (
            runtime.agent_startup_callback_is_current(
                envelope=envelope,
                logical_call_id=logical_call_id,
                action_key=current.action_key,
                agent_profile_id=current.agent_profile_id,
            )
            is expected
        )
    assert runtime._runs[old.match_id] is current
    assert not current.media_fenced


@pytest.mark.asyncio
@pytest.mark.parametrize("first_attempt_has_generation", [False, True])
async def test_actual_retry_reports_configuration_failure_with_current_startup_identity(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope],
    monkeypatch: pytest.MonkeyPatch,
    first_attempt_has_generation: bool,
) -> None:
    runtime, old, original = registered_agent
    runtime._runs.pop(old.match_id)
    config = SimpleNamespace(
        context_version=original.context_version,
        opportunity_id=original.opportunity_id,
        opportunity_generation=original.opportunity_generation,
        chars_per_second=4.0,
        voice="synthetic-unused",
        rate=1.0,
        playback_gain=1.0,
    )
    load_config = AsyncMock(
        side_effect=[config, LlmProviderError("model_profile_unavailable")]
        if first_attempt_has_generation
        else LlmProviderError("model_profile_unavailable")
    )
    generation_id = uuid4()
    create_generation = AsyncMock(return_value=generation_id)
    mark_failed = AsyncMock()
    execute = AsyncMock(side_effect=LlmProviderError("llm_first_token_timeout"))
    monkeypatch.setattr(runtime, "_load_config", load_config)
    monkeypatch.setattr(runtime, "_create_generation", create_generation)
    monkeypatch.setattr(runtime, "_mark_generation_failed", mark_failed)
    monkeypatch.setattr(runtime, "_execute", execute)
    monkeypatch.setattr("jx_core.agent.runtime.AGENT_PROVIDER_RETRY_BACKOFF_SECONDS", 0)
    observed: list[tuple[CallbackEnvelope, object, bool]] = []

    async def failure(
        *, envelope: CallbackEnvelope, error_code: str, logical_call_id: object
    ) -> None:
        assert error_code == "model_profile_unavailable"
        run = runtime._runs[old.match_id]
        assert logical_call_id == run.logical_call_id
        current = runtime.agent_startup_callback_is_current(
            envelope=envelope,
            logical_call_id=run.logical_call_id,
            action_key=run.action_key,
            agent_profile_id=run.agent_profile_id,
        )
        observed.append((envelope, logical_call_id, current))

    cast(AsyncMock, runtime._callbacks.handle_agent_failure).side_effect = failure
    await runtime.start_agent(
        match_id=old.match_id,
        action_key=old.action_key,
        agent_profile_id=old.agent_profile_id,
        duration_ms=old.duration_ms,
        context_version=original.context_version,
        opportunity_id=original.opportunity_id,
        opportunity_generation=original.opportunity_generation,
    )
    run = runtime._runs[old.match_id]
    assert run.task is not None
    await run.task
    assert load_config.await_count == 2
    assert len(observed) == 1
    envelope, _, current = observed[0]
    assert current
    assert envelope.generation_id is None and envelope.speech_id is None
    assert envelope.attempt_no == 2 and envelope.context_version == 7
    assert envelope.opportunity_id == original.opportunity_id
    assert envelope.opportunity_generation == original.opportunity_generation
    assert old.match_id not in runtime._runs
    assert mark_failed.await_args is not None and mark_failed.await_args.args == (
        None,
        "model_profile_unavailable",
    )
    if first_attempt_has_generation:
        create_generation.assert_awaited_once()
        execute.assert_awaited_once()
        assert mark_failed.await_args_list[0].args == (generation_id, "llm_first_token_timeout")
    else:
        create_generation.assert_not_awaited()
        execute.assert_not_awaited()


@pytest.mark.parametrize("first_pcm_bound", [False, True])
def test_text_completion_keeps_original_envelope_across_its_first_pcm_binding(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], first_pcm_bound: bool
) -> None:
    runtime, run, original = registered_agent
    if first_pcm_bound:
        run.speech_id = uuid4()
        run.callback_envelope = replace(original, speech_id=run.speech_id)
    assert runtime.agent_text_callback_is_current(
        envelope=original, action_key=run.action_key, agent_profile_id=run.agent_profile_id
    )
    assert original.speech_id is None
    assert run.callback_envelope is not None
    assert runtime.agent_text_callback_is_current(
        envelope=run.callback_envelope,
        action_key=run.action_key,
        agent_profile_id=run.agent_profile_id,
    )
    if first_pcm_bound:
        assert not runtime.agent_callback_is_current(
            envelope=original,
            action_key=run.action_key,
            agent_profile_id=run.agent_profile_id,
        ), "a failure callback does not inherit the text-only pre-speech allowance"


@pytest.mark.parametrize(
    "change",
    [
        "speech",
        "attempt",
        "generation",
        "context",
        "opportunity",
        "opportunity_generation",
        "connection_epoch",
        "fenced",
        "replaced",
        "closed",
    ],
)
def test_pending_text_cannot_borrow_another_attempt_or_speech_binding(
    registered_agent: tuple[AgentRuntime, AgentRun, CallbackEnvelope], change: str
) -> None:
    runtime, run, original = registered_agent
    run.speech_id = uuid4()
    run.callback_envelope = replace(original, speech_id=run.speech_id)
    envelope = original
    if change == "speech":
        envelope = replace(original, speech_id=uuid4())
    elif change == "attempt":
        envelope = replace(original, attempt_no=1)
    elif change == "generation":
        envelope = replace(original, generation_id=uuid4())
    elif change == "context":
        envelope = replace(original, context_version=8)
    elif change == "opportunity":
        envelope = replace(original, opportunity_id=uuid4())
    elif change == "opportunity_generation":
        envelope = replace(original, opportunity_generation=4)
    elif change == "connection_epoch":
        envelope = replace(original, connection_epoch=1)
    elif change == "fenced":
        run.media_fenced = True
    elif change == "replaced":
        new_envelope = replace(run.callback_envelope, generation_id=uuid4())
        runtime._runs[run.match_id] = AgentRun(
            match_id=run.match_id,
            action_key=run.action_key,
            agent_profile_id=run.agent_profile_id,
            duration_ms=run.duration_ms,
            generation_id=new_envelope.generation_id,
            speech_id=new_envelope.speech_id,
            callback_envelope=new_envelope,
        )
    elif change == "closed":
        runtime._closed = True
    assert not runtime.agent_text_callback_is_current(
        envelope=envelope, action_key=run.action_key, agent_profile_id=run.agent_profile_id
    )
    assert original.speech_id is None
