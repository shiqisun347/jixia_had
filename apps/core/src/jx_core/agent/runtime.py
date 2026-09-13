"""Fixed-stage Agent LLM -> TTS -> PyAV -> LiveKit runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from livekit import api, rtc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..data_capture.content import CAPTURE_VERSION, store_content_blob
from ..data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from ..data_capture.provider_persistence import persist_provider_capture
from ..experiments.prompts import (
    EXPERIMENT_PROMPT_VERSION,
    render_experiment_decision_prompt,
    render_experiment_speech_prompt,
    render_history,
)
from ..models import (
    AgentAudioAsset,
    AgentFreeDebateDecision,
    AgentGeneration,
    AgentProfile,
    ExperimentMatchAttempt,
    ExternalCall,
    Match,
    MatchParticipant,
    ModelProfile,
    Room,
    Speech,
    VoiceProfile,
)
from ..runtime_identity import CallbackEnvelope
from ..security.crypto import decrypt_secret
from .audio import IncrementalOpusDecoder, apply_pcm16_gain
from .llm import (
    LLM_FAST_DECISION_TIMEOUT_SECONDS,
    LlmCapacityLimiter,
    LlmProviderError,
    OpenAIStreamingClient,
    iter_text_queue,
)
from .tts import QwenTtsConnection, TtsProviderError, TtsStreamResult

logger = logging.getLogger("jx-core.agent")
AGENT_TASK_CANCEL_TIMEOUT_SECONDS = 3.0
_PROMPT_VARIABLE_RE = re.compile(r"{{\s*([A-Z][A-Z0-9_]*)\s*}}")


def _snapshot_agent(
    snapshot: dict[str, Any] | None, agent_profile_id: UUID
) -> dict[str, Any] | None:
    if not snapshot:
        return None
    for item in cast(list[object], snapshot.get("agents", [])):
        if isinstance(item, dict):
            mapping = cast(dict[str, Any], item)
            if str(mapping.get("id")) == str(agent_profile_id):
                return mapping
    return None


def _snapshot_prompt(
    snapshot: dict[str, Any] | None,
    *,
    agent_profile_id: UUID,
    stage_key: str,
    purpose: str,
) -> dict[str, Any] | None:
    if not snapshot:
        return None
    candidates: list[dict[str, Any]] = []
    for item in cast(list[object], snapshot.get("prompts", [])):
        if not isinstance(item, dict):
            continue
        mapping = cast(dict[str, Any], item)
        if mapping.get("stage_key") == stage_key and mapping.get("purpose") == purpose:
            candidates.append(mapping)
    return next(
        (item for item in candidates if str(item.get("agent_profile_id")) == str(agent_profile_id)),
        next((item for item in candidates if item.get("agent_profile_id") is None), None),
    )


def _render_snapshot_prompt(template: str, variables: dict[str, str]) -> str:
    missing = sorted(set(_PROMPT_VARIABLE_RE.findall(template)) - set(variables))
    if missing:
        raise LlmProviderError("format_prompt_variable_missing")
    return _PROMPT_VARIABLE_RE.sub(lambda match: variables[match.group(1)], template)


async def _ignore_delta(_: str) -> None:
    return


async def _close_llm_client(client: OpenAIStreamingClient) -> None:
    try:
        await asyncio.wait_for(client.close(), timeout=AGENT_TASK_CANCEL_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "LLM client cleanup timed out",
            extra={"error_code": "llm_cleanup_timeout"},
        )


def _parse_decision(text: str) -> tuple[bool, float]:
    candidate = text.strip().removeprefix("```").removeprefix("json").removesuffix("```").strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise LlmProviderError("agent_decision_invalid") from error
    value = cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
    if not isinstance(value.get("should_speak"), bool):
        raise LlmProviderError("agent_decision_invalid")
    willingness_value = value.get("willingness")
    if not isinstance(willingness_value, (int, float)):
        raise LlmProviderError("agent_decision_invalid")
    should_speak = cast(bool, value["should_speak"])
    return should_speak, max(0.0, min(1.0, float(willingness_value)))


def _parse_experiment_decision(text: str) -> tuple[bool, str]:
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError as error:
        raise LlmProviderError("agent_decision_invalid") from error
    value = cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
    reason = value.get("decision_reason")
    if set(value) != {"should_speak", "decision_reason"} or not isinstance(
        value.get("should_speak"), bool
    ):
        raise LlmProviderError("agent_decision_invalid")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 20:
        raise LlmProviderError("agent_decision_invalid")
    return cast(bool, value["should_speak"]), reason.strip()


class AgentRuntimeCallbacks(Protocol):
    async def report_free_decision(
        self,
        *,
        envelope: CallbackEnvelope,
        action_key: str,
        agent_profile_id: UUID,
        side: str,
        decision_round_id: UUID,
        should_speak: bool | None,
        willingness: float | None,
        failed: bool,
        attempt_no: int,
        duration_ms: int,
        error_code: str | None,
        decision_reason: str | None = None,
    ) -> object: ...

    async def publish_agent_text_delta(self, *, envelope: CallbackEnvelope, text: str) -> None: ...

    async def agent_text_generated(self, *, envelope: CallbackEnvelope, text: str) -> None: ...

    async def publish_agent_subtitle(
        self, *, envelope: CallbackEnvelope, text: str, played_ms: int
    ) -> None: ...

    async def publish_agent_retry(self, *, envelope: CallbackEnvelope, error_code: str) -> None: ...

    async def start_agent_playback(
        self,
        *,
        envelope: CallbackEnvelope,
        agent_profile_id: UUID,
        audio_storage_path: str,
    ) -> object: ...

    async def finish_agent_playback(self, *, envelope: CallbackEnvelope) -> object: ...

    async def finalize_agent_speech(
        self,
        *,
        envelope: CallbackEnvelope,
        final_text: str,
        llm_draft_text: str,
        audio_storage_path: str,
        audio_duration_ms: int,
        audio_truncated: bool,
    ) -> object: ...

    async def handle_agent_failure(
        self, *, envelope: CallbackEnvelope, error_code: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentConfig:
    match_id: UUID
    action_key: str
    agent_profile_id: UUID
    context_version: int
    match_seed: int
    model_key: str
    base_url: str
    model_id: str
    api_key: str
    model_limit: int
    generation_params: dict[str, Any]
    max_tokens: int
    messages: list[dict[str, str]]
    voice: str
    rate: float
    chars_per_second: float
    playback_gain: float
    experiment_mode: bool = False
    formal_4v4: bool = False
    decision_messages: list[dict[str, str]] | None = None
    prompt_version: str | None = None
    opportunity_id: UUID | None = None
    opportunity_generation: int | None = None


@dataclass(slots=True)
class AgentRun:
    match_id: UUID
    action_key: str
    agent_profile_id: UUID
    duration_ms: int
    side: str | None = None
    task: asyncio.Task[None] | None = None
    generation_id: UUID | None = None
    speech_id: UUID | None = None
    draft_parts: list[str] = field(default_factory=lambda: list[str]())
    spool_path: Path | None = None
    storage_path: Path | None = None
    byte_count: int = 0
    played_samples: int = 0
    chars_per_second: float = 4.0
    voice: str = ""
    rate: float = 1.0
    playback_gain: float = 1.0
    tts_task_id: UUID | None = None
    tts_call_id: UUID | None = None
    first_audio_latency_ms: int | None = None
    tts_completed_latency_ms: int | None = None
    natural_complete: bool = False
    finalizing: bool = False
    source: rtc.AudioSource | None = None
    room: rtc.Room | None = None
    decoder: IncrementalOpusDecoder | None = None
    text_queue: asyncio.Queue[str | None] | None = None
    tts_task: asyncio.Task[TtsStreamResult] | None = None
    logical_call_id: UUID = field(default_factory=uuid4)
    tts_logical_call_id: UUID = field(default_factory=uuid4)
    llm_capture: ProviderCallCapture | None = None
    tts_capture: ProviderCallCapture | None = None
    media_fenced: bool = False
    callback_envelope: CallbackEnvelope | None = None

    @property
    def draft(self) -> str:
        return "".join(self.draft_parts)


class AgentRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        callbacks: AgentRuntimeCallbacks,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._callbacks = callbacks
        self._limiter = LlmCapacityLimiter(settings.llm_global_concurrency)
        self._runs: dict[UUID, AgentRun] = {}
        self._decision_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._tts_connections: dict[UUID, QwenTtsConnection] = {}
        self._lock = asyncio.Lock()
        self._media_lock = asyncio.Lock()

    @property
    def capacity_limiter(self) -> LlmCapacityLimiter:
        return self._limiter

    @staticmethod
    def _run_envelope(run: AgentRun, *, attempt_no: int | None = None) -> CallbackEnvelope:
        if run.callback_envelope is not None:
            return replace(
                run.callback_envelope,
                attempt_no=attempt_no or run.callback_envelope.attempt_no,
            )
        return CallbackEnvelope(
            match_id=run.match_id,
            speech_id=run.speech_id,
            attempt_no=attempt_no or 1,
            generation_id=run.generation_id,
            connection_epoch=None,
            context_version=0,
            opportunity_id=None,
            opportunity_generation=None,
        )

    async def start_agent(
        self,
        *,
        match_id: UUID,
        action_key: str,
        agent_profile_id: UUID,
        duration_ms: int,
        side: str | None = None,
    ) -> None:
        async with self._lock:
            current = self._runs.get(match_id)
            if current is not None and current.action_key == action_key:
                # A failed/completed task can remain visible briefly while its
                # callback is being handled. It must not block recovery of the
                # same action.
                if current.task is not None and current.task.done():
                    self._runs.pop(match_id, None)
                else:
                    return
            if current is not None:
                await self._cancel_run(current)
            run = AgentRun(
                match_id=match_id,
                action_key=action_key,
                agent_profile_id=agent_profile_id,
                duration_ms=duration_ms,
                side=side,
            )
            self._runs[match_id] = run
            run.task = asyncio.create_task(
                self._run_with_retry(run), name=f"agent-run-{match_id}-{action_key}"
            )

    async def decide_free_debate(
        self,
        *,
        match_id: UUID,
        action_key: str,
        side: str,
        agent_profile_ids: list[UUID],
        decision_round_id: UUID,
        envelope: CallbackEnvelope,
    ) -> None:
        """Run and report each free-debate decision independently."""

        current_task = asyncio.current_task()
        if current_task is None:
            return
        previous = self._decision_tasks.get(match_id)
        if previous is not None and previous is not current_task:
            await self._cancel_agent_task(previous)
        self._decision_tasks[match_id] = current_task

        async def decide(agent_profile_id: UUID) -> None:
            started = asyncio.get_running_loop().time()
            run = AgentRun(
                match_id=match_id,
                action_key=action_key,
                agent_profile_id=agent_profile_id,
                duration_ms=0,
                side=side,
            )
            last_error: Exception | None = None
            logical_call_id = uuid4()
            for attempt in (1, 2):
                leases: tuple[asyncio.Semaphore, asyncio.Semaphore] | None = None
                client: OpenAIStreamingClient | None = None
                value: tuple[bool, float | None, str | None] | None = None
                external_call_id: UUID | None = None
                raw_response: str | None = None
                provider_capture = ProviderCallCapture()
                callback_envelope = replace(envelope, match_id=match_id, attempt_no=attempt)
                try:
                    config = await self._load_config(run)
                    callback_envelope = replace(
                        envelope,
                        match_id=match_id,
                        attempt_no=attempt,
                        context_version=config.context_version,
                    )
                    boolean_decision = config.experiment_mode or config.formal_4v4
                    decision_messages = list(config.decision_messages or config.messages)
                    if boolean_decision:
                        decision_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "这是自由辩论快速决策。只输出 JSON，不要 Markdown："
                                    '{"should_speak":true或false,"decision_reason":"20字以内理由"}。'
                                    "decision_reason 必须填写且不超过 20 个字。"
                                    "结合当前辩论上下文判断是否值得发言。"
                                ),
                            }
                        )
                    elif not config.decision_messages:
                        decision_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "这是自由辩论快速决策。"
                                    "请结合当前辩论上下文判断是否值得发言。"
                                ),
                            }
                        )
                    leases = await self._limiter.acquire(
                        config.model_key, config.model_limit, timeout_seconds=3.0
                    )
                    try:
                        external_call_id = await self._start_decision_call(
                            config=config,
                            decision_round_id=decision_round_id,
                            envelope=callback_envelope,
                            messages=decision_messages,
                            attempt_no=attempt,
                            logical_call_id=logical_call_id,
                        )
                    except Exception:
                        logger.warning(
                            "decision call capture start failed",
                            extra={
                                "error_code": "decision_capture_failed",
                                "match_id": str(match_id),
                                "decision_round_id": str(decision_round_id),
                            },
                        )
                    client = OpenAIStreamingClient(
                        base_url=config.base_url,
                        api_key=config.api_key,
                        model=config.model_id,
                        connection_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS,
                        first_token_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS,
                        idle_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS,
                    )
                    result = await client.stream_chat(
                        messages=decision_messages,
                        max_tokens=32 if boolean_decision else 64,
                        generation_params={
                            **config.generation_params,
                            **(
                                {
                                    "temperature": 0.75,
                                    "top_p": 0.9,
                                    "enable_thinking": False,
                                }
                                if boolean_decision
                                else {"enable_thinking": False}
                            ),
                        },
                        on_delta=_ignore_delta,
                        capture=provider_capture,
                    )
                    raw_response = result.text
                    if boolean_decision:
                        parsed_decision = _parse_experiment_decision(result.text)
                        value = (parsed_decision[0], None, parsed_decision[1])
                    else:
                        parsed_decision = _parse_decision(result.text)
                        value = (*parsed_decision, None)
                    try:
                        await self._finish_external_call(
                            external_call_id,
                            response_payload={"text": result.text},
                            first_result_latency_ms=getattr(result, "first_token_latency_ms", None),
                            completed_latency_ms=getattr(result, "completed_latency_ms", None),
                            completion_tokens=getattr(result, "completion_tokens", None),
                            provider_capture=provider_capture,
                        )
                    except Exception:
                        logger.warning(
                            "decision call capture completion failed",
                            extra={
                                "error_code": "decision_capture_failed",
                                "match_id": str(match_id),
                                "decision_round_id": str(decision_round_id),
                            },
                        )
                except asyncio.CancelledError:
                    try:
                        await self._fail_external_call(
                            external_call_id,
                            "cancelled",
                            cancelled=True,
                            provider_capture=provider_capture,
                        )
                    except Exception:
                        logger.warning(
                            "decision cancellation capture failed",
                            extra={
                                "error_code": "decision_capture_failed",
                                "match_id": str(match_id),
                                "decision_round_id": str(decision_round_id),
                            },
                        )
                    raise
                except Exception as error:
                    last_error = error
                    try:
                        await self._fail_external_call(
                            external_call_id,
                            _error_code(error),
                            response_payload=(
                                {"text": raw_response} if raw_response is not None else None
                            ),
                            provider_capture=provider_capture,
                        )
                    except Exception:
                        logger.warning(
                            "decision call failure capture failed",
                            extra={
                                "error_code": "decision_capture_failed",
                                "match_id": str(match_id),
                                "decision_round_id": str(decision_round_id),
                            },
                        )
                finally:
                    if leases is not None:
                        self._limiter.release(leases)
                    if client is not None:
                        await _close_llm_client(client)
                if value is not None:
                    await self._callbacks.report_free_decision(
                        envelope=callback_envelope,
                        action_key=action_key,
                        agent_profile_id=agent_profile_id,
                        side=side,
                        decision_round_id=decision_round_id,
                        should_speak=value[0],
                        willingness=value[1],
                        decision_reason=value[2],
                        failed=False,
                        attempt_no=attempt,
                        duration_ms=max(
                            0,
                            int((asyncio.get_running_loop().time() - started) * 1000),
                        ),
                        error_code=None,
                    )
                    return
            assert last_error is not None
            await self._callbacks.report_free_decision(
                envelope=replace(envelope, match_id=match_id, attempt_no=2),
                action_key=action_key,
                agent_profile_id=agent_profile_id,
                side=side,
                decision_round_id=decision_round_id,
                should_speak=None,
                willingness=None,
                decision_reason=None,
                failed=True,
                attempt_no=2,
                duration_ms=max(
                    0,
                    int((asyncio.get_running_loop().time() - started) * 1000),
                ),
                error_code=_error_code(last_error),
            )

        try:
            await asyncio.gather(*(decide(item) for item in agent_profile_ids))
        finally:
            if self._decision_tasks.get(match_id) is current_task:
                self._decision_tasks.pop(match_id, None)

    async def finalize_agent(self, match_id: UUID, speech_id: UUID, reason: str) -> None:
        run = self._runs.get(match_id)
        if run is None or run.speech_id != speech_id or run.finalizing:
            return
        run.finalizing = True
        truncated = reason != "COMPLETED" or not run.natural_complete
        if truncated:
            if run.source is not None:
                run.source.clear_queue()
            if run.task is not None and not run.task.done():
                await self._cancel_agent_task(run.task)
            # A truncated pipeline may still own the reusable TTS connection
            # lock while it cleans up. Discard it before the next action so a
            # slow provider close cannot stall the whole match indefinitely.
            await self._discard_tts_connection(match_id)
        draft = run.draft
        played_ms = run.played_samples * 1000 // 48_000
        # The LLM draft is the authoritative Agent transcript. Playback may be
        # truncated by the stage timer, but TTS never rewrites formal context.
        final_text = draft
        if run.spool_path is None or run.storage_path is None or run.generation_id is None:
            await self._callbacks.handle_agent_failure(
                envelope=self._run_envelope(run), error_code="tts_audio_empty"
            )
            return
        if run.spool_path.exists():
            run.storage_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(run.spool_path, run.storage_path)
        try:
            async with asyncio.timeout(AGENT_FINALIZATION_TIMEOUT_SECONDS):
                await self._persist_final(run)
                if self._runs.get(match_id) is run:
                    self._runs.pop(match_id, None)
                callback_kwargs: dict[str, Any] = {
                    "envelope": self._run_envelope(run),
                    "final_text": final_text,
                    "llm_draft_text": draft,
                    "audio_storage_path": str(run.storage_path),
                    "audio_duration_ms": played_ms,
                    "audio_truncated": truncated,
                }
                for callback_attempt in range(2):
                    try:
                        await self._callbacks.finalize_agent_speech(**callback_kwargs)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if callback_attempt == 1:
                            raise
                        logger.warning(
                            "Agent finalization callback failed; retrying",
                            extra={
                                "error_code": "agent_finalization_callback_retry",
                                "match_id": str(match_id),
                                "speech_id": str(speech_id),
                                "generation_id": str(run.generation_id),
                            },
                        )
                        await asyncio.sleep(0.05)
        except TimeoutError:
            logger.error(
                "agent finalization timed out",
                extra={
                    "error_code": "agent_finalization_timeout",
                    "match_id": str(match_id),
                    "speech_id": str(speech_id),
                    "generation_id": str(run.generation_id),
                },
            )
            await self._callbacks.handle_agent_failure(
                envelope=self._run_envelope(run), error_code="agent_finalization_timeout"
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            original = getattr(error, "orig", None)
            diagnostic: dict[str, str] = {"exception_type": type(error).__name__}
            sqlstate = getattr(original, "sqlstate", None)
            if isinstance(sqlstate, str):
                diagnostic["sqlstate"] = sqlstate
            constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
            if isinstance(constraint, str) and constraint.isascii() and len(constraint) <= 128:
                diagnostic["constraint"] = constraint
            logger.exception(
                "agent finalization failed",
                extra={
                    "error_code": "agent_finalization_failed",
                    "match_id": str(match_id),
                    "speech_id": str(speech_id),
                    "generation_id": str(run.generation_id),
                    "details": diagnostic,
                },
            )
            await self._callbacks.handle_agent_failure(
                envelope=self._run_envelope(run), error_code="agent_finalization_failed"
            )
        finally:
            try:
                await asyncio.wait_for(self._close_run(run), timeout=3.0)
            except TimeoutError:
                logger.warning(
                    "agent media cleanup timed out",
                    extra={"error_code": "agent_cleanup_timeout"},
                )

    async def reset_agent(self, match_id: UUID) -> None:
        run = self._runs.pop(match_id, None)
        if run is not None:
            await self._cancel_run(run)

    async def fence_agent(self, match_id: UUID) -> None:
        """Synchronously invalidate media before an interruption is broadcast."""
        run = self._runs.get(match_id)
        if run is None:
            return
        async with self._media_lock:
            run.media_fenced = True
            if run.source is not None:
                run.source.clear_queue()

    async def cancel_free_decision(self, match_id: UUID) -> None:
        task = self._decision_tasks.pop(match_id, None)
        if task is not None and task is not asyncio.current_task():
            await self._cancel_agent_task(task)

    async def close_match(self, match_id: UUID) -> None:
        await self.cancel_free_decision(match_id)
        await self.reset_agent(match_id)
        connection = self._tts_connections.pop(match_id, None)
        if connection is not None:
            await connection.close()

    async def close(self) -> None:
        for match_id in tuple(self._decision_tasks):
            await self.cancel_free_decision(match_id)
        for match_id in tuple(self._runs):
            await self.reset_agent(match_id)
        for connection in tuple(self._tts_connections.values()):
            await connection.close()
        self._tts_connections.clear()

    async def _run_with_retry(self, run: AgentRun) -> None:
        try:
            for attempt in (1, 2):
                try:
                    config = await self._load_config(run)
                    run.callback_envelope = CallbackEnvelope(
                        match_id=run.match_id,
                        speech_id=None,
                        attempt_no=attempt,
                        generation_id=None,
                        connection_epoch=None,
                        context_version=config.context_version,
                        opportunity_id=config.opportunity_id,
                        opportunity_generation=config.opportunity_generation,
                    )
                    run.chars_per_second = config.chars_per_second
                    run.voice = config.voice
                    run.playback_gain = config.playback_gain
                    run.rate = config.rate
                    generation_id = await self._create_generation(config, attempt)
                    run.generation_id = generation_id
                    assert run.callback_envelope is not None
                    run.callback_envelope = replace(
                        run.callback_envelope, generation_id=generation_id
                    )
                    await self._execute(run, config, generation_id)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    code = _error_code(error)
                    original = getattr(error, "orig", None)
                    diagnostic: dict[str, str] = {"exception_type": type(error).__name__}
                    sqlstate = getattr(original, "sqlstate", None)
                    if isinstance(sqlstate, str):
                        diagnostic["sqlstate"] = sqlstate
                    constraint = getattr(
                        getattr(original, "diag", None), "constraint_name", None
                    )
                    if (
                        isinstance(constraint, str)
                        and constraint.isascii()
                        and len(constraint) <= 128
                    ):
                        diagnostic["constraint"] = constraint
                    logger.exception(
                        "agent pipeline failed",
                        extra={
                            "error_code": code,
                            "match_id": str(run.match_id),
                            "generation_id": str(run.generation_id) if run.generation_id else None,
                            "details": diagnostic,
                        },
                    )
                    playback_started = run.speech_id is not None
                    await self._mark_generation_failed(
                        run.generation_id,
                        code,
                        llm_capture=run.llm_capture,
                        tts_capture=run.tts_capture,
                    )
                    if isinstance(error, TtsProviderError):
                        await self._discard_tts_connection(run.match_id)
                    if playback_started:
                        await self._callbacks.handle_agent_failure(
                            envelope=self._run_envelope(run, attempt_no=attempt), error_code=code
                        )
                    await self._cleanup_attempt(run)
                    if attempt == 1 and not playback_started:
                        if run.generation_id is not None:
                            await self._callbacks.publish_agent_retry(
                                envelope=self._run_envelope(run, attempt_no=attempt),
                                error_code=code,
                            )
                        continue
                    if not playback_started:
                        await self._callbacks.handle_agent_failure(
                            envelope=self._run_envelope(run, attempt_no=attempt), error_code=code
                        )
                    return
        finally:
            # Do not let a terminal task make a later recovery look like a
            # duplicate run for the same action.
            if self._runs.get(run.match_id) is run:
                self._runs.pop(run.match_id, None)

    async def _discard_tts_connection(self, match_id: UUID) -> None:
        connection = self._tts_connections.pop(match_id, None)
        if connection is None:
            return
        try:
            await asyncio.wait_for(connection.close(), timeout=2.0)
        except TimeoutError:
            logger.warning(
                "stale TTS connection cleanup timed out",
                extra={"error_code": "tts_connection_cleanup_timeout"},
            )

    async def _execute(self, run: AgentRun, config: AgentConfig, generation_id: UUID) -> None:
        run.draft_parts.clear()
        run.speech_id = None
        run.played_samples = 0
        run.natural_complete = False
        run.byte_count = 0
        storage_root = Path(self._settings.agent_audio_storage_dir)
        directory = storage_root / str(run.match_id)
        directory.mkdir(parents=True, exist_ok=True)
        run.spool_path = directory / f".{generation_id}.ogg.part"
        run.storage_path = directory / f"{generation_id}.ogg"
        run.spool_path.write_bytes(b"")
        run.decoder = IncrementalOpusDecoder()
        await run.decoder.start(run.spool_path)
        run.room, run.source = await self._connect_publisher(run.match_id)
        run.text_queue = asyncio.Queue(maxsize=32)

        connection = self._tts_connections.get(run.match_id)
        if connection is None:
            api_key = self._settings.tts_api_key or self._settings.asr_api_key
            if api_key is None:
                raise TtsProviderError("tts_not_configured")
            connection = QwenTtsConnection(
                url=self._settings.tts_ws_url,
                api_key=api_key.get_secret_value(),
                model=self._settings.tts_model,
                workspace_id=self._settings.tts_workspace_id,
            )
            self._tts_connections[run.match_id] = connection

        spool = run.spool_path.open("ab", buffering=0)

        async def on_audio(chunk: bytes) -> None:
            spool.write(chunk)
            run.byte_count += len(chunk)
            assert run.decoder is not None
            run.decoder.notify_written(run.byte_count)

        async def start_tts_if_needed() -> None:
            if run.tts_task is None:
                assert run.text_queue is not None
                try:
                    run.tts_call_id = await self._start_tts_call(run, config, generation_id)
                except Exception:
                    logger.warning(
                        "TTS call capture start failed",
                        extra={
                            "error_code": "tts_capture_failed",
                            "match_id": str(run.match_id),
                            "generation_id": str(generation_id),
                        },
                    )
                run.tts_capture = ProviderCallCapture()
                run.tts_task = asyncio.create_task(
                    connection.synthesize(
                        iter_text_queue(run.text_queue),
                        voice=config.voice,
                        rate=config.rate,
                        on_audio=on_audio,
                        capture=run.tts_capture,
                    ),
                    name=f"agent-tts-{generation_id}",
                )

        async def on_delta(delta: str) -> None:
            run.draft_parts.append(delta)
            await self._callbacks.publish_agent_text_delta(
                envelope=self._run_envelope(run), text=delta
            )
            await start_tts_if_needed()
            assert run.text_queue is not None
            await run.text_queue.put(delta)

        client = OpenAIStreamingClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_id,
        )
        leases = await self._limiter.acquire(
            config.model_key, config.model_limit, timeout_seconds=3.0
        )
        playback = asyncio.create_task(
            self._play_pcm(run, generation_id), name=f"agent-play-{generation_id}"
        )
        try:
            run.llm_capture = ProviderCallCapture()
            try:
                await self._start_generation_call(
                    generation_id, config, logical_call_id=run.logical_call_id
                )
            except Exception:
                logger.warning(
                    "Agent LLM call capture start failed",
                    extra={
                        "error_code": "llm_capture_failed",
                        "match_id": str(run.match_id),
                        "generation_id": str(generation_id),
                    },
                )
            result = await client.stream_chat(
                messages=config.messages,
                max_tokens=config.max_tokens,
                generation_params=config.generation_params,
                on_delta=on_delta,
                capture=run.llm_capture,
            )
            await self._mark_generation_llm_ready(generation_id, result, run.llm_capture)
            await self._callbacks.agent_text_generated(
                envelope=self._run_envelope(run), text=result.text
            )
            assert run.text_queue is not None
            assert run.tts_task is not None
            await run.text_queue.put(None)
            tts_result = await run.tts_task
            run.tts_task_id = tts_result.task_id
            run.first_audio_latency_ms = tts_result.first_audio_latency_ms
            run.tts_completed_latency_ms = tts_result.completed_latency_ms
            try:
                await self._finish_external_call(
                    run.tts_call_id,
                    request_payload={
                        "text": result.text,
                        "voice": config.voice,
                        "rate": config.rate,
                        "mode": "server_commit",
                        "format": "opus",
                    },
                    response_payload={
                        "task_id": str(tts_result.task_id),
                        "audio_bytes": tts_result.byte_count,
                    },
                    first_result_latency_ms=tts_result.first_audio_latency_ms,
                    completed_latency_ms=tts_result.completed_latency_ms,
                    audio_bytes=run.byte_count,
                    provider_capture=run.tts_capture,
                )
            except Exception:
                logger.warning(
                    "TTS call capture completion failed",
                    extra={
                        "error_code": "tts_capture_failed",
                        "match_id": str(run.match_id),
                        "generation_id": str(generation_id),
                    },
                )
            assert run.decoder is not None
            run.decoder.finish_input()
            await playback
            assert run.source is not None
            await run.source.wait_for_playout()
            run.natural_complete = True
            if run.speech_id is None:
                raise TtsProviderError("tts_audio_empty")
            await self._callbacks.finish_agent_playback(envelope=self._run_envelope(run))
        finally:
            self._limiter.release(leases)
            await _close_llm_client(client)
            spool.close()
            if not playback.done():
                playback.cancel()
                await asyncio.gather(playback, return_exceptions=True)

    async def _play_pcm(self, run: AgentRun, generation_id: UUID) -> None:
        assert run.decoder is not None
        assert run.source is not None
        source = run.source
        frame_no = 0
        async for pcm in run.decoder.frames():
            async with self._media_lock:
                if (
                    run.media_fenced
                    or self._runs.get(run.match_id) is not run
                    or run.generation_id != generation_id
                ):
                    return
                if run.speech_id is None:
                    run.speech_id = uuid4()
                    run.callback_envelope = replace(
                        self._run_envelope(run), speech_id=run.speech_id
                    )
                    assert run.storage_path is not None
                    await self._start_agent_playback_with_retry(run)
                pcm = apply_pcm16_gain(pcm, run.playback_gain)
                samples = len(pcm) // 2
                frame = rtc.AudioFrame(
                    data=pcm,
                    sample_rate=48_000,
                    num_channels=1,
                    samples_per_channel=samples,
                )
                await source.capture_frame(frame)
                run.played_samples += samples
                frame_no += 1
            if frame_no % 5 == 0:
                assert run.speech_id is not None
                played_ms = run.played_samples * 1000 // 48_000
                subtitle = _played_prefix(run.draft, played_ms, run.chars_per_second)
                await self._callbacks.publish_agent_subtitle(
                    envelope=self._run_envelope(run), text=subtitle, played_ms=played_ms
                )

    async def _start_agent_playback_with_retry(self, run: AgentRun) -> None:
        """Commit the first playback event without failing on one transient error."""
        assert run.storage_path is not None
        for callback_attempt in range(2):
            try:
                await self._callbacks.start_agent_playback(
                    envelope=self._run_envelope(run),
                    agent_profile_id=run.agent_profile_id,
                    audio_storage_path=str(run.storage_path),
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if callback_attempt == 1:
                    raise
                logger.warning(
                    "Agent playback-start callback failed; retrying",
                    extra={
                        "error_code": "agent_playback_start_callback_retry",
                        "match_id": str(run.match_id),
                        "speech_id": str(run.speech_id) if run.speech_id else None,
                        "generation_id": str(run.generation_id) if run.generation_id else None,
                    },
                )
                await asyncio.sleep(0.05)

    async def _connect_publisher(self, match_id: UUID) -> tuple[rtc.Room, rtc.AudioSource]:
        if (
            self._settings.livekit_url is None
            or self._settings.livekit_api_key is None
            or self._settings.livekit_api_secret is None
        ):
            raise TtsProviderError("livekit_not_configured")
        room_name = f"jx-match-{match_id}"
        token = (
            api.AccessToken(
                self._settings.livekit_api_key.get_secret_value(),
                self._settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(f"jx-agent-{match_id}-{uuid4().hex[:8]}")
            .with_ttl(timedelta(minutes=10))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=False,
                    can_publish_data=False,
                )
            )
            .to_jwt()
        )
        room = rtc.Room()
        await room.connect(self._settings.livekit_url, token)
        source = rtc.AudioSource(48_000, 1, queue_size_ms=200)
        track = rtc.LocalAudioTrack.create_audio_track("agent-speech", source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await room.local_participant.publish_track(track, options)
        return room, source

    async def _load_config(self, run: AgentRun) -> AgentConfig:
        if self._settings.llm_key_encryption_key is None:
            raise LlmProviderError("model_encryption_not_configured")
        async with self._session_factory() as session:
            match = await session.get(Match, run.match_id)
            if match is None:
                raise LlmProviderError("match_not_found")
            experiment_attempt = await session.scalar(
                select(ExperimentMatchAttempt).where(
                    ExperimentMatchAttempt.match_id == run.match_id
                )
            )
            experiment_mode = experiment_attempt is not None
            runtime_snapshot = match.runtime_snapshot or {}
            formal_4v4 = bool(runtime_snapshot.get("formal_4v4", False))
            competition_mode = experiment_mode or formal_4v4
            room = await session.get(Room, match.room_id)
            agent = await session.get(AgentProfile, run.agent_profile_id)
            if room is None or agent is None or agent.status != "ENABLED":
                raise LlmProviderError("agent_unavailable")
            format_snapshot = room.format_snapshot
            frozen_agent = _snapshot_agent(format_snapshot, run.agent_profile_id)
            if format_snapshot is not None and frozen_agent is None:
                raise LlmProviderError("agent_unavailable")
            model = await session.get(ModelProfile, agent.model_profile_id)
            voice = await session.get(VoiceProfile, agent.voice_profile_id)
            if model is None or model.api_key_ciphertext is None or model.api_key_nonce is None:
                raise LlmProviderError("model_profile_unavailable")
            if frozen_agent is None and (
                model.status != "ENABLED" or not model.base_url or not model.model_id
            ):
                raise LlmProviderError("model_profile_unavailable")
            if voice is None or (frozen_agent is None and voice.status != "ENABLED"):
                raise LlmProviderError("voice_profile_unavailable")
            frozen_model = (
                cast(dict[str, Any], frozen_agent.get("model", {})) if frozen_agent else {}
            )
            frozen_voice = (
                cast(dict[str, Any], frozen_agent.get("voice", {})) if frozen_agent else {}
            )
            base_url = str(frozen_model.get("base_url") or model.base_url or "")
            model_id = str(frozen_model.get("model_id") or model.model_id or "")
            if not base_url or not model_id:
                raise LlmProviderError("model_profile_unavailable")
            participant = await session.scalar(
                select(MatchParticipant).where(
                    MatchParticipant.match_id == run.match_id,
                    MatchParticipant.kind == "AGENT",
                    MatchParticipant.agent_profile_id == run.agent_profile_id,
                )
            )
            if experiment_mode and participant is None:
                raise LlmProviderError("agent_unavailable")
            speech_statuses = ("FINALIZED", "STARTED") if competition_mode else ("FINALIZED",)
            speeches = list(
                (
                    await session.scalars(
                        select(Speech)
                        .where(
                            Speech.match_id == run.match_id,
                            Speech.status.in_(speech_statuses),
                            Speech.display_text.is_not(None),
                        )
                        .order_by(Speech.created_at)
                    )
                ).all()
            )
            history: list[dict[str, Any]] = [
                {
                    "stage": _stage_name_for_action(speech.action_key, room.rule_snapshot),
                    "speaker": _speaker_label(speech.side, speech.seat_no),
                    "side": speech.side,
                    "seat_no": speech.seat_no,
                    "speaker_kind": speech.speaker_kind,
                    "agent_profile_id": (
                        str(speech.agent_profile_id)
                        if speech.agent_profile_id is not None
                        else None
                    ),
                    "content": speech.display_text or "",
                }
                for speech in speeches
            ]
            topic = room.topic_snapshot
            effective_side = run.side or _side_for_action(run.action_key, room.rule_snapshot)
            affirmative_position = str(topic.get("affirmative_text") or "支持正方对辩题的明确判断")
            negative_position = str(topic.get("negative_text") or "支持反方对辩题的明确判断")
            position, current_position = _prompt_side_values(
                effective_side,
                affirmative_position=affirmative_position,
                negative_position=negative_position,
            )
            opponent_side = "NEGATIVE" if effective_side == "AFFIRMATIVE" else "AFFIRMATIVE"
            recent_history = history[-8:]
            recent_opponent = next(
                (
                    item
                    for item in reversed(history)
                    if item.get("side") == opponent_side and item.get("content")
                ),
                None,
            )
            agent_history = [
                item
                for item in history
                if item.get("speaker_kind") == "AGENT"
                and item.get("agent_profile_id") == str(run.agent_profile_id)
            ]
            chars_per_second = float(
                frozen_voice.get("chars_per_second") or voice.chars_per_second or 4.0
            )
            token_per_char = float(frozen_model.get("token_per_char") or model.token_per_char)
            target_chars = max(
                20,
                int((run.duration_ms / 1000) * chars_per_second * 0.85),
            )
            max_tokens = max(32, int(target_chars * token_per_char + 16))
            current_stage, next_stage = _current_and_next_stage(run.action_key, room.rule_snapshot)
            context = {
                "model_name": model.model_id,
                "debater_name": agent.name,
                "debate_side": effective_side,
                "debate_position": _debate_position(run.action_key, room.rule_snapshot),
                "debate_topic": topic.get("title", ""),
                "affirmative_position": affirmative_position,
                "negative_position": negative_position,
                "current_position": current_position,
                "current_stage": current_stage,
                "next_stage": next_stage,
                "holder": effective_side,
                "debate_history": json.loads(render_history(history)),
                "recent_history": recent_history,
                "recent_opponent_speech": recent_opponent,
                "this_agent_previous_speeches": agent_history[-4:],
                "target_chinese_characters": target_chars,
            }
            runtime_snapshot = match.runtime_snapshot
            affirmative_remaining_ms = int(
                runtime_snapshot.get("free_affirmative_remaining_ms") or 0
            )
            negative_remaining_ms = int(runtime_snapshot.get("free_negative_remaining_ms") or 0)
            side_remaining_ms = (
                affirmative_remaining_ms
                if effective_side == "AFFIRMATIVE"
                else negative_remaining_ms
            )
            opponent_remaining_ms = (
                negative_remaining_ms
                if effective_side == "AFFIRMATIVE"
                else affirmative_remaining_ms
            )
            messages: list[dict[str, str]] = []
            decision_messages: list[dict[str, str]] | None = None
            params: dict[str, Any] = {}
            stage_key = _stage_key_for_action(run.action_key, room.rule_snapshot)
            if format_snapshot and format_snapshot.get("schema") != "rule-config-v1":
                stage_key = _stage_kind_for_action(run.action_key, room.rule_snapshot)
            speech_template = _snapshot_prompt(
                format_snapshot,
                agent_profile_id=run.agent_profile_id,
                stage_key=stage_key,
                purpose="SPEECH",
            )
            decision_template = _snapshot_prompt(
                format_snapshot,
                agent_profile_id=run.agent_profile_id,
                stage_key=stage_key,
                purpose="DECISION",
            )
            prompt_version: str | None = None
            if speech_template is not None:
                assert participant is not None
                assert frozen_agent is not None
                assert format_snapshot is not None
                prompt_variables = {
                    "TOPIC": str(topic.get("title", "")),
                    "POSITION": position,
                    "STANCE": current_position,
                    "AFFIRMATIVE_STANCE": affirmative_position,
                    "NEGATIVE_STANCE": negative_position,
                    "SEAT": _seat_label(participant.seat_no),
                    "DEBATER_SEAT": _seat_label(participant.seat_no),
                    "STAGE_NAME": current_stage,
                    "MAX_SPEECH_SECONDS": str(max(1, run.duration_ms // 1000)),
                    "TARGET_CHAR_COUNT": str(target_chars),
                    "DEBATE_HISTORY": render_history(history),
                    "SIDE_REMAINING_MS": str(side_remaining_ms),
                    "OPPONENT_REMAINING_MS": str(opponent_remaining_ms),
                    "CURRENT_STAGE": current_stage,
                    "NEXT_STAGE": next_stage,
                }
                speech_prompt = _render_snapshot_prompt(
                    str(speech_template["template_text"]), prompt_variables
                )
                messages = [{"role": "user", "content": speech_prompt}]
                if decision_template is not None:
                    decision_messages = [
                        {
                            "role": "user",
                            "content": _render_snapshot_prompt(
                                str(decision_template["template_text"]), prompt_variables
                            ),
                        }
                    ]
                params = {
                    **cast(dict[str, Any], frozen_model.get("generation_params", {})),
                    **cast(dict[str, Any], frozen_agent.get("generation_params", {})),
                }
                prompt_version = (
                    f"{format_snapshot.get('rule_key')}@r{format_snapshot.get('config_revision')}"
                    if format_snapshot.get("schema") == "rule-config-v1"
                    else f"{format_snapshot.get('format_key')}@{format_snapshot.get('version')}"
                )
            elif experiment_mode:
                assert participant is not None
                messages = [
                    {
                        "role": "user",
                        "content": render_experiment_speech_prompt(
                            side=effective_side,
                            seat_no=participant.seat_no,
                            topic=str(topic.get("title", "")),
                            affirmative_stance=affirmative_position,
                            negative_stance=negative_position,
                            side_remaining_ms=side_remaining_ms,
                            opponent_remaining_ms=opponent_remaining_ms,
                            history=history,
                            max_speech_seconds=max(1, run.duration_ms // 1000),
                        ),
                    }
                ]
                decision_messages = [
                    {
                        "role": "user",
                        "content": render_experiment_decision_prompt(
                            side=effective_side,
                            seat_no=participant.seat_no,
                            topic=str(topic.get("title", "")),
                            affirmative_stance=affirmative_position,
                            negative_stance=negative_position,
                            side_remaining_ms=side_remaining_ms,
                            opponent_remaining_ms=opponent_remaining_ms,
                            history=history,
                        ),
                    }
                ]
                params = {"temperature": 0.75, "top_p": 0.9, "enable_thinking": False}
                prompt_version = EXPERIMENT_PROMPT_VERSION
            else:
                decision_messages = None
            prompt_addendum = (
                "\n\n【必须遵守的现场发言约束】\n"
                f"你当前是{('正方' if effective_side == 'AFFIRMATIVE' else '反方')}，"
                f"本方明确立场是：{current_position}。"
                "不得替对方辩护，不得把对方立场写成我方结论。\n"
                "先回应最近一条对方发言中的一个具体命题，再推进本方论证。"
                "每次发言必须引入至少一个此前记录中没有使用过的新论据、反例、限定条件或追问；"
                "禁止重复自己、队友或对方已经说过的完整论点、例子、比喻、结论和固定句式。\n"
                "如果当前没有新的有效回应，不要为了凑时长重复口号；自由辩论决策应选择不发言。\n"
                "避免使用‘首先/其次/综上’等机械套话和‘谢谢主席，各位好’等固定开场，直接进入回应。"
                "输出仅为可直接朗读的正式发言，不输出分析、标签、标题或舞台说明。"
            )
            if not experiment_mode and speech_template is None:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            agent.system_prompt
                            or "你是一名中文辩论赛辩手。论证清晰、直接回应对方，不输出舞台说明。"
                        )
                        + prompt_addendum,
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{agent.debater_prompt}\n"
                            "请只根据下面的结构化现场上下文生成本轮发言：\n"
                            f"{json.dumps(context, ensure_ascii=False)}"
                        ),
                    },
                ]
                params = {**model.generation_params, **agent.generation_params}
            api_key = decrypt_secret(
                model.api_key_ciphertext,
                model.api_key_nonce,
                self._settings.llm_key_encryption_key.get_secret_value(),
            )
            return AgentConfig(
                match_id=run.match_id,
                action_key=run.action_key,
                agent_profile_id=run.agent_profile_id,
                context_version=match.context_version,
                match_seed=match.match_seed,
                model_key=str(frozen_model.get("id") or model.id),
                base_url=base_url,
                model_id=model_id,
                api_key=api_key,
                model_limit=int(frozen_model.get("max_concurrency") or model.max_concurrency),
                generation_params=params,
                max_tokens=max_tokens,
                messages=messages,
                voice=str(frozen_voice.get("provider_voice") or voice.provider_voice),
                rate=float(frozen_voice.get("rate") or voice.rate),
                chars_per_second=chars_per_second,
                playback_gain=float(frozen_voice.get("playback_gain") or voice.playback_gain),
                experiment_mode=experiment_mode,
                formal_4v4=formal_4v4,
                decision_messages=decision_messages,
                prompt_version=prompt_version,
                opportunity_id=(
                    UUID(str(runtime_snapshot["opportunity_id"]))
                    if runtime_snapshot.get("opportunity_id")
                    else None
                ),
                opportunity_generation=(
                    int(runtime_snapshot["opportunity_generation"])
                    if runtime_snapshot.get("opportunity_generation")
                    and int(runtime_snapshot["opportunity_generation"]) > 0
                    else None
                ),
            )

    async def _start_decision_call(
        self,
        *,
        config: AgentConfig,
        decision_round_id: UUID,
        envelope: CallbackEnvelope,
        messages: list[dict[str, str]],
        attempt_no: int,
        logical_call_id: UUID,
    ) -> UUID:
        call_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                decision = await session.scalar(
                    select(AgentFreeDebateDecision).where(
                        AgentFreeDebateDecision.match_id == config.match_id,
                        AgentFreeDebateDecision.decision_round_id == decision_round_id,
                        AgentFreeDebateDecision.agent_profile_id == config.agent_profile_id,
                    )
                )
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="LLM_DECISION",
                        provider="OPENAI_COMPATIBLE",
                        operation="chat.completions.stream",
                        model=config.model_id,
                        attempt_no=attempt_no,
                        status="STARTED",
                        match_id=config.match_id,
                        speech_id=envelope.speech_id,
                        agent_decision_id=decision.id if decision is not None else None,
                        decision_round_id=decision_round_id,
                        opportunity_id=envelope.opportunity_id,
                        connection_epoch=envelope.connection_epoch,
                        context_version=envelope.context_version,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=logical_call_id,
                        started_at=datetime.now(UTC),
                    )
                )
        return call_id

    async def _start_generation_call(
        self, generation_id: UUID, config: AgentConfig, *, logical_call_id: UUID
    ) -> UUID:
        call_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(AgentGeneration, generation_id)
                if generation is None:
                    raise LlmProviderError("agent_generation_not_found")
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="LLM_SPEECH",
                        provider="OPENAI_COMPATIBLE",
                        operation="chat.completions.stream",
                        model=config.model_id,
                        attempt_no=generation.attempt_no,
                        status="STARTED",
                        match_id=config.match_id,
                        agent_generation_id=generation_id,
                        generation_id=generation_id,
                        context_version=config.context_version,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=logical_call_id,
                        started_at=datetime.now(UTC),
                    )
                )
        return call_id

    async def _start_tts_call(
        self, run: AgentRun, config: AgentConfig, generation_id: UUID
    ) -> UUID:
        call_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="TTS",
                        provider="BAILIAN",
                        operation="duplex.server_commit",
                        model=self._settings.tts_model,
                        voice=config.voice,
                        attempt_no=1,
                        status="STARTED",
                        match_id=run.match_id,
                        agent_generation_id=generation_id,
                        generation_id=generation_id,
                        context_version=config.context_version,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=run.tts_logical_call_id,
                        started_at=datetime.now(UTC),
                    )
                )
        return call_id

    async def _finish_external_call(
        self,
        call_id: UUID | None,
        *,
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
        first_result_latency_ms: int | None = None,
        completed_latency_ms: int | None = None,
        completion_tokens: int | None = None,
        audio_bytes: int | None = None,
        audio_duration_ms: int | None = None,
        provider_capture: ProviderCallCapture | None = None,
    ) -> None:
        if call_id is None:
            return
        async with self._session_factory() as session:
            async with session.begin():
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                if call is None:
                    return
                if provider_capture is not None:
                    await persist_provider_capture(session, call, provider_capture)
                elif request_payload is not None:
                    call.request_blob_id = await store_content_blob(
                        session, content_kind="REQUEST", payload=request_payload
                    )
                if response_payload is not None:
                    call.response_blob_id = await store_content_blob(
                        session, content_kind="RESPONSE", payload=response_payload
                    )
                call.status = "SUCCEEDED"
                call.first_result_latency_ms = first_result_latency_ms
                call.completed_latency_ms = completed_latency_ms
                call.completion_tokens = completion_tokens
                call.audio_bytes = audio_bytes
                call.audio_duration_ms = audio_duration_ms
                call.first_result_at = _at_latency(call.started_at, first_result_latency_ms)
                call.completed_at = datetime.now(UTC)

    async def _fail_external_call(
        self,
        call_id: UUID | None,
        error_code: str,
        *,
        cancelled: bool = False,
        response_payload: dict[str, Any] | None = None,
        provider_capture: ProviderCallCapture | None = None,
    ) -> None:
        if call_id is None:
            return
        async with self._session_factory() as session:
            async with session.begin():
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                if call is None or call.status != "STARTED":
                    return
                if provider_capture is not None:
                    await persist_provider_capture(session, call, provider_capture)
                elif response_payload is not None:
                    call.response_blob_id = await store_content_blob(
                        session, content_kind="RESPONSE", payload=response_payload
                    )
                call.status = "CANCELLED" if cancelled else "FAILED"
                call.error_code = error_code
                call.completed_at = datetime.now(UTC)

    async def _create_generation(self, config: AgentConfig, attempt_no: int) -> UUID:
        generation_id = uuid4()
        snapshot = {
            "action_key": config.action_key,
            "context_version": config.context_version,
            "model_id": config.model_id,
            "max_tokens": config.max_tokens,
            "message_count": len(config.messages),
            "prompt_version": config.prompt_version,
            "capture_version": CAPTURE_VERSION,
        }
        async with self._session_factory() as session:
            async with session.begin():
                request_payload = {
                    "model": config.model_id,
                    "messages": config.messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_tokens": config.max_tokens,
                    "prompt_version": config.prompt_version,
                    "generation_params": {
                        **config.generation_params,
                        "enable_thinking": config.generation_params.get("enable_thinking", False),
                    },
                }
                request_blob_id = await store_content_blob(
                    session, content_kind="REQUEST", payload=request_payload
                )
                session.add(
                    AgentGeneration(
                        id=generation_id,
                        match_id=config.match_id,
                        action_key=config.action_key,
                        agent_profile_id=config.agent_profile_id,
                        context_version=config.context_version,
                        attempt_no=attempt_no,
                        input_snapshot=snapshot,
                        call_type="LLM_SPEECH",
                        provider="OPENAI_COMPATIBLE",
                        model_snapshot=config.model_id,
                        generation_params_snapshot=cast(
                            dict[str, Any], request_payload["generation_params"]
                        ),
                        request_blob_id=request_blob_id,
                        capture_version=CAPTURE_VERSION,
                        capture_completeness="COMPLETE",
                    )
                )
        return generation_id

    async def _mark_generation_llm_ready(
        self,
        generation_id: UUID,
        result: object,
        provider_capture: ProviderCallCapture | None,
    ) -> None:
        from .llm import LlmStreamResult

        if not isinstance(result, LlmStreamResult):
            return
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(AgentGeneration, generation_id, with_for_update=True)
                if generation is None:
                    return
                generation.status = "PLAYING"
                generation.first_token_latency_ms = result.first_token_latency_ms
                generation.completed_latency_ms = result.completed_latency_ms
                generation.completion_tokens = result.completion_tokens
                generation.response_blob_id = await store_content_blob(
                    session,
                    content_kind="RESPONSE",
                    payload={"text": result.text},
                )
                call = await session.scalar(
                    select(ExternalCall)
                    .where(
                        ExternalCall.agent_generation_id == generation_id,
                        ExternalCall.call_kind == "LLM_SPEECH",
                    )
                    .with_for_update()
                )
                if call is not None:
                    completed_at = datetime.now(UTC)
                    call.status = "SUCCEEDED"
                    if provider_capture is not None:
                        await persist_provider_capture(session, call, provider_capture)
                    call.first_result_at = _at_latency(
                        call.started_at, result.first_token_latency_ms
                    )
                    call.completed_at = completed_at
                    call.first_result_latency_ms = result.first_token_latency_ms
                    call.completed_latency_ms = result.completed_latency_ms
                    call.completion_tokens = result.completion_tokens

    async def _persist_final(self, run: AgentRun) -> None:
        assert run.generation_id is not None
        assert run.storage_path is not None
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(
                    AgentGeneration, run.generation_id, with_for_update=True
                )
                if generation is not None:
                    generation.status = "FINALIZED"
                    generation.completed_at = datetime.now(UTC)
                asset = await session.scalar(
                    select(AgentAudioAsset).where(
                        AgentAudioAsset.generation_id == run.generation_id
                    )
                )
                if asset is None:
                    session.add(
                        AgentAudioAsset(
                            generation_id=run.generation_id,
                            task_id=str(run.tts_task_id or run.generation_id),
                            voice=run.voice,
                            rate=run.rate,
                            status="FINALIZED",
                            storage_path=str(run.storage_path),
                            byte_count=run.byte_count,
                            first_audio_latency_ms=run.first_audio_latency_ms,
                            tts_completed_latency_ms=run.tts_completed_latency_ms,
                            pcm_sample_count=run.played_samples,
                            duration_ms=run.played_samples * 1000 // 48_000,
                            finalized_at=datetime.now(UTC),
                        )
                    )
                tts_call = (
                    await session.get(ExternalCall, run.tts_call_id, with_for_update=True)
                    if run.tts_call_id is not None
                    else None
                )
                if tts_call is not None:
                    duration_ms = run.played_samples * 1000 // 48_000
                    tts_call.audio_bytes = run.byte_count
                    tts_call.audio_duration_ms = duration_ms

    async def _mark_generation_failed(
        self,
        generation_id: UUID | None,
        error_code: str,
        *,
        llm_capture: ProviderCallCapture | None = None,
        tts_capture: ProviderCallCapture | None = None,
    ) -> None:
        if generation_id is None:
            return
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(AgentGeneration, generation_id, with_for_update=True)
                if generation is not None and generation.status != "FINALIZED":
                    generation.status = "FAILED"
                    generation.error_code = error_code
                    generation.completed_at = datetime.now(UTC)
                calls = list(
                    (
                        await session.scalars(
                            select(ExternalCall)
                            .where(
                                ExternalCall.agent_generation_id == generation_id,
                                ExternalCall.status == "STARTED",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for call in calls:
                    capture = llm_capture if call.call_kind == "LLM_SPEECH" else tts_capture
                    if capture is not None:
                        await persist_provider_capture(session, call, capture)
                    call.status = "FAILED"
                    call.error_code = error_code
                    call.completed_at = datetime.now(UTC)

    async def _cleanup_attempt(self, run: AgentRun) -> None:
        if run.tts_task is not None:
            run.tts_task.cancel()
            await asyncio.gather(run.tts_task, return_exceptions=True)
        run.tts_task = None
        if run.decoder is not None:
            run.decoder.finish_input()
            await run.decoder.close()
            run.decoder = None
        if run.source is not None:
            run.source.clear_queue()
            await run.source.aclose()
            run.source = None
        if run.room is not None:
            await run.room.disconnect()
            run.room = None
        if run.spool_path is not None:
            run.spool_path.unlink(missing_ok=True)
        run.text_queue = None

    async def _cancel_run(self, run: AgentRun) -> None:
        # Fence publication before awaiting any potentially stuck LiveKit/API cleanup.
        # A cancelled generation must never enqueue another frame after reset commits.
        async with self._media_lock:
            run.media_fenced = True
            if run.source is not None:
                run.source.clear_queue()
        if run.task is not None and not run.task.done():
            await self._cancel_agent_task(run.task)
        try:
            await asyncio.wait_for(
                self._cleanup_attempt(run), timeout=AGENT_TASK_CANCEL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            logger.warning(
                "agent attempt cleanup timed out",
                extra={"error_code": "agent_cleanup_timeout"},
            )
        if run.generation_id is not None:
            await self._mark_generation_failed(run.generation_id, "agent_cancelled")

    async def _cancel_agent_task(self, task: asyncio.Task[None]) -> None:
        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=AGENT_TASK_CANCEL_TIMEOUT_SECONDS)
        if pending:
            logger.warning(
                "agent task cancellation timed out",
                extra={
                    "error_code": "agent_cancel_timeout",
                    "task_name": task.get_name(),
                },
            )
            return
        # Consume unexpected cleanup errors so they do not become unhandled
        # event-loop warnings. Provider failures use the normal callback path.
        for completed in done:
            if not completed.cancelled():
                completed.exception()

    async def _close_run(self, run: AgentRun) -> None:
        if run.decoder is not None:
            run.decoder.finish_input()
            await run.decoder.close()
            run.decoder = None
        if run.source is not None:
            await run.source.aclose()
            run.source = None
        if run.room is not None:
            await run.room.disconnect()
            run.room = None


def _error_code(error: Exception) -> str:
    if isinstance(error, (LlmProviderError, TtsProviderError)):
        return error.code
    return "agent_pipeline_failed"


def _at_latency(started_at: datetime, latency_ms: int | None) -> datetime | None:
    if latency_ms is None:
        return None
    return started_at + timedelta(milliseconds=max(0, latency_ms))


AGENT_SUBTITLE_LEAD_MS = 300
AGENT_FINALIZATION_TIMEOUT_SECONDS = 15.0


def _played_prefix(text: str, played_ms: int, chars_per_second: float) -> str:
    count = max(
        0,
        int((played_ms + AGENT_SUBTITLE_LEAD_MS) / 1000 * chars_per_second),
    )
    return text[:count]


def _side_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    for stage in rule_snapshot.get("stages", []):
        for action in stage.get("actions", []):
            if f"{stage.get('position')}:{action.get('position')}" == action_key:
                return str(action.get("side") or "")
    return ""


def _prompt_side_values(
    side: str, *, affirmative_position: str, negative_position: str
) -> tuple[str, str]:
    if side == "AFFIRMATIVE":
        return "正方", affirmative_position
    if side == "NEGATIVE":
        return "反方", negative_position
    raise LlmProviderError("agent_side_invalid")


def _stage_name(stage: dict[str, Any]) -> str:
    position = int(stage.get("position", 0))
    return str(stage.get("name") or f"第 {position} 阶段")


def _stage_name_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    for stage in rule_snapshot.get("stages", []):
        if int(stage.get("position", 0)) == stage_position:
            return _stage_name(stage)
    return f"第 {stage_position} 阶段"


def _stage_key_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    return f"stage-{stage_position}"


def _stage_kind_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    for stage in rule_snapshot.get("stages", []):
        if int(stage.get("position", 0)) == stage_position:
            return f"stage-{stage_position}"
    return ""


def _seat_label(seat_no: int) -> str:
    return {1: "一辩", 2: "二辩", 3: "三辩", 4: "四辩"}.get(seat_no, f"{seat_no}辩")


def _current_and_next_stage(action_key: str, rule_snapshot: dict[str, Any]) -> tuple[str, str]:
    stage_position = int(action_key.split(":", 1)[0])
    raw_stages = cast(list[Any], rule_snapshot.get("stages", []))
    stages: list[dict[str, Any]] = [
        cast(dict[str, Any], stage) for stage in raw_stages if isinstance(stage, dict)
    ]
    stages.sort(key=lambda stage: int(stage.get("position", 0)))
    current = next(
        (stage for stage in stages if int(stage.get("position", 0)) == stage_position),
        {"position": stage_position},
    )
    following = next(
        (stage for stage in stages if int(stage.get("position", 0)) > stage_position), None
    )
    return _stage_name(current), _stage_name(following) if following else "比赛结束"


def _debate_position(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    for stage in rule_snapshot.get("stages", []):
        for action in stage.get("actions", []):
            if f"{stage.get('position')}:{action.get('position')}" == action_key:
                side = "正方" if action.get("side") == "AFFIRMATIVE" else "反方"
                seat_no = action.get("seat_no")
                return f"{side}{seat_no}辩" if seat_no else side
    return "自由辩论"


def _speaker_label(side: str, seat_no: int) -> str:
    return f"{'正方' if side == 'AFFIRMATIVE' else '反方'}{seat_no}辩"


__all__ = ["AgentRuntime", "AgentRuntimeCallbacks"]
