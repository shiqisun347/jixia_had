"""Owned PG regression: real Room/Manager/Actor/Agent, synthetic provider/media.

No production settings, provider calls, SDK overlay or business changes. Keep
the retirement assertion red when the old durable Speech remains unfinished.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import sys
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import server_full_match_probe as full
from pydantic import SecretStr
from sqlalchemy import select
from starlette.requests import Request

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult
from jx_core.agent.runtime import AgentRuntime
from jx_core.agent.tts import TtsStreamResult
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.database import Database
from jx_core.matches.routes import update_speech_display_text
from jx_core.matches.schemas import SpeechTextUpdateRequest
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import (
    AgentAudioAsset,
    AgentGeneration,
    HostAudioAsset,
    Match,
    RuleAudioGeneration,
    Speech,
    User,
)


class SyntheticDecoder:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
        self.written = False

    async def start(self, _: Path) -> None:
        return None

    def notify_written(self, _: int) -> None:
        if not self.written:
            self.written = True
            self.queue.put_nowait(b"\x10\x00" * 4800)

    def finish_input(self) -> None:
        if not self.queue.full():
            self.queue.put_nowait(None)

    async def frames(self) -> Any:
        while (chunk := await self.queue.get()) is not None:
            yield chunk

    async def close(self) -> None:
        self.finish_input()


class SyntheticSource:
    async def capture_frame(self, _: Any) -> None:
        await asyncio.sleep(0.001)

    async def wait_for_playout(self) -> None:
        return None

    def clear_queue(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class SyntheticTTS:
    def __init__(self, **_: Any) -> None:
        pass

    async def synthesize(self, chunks: Any, **values: Any) -> TtsStreamResult:
        async for _ in chunks:
            await values["on_audio"](b"synthetic-owned-audio")
        # Model normal asynchronous provider task-finished acknowledgement.
        await asyncio.sleep(0.03)
        return TtsStreamResult(uuid4(), 21, 1, 2)

    async def close(self) -> None:
        return None


class SyntheticProviders:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls = 0

    def client(self, **configuration: Any) -> Any:
        self.calls += 1
        fast_decision = "first_token_timeout_seconds" in configuration

        async def stream_chat(**values: Any) -> LlmStreamResult:
            if fast_decision:
                return LlmStreamResult('{"should_speak":true,"decision_reason":"合成"}', 1, 2, 12)
            await values["on_delta"]("有界合成暂停恢复发言。")
            await self.release.wait()
            return LlmStreamResult("有界合成暂停恢复发言。", 1, 2, 8)

        async def close() -> None:
            return None

        return SimpleNamespace(stream_chat=stream_chat, close=close)


async def synthetic_host_jobs(factory: Any, **values: Any) -> bool:
    async with factory() as session:
        assets = list((await session.scalars(select(HostAudioAsset))).all())
        for asset in assets:
            path = Path(values["storage_root"]) / f"synthetic-{asset.id}.ogg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-owned-host")
            asset.status = "READY"
            asset.storage_path = path.name
            asset.duration_ms = 100
            asset.byte_count = path.stat().st_size
        for generation in (await session.scalars(select(RuleAudioGeneration))).all():
            generation.status = "SUCCEEDED"
            generation.ready_count = sum(a.generation_id == generation.id for a in assets)
        await session.commit()
    return bool(assets)


async def wait_until(check: Any, timeout: float = 8) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


async def rows(factory: Any, match_id: Any) -> dict[str, Any]:
    async with factory() as session:
        speeches = list(
            (await session.scalars(select(Speech).where(Speech.match_id == match_id))).all()
        )
        generations = list(
            (
                await session.scalars(
                    select(AgentGeneration).where(AgentGeneration.match_id == match_id)
                )
            ).all()
        )
        match = await session.get(Match, match_id)
        assets = list(
            (
                await session.scalars(
                    select(AgentAudioAsset)
                    .join(AgentGeneration, AgentAudioAsset.generation_id == AgentGeneration.id)
                    .where(AgentGeneration.match_id == match_id)
                )
            ).all()
        )
        return {
            "match_status": match.status if match else None,
            "context_version": match.context_version if match else None,
            "sequence": match.sequence if match else None,
            "speeches": sorted(
                [
                    {
                        "speech_id": str(s.id),
                        "generation_id": str(s.generation_id),
                        "status": s.status,
                        "audio_duration_ms": s.audio_duration_ms,
                        "finalized": s.finalized_at is not None,
                    }
                    for s in speeches
                ],
                key=lambda item: item["speech_id"],
            ),
            "generations": sorted(
                [
                    {
                        "generation_id": str(g.id),
                        "status": g.status,
                        "error_code": g.error_code,
                        "context_version": g.context_version,
                    }
                    for g in generations
                ],
                key=lambda item: item["generation_id"],
            ),
            "assets": sorted(
                [
                    {
                        "generation_id": str(a.generation_id),
                        "status": a.status,
                        "file_exists": Path(a.storage_path).exists(),
                    }
                    for a in assets
                ],
                key=lambda item: item["generation_id"],
            ),
        }


async def case(
    factory: Any,
    settings: Settings,
    *,
    finalizer_control: bool,
    context_drift: bool = False,
    free_control: bool = False,
) -> dict[str, Any]:
    providers = SyntheticProviders()
    manager = MatchRuntimeManager(factory)
    runtime = AgentRuntime(settings=settings, session_factory=factory, callbacks=manager)
    manager.set_agent_runtime(runtime)
    receipt: dict[str, Any] = {
        "case": "free_held_finalizer"
        if free_control
        else "history_context_edit"
        if context_drift
        else "finalizer_owner"
        if finalizer_control
        else "pause_playback"
    }
    original_persist = runtime._persist_final
    finalizer_entered, finalizer_release = asyncio.Event(), asyncio.Event()

    async def held_persist(run: Any) -> None:
        if finalizer_control:
            finalizer_entered.set()
            await finalizer_release.wait()
        await original_persist(run)

    async def publisher(*_: Any, **__: Any) -> Any:
        async def disconnect() -> None:
            return None

        return SimpleNamespace(disconnect=disconnect), SyntheticSource()

    material = SimpleNamespace(
        base_url="https://synthetic.invalid/v1",
        model_id="synthetic-model",
        model_params={},
        api_key="synthetic",
        judge_prompt="synthetic",
    )
    voices = [
        {"name": f"owned-host-{uuid4().hex[:8]}", "kind": "HOST", "provider_voice": "synthetic"},
        *[
            {
                "name": f"owned-agent-{uuid4().hex[:8]}",
                "kind": "AGENT",
                "provider_voice": f"synthetic-{index}",
                "avatar_key": f"agent-{index + 1:02d}",
            }
            for index in range(8)
        ],
    ]
    with (
        patch.object(full, "DashScopeTTSClient", SyntheticTTS),
        patch.object(full, "process_one_host_tts", synthetic_host_jobs),
        patch.object(runtime_module, "OpenAIStreamingClient", providers.client),
        patch.object(runtime_module, "QwenTtsConnection", SyntheticTTS),
        patch.object(runtime_module, "IncrementalOpusDecoder", SyntheticDecoder),
        patch.object(runtime, "_connect_publisher", publisher),
        patch.object(runtime, "_persist_final", held_persist),
    ):
        try:
            values = await full.prepare_room(
                factory,
                settings,
                material,
                voices,
                handoff_slice=not (context_drift or free_control),
                free_closing_slice=free_control,
                lifecycle_slice=context_drift,
            )
            async with factory() as session:
                pending = await manager.start_room_match(
                    session,
                    room_id=values["room_id"],
                    actor_user_id=values["owner"],
                    actor_role="USER",
                )
            actor = await manager.get_actor(pending.match_id)
            await wait_until(
                lambda: actor.state.action_state == "AGENT_SPEAKING", 12 if free_control else 8
            )
            if context_drift:
                historical_speech = actor.state.current_speech_id
                old_action_index = actor.state.current_action_index
                providers.release.set()
                await wait_until(lambda: actor.state.current_action_index != old_action_index)
                providers.release = asyncio.Event()
                await wait_until(
                    lambda: actor.state.action_state == "AGENT_SPEAKING", 12 if free_control else 8
                )
                async with factory() as session:
                    admin = await session.scalar(select(User).where(User.role == "ADMIN"))
                    assert admin is not None
                    await session.commit()
                    await update_speech_display_text(
                        actor.state.match_id,
                        historical_speech,
                        SpeechTextUpdateRequest(display_text="管理员合法编辑合成历史发言"),
                        Request(
                            {
                                "type": "http",
                                "app": SimpleNamespace(
                                    state=SimpleNamespace(match_runtime_manager=manager)
                                ),
                            }
                        ),
                        AuthContext(admin.id, "ADMIN", uuid4(), False),
                        session,
                    )
                receipt["history_edit_route"] = "normal_admin_route"
            if free_control:
                assert actor.state.current_action.action_kind == "FREE_DEBATE"
                receipt["current_action_kind"] = actor.state.current_action.action_kind
                receipt["formal_4v4"] = actor.state.formal_4v4
            old_run = runtime._runs[actor.state.match_id]
            old_envelope = runtime._run_envelope(old_run)
            receipt["before"] = await rows(factory, actor.state.match_id)
            receipt["old_speech_id"] = str(old_run.speech_id)
            receipt["old_generation_id"] = str(old_run.generation_id)
            assert old_run.played_samples > 0
            if finalizer_control:
                providers.release.set()
                await asyncio.wait_for(finalizer_entered.wait(), 5)
                assert old_run.finalizing
                held_path = old_run.storage_path
                assert held_path is not None and held_path.exists()
                receipt["finalizer_before_pause"] = await rows(factory, actor.state.match_id)
            await full.route_command(manager, factory, values, actor, "match.pause")
            assert actor.state.status == "PAUSED"
            if finalizer_control:
                await wait_until(lambda: actor.state.match_id not in runtime._runs)
            else:
                await wait_until(lambda: not manager._background_tasks)
            receipt["after_pause"] = await rows(factory, actor.state.match_id)
            if finalizer_control:
                receipt["finalizer_file_preserved"] = held_path.exists()
                receipt["finalizer_task_still_owned"] = old_run.finalizing
                finalizer_release.set()
                await wait_until(lambda: not manager._background_tasks)
                receipt["after_finalizer_release"] = await rows(factory, actor.state.match_id)
            if finalizer_control:
                providers.release = asyncio.Event()
            start = monotonic()
            await full.route_command(manager, factory, values, actor, "match.resume")
            assert actor.state.action_state == "RESUME_COUNTDOWN"
            await wait_until(lambda: actor.state.status == "RUNNING", 6)
            receipt["natural_resume_ms"] = round((monotonic() - start) * 1000)
            await wait_until(
                lambda: actor.state.action_state == "AGENT_SPEAKING", 12 if free_control else 8
            )
            new_run = runtime._runs[actor.state.match_id]
            receipt["new_speech_id"] = str(new_run.speech_id)
            receipt["new_generation_id"] = str(new_run.generation_id)
            assert old_run.speech_id != new_run.speech_id
            assert old_run.generation_id != new_run.generation_id
            before_late = await rows(factory, actor.state.match_id)
            if free_control:
                assert old_run.storage_path is not None
                await manager.finalize_agent_speech(
                    envelope=old_envelope,
                    final_text="迟到合成全文",
                    llm_draft_text="迟到合成全文",
                    audio_storage_path=str(old_run.storage_path),
                    audio_duration_ms=100,
                    audio_truncated=False,
                )
                after_free_callback = await rows(factory, actor.state.match_id)
                receipt["free_old_callback_context_unchanged"] = (
                    before_late["context_version"] == after_free_callback["context_version"]
                )
                receipt["free_old_callback_speeches_unchanged"] = (
                    before_late["speeches"] == after_free_callback["speeches"]
                )
            await manager.publish_agent_subtitle(
                envelope=old_envelope, text="迟到合成字幕", played_ms=9
            )
            await manager.agent_text_generated(envelope=old_envelope, text="迟到合成全文")
            try:
                await runtime._mark_generation_llm_ready(
                    old_run.generation_id, LlmStreamResult("迟到合成结果", 1, 2, 8), None
                )
            except asyncio.CancelledError:
                receipt["late_llm_ready"] = "rejected"
            else:
                receipt["late_llm_ready"] = "accepted"
            receipt["late_callbacks_durable_rows_unchanged"] = before_late == await rows(
                factory, actor.state.match_id
            )
            providers.release.set()
            finalizer_release.set()
            if free_control:
                async with asyncio.timeout(8):
                    while True:
                        state_rows = await rows(factory, actor.state.match_id)
                        if any(
                            row["speech_id"] == receipt["new_speech_id"]
                            and row["status"] == "FINALIZED"
                            for row in state_rows["speeches"]
                        ):
                            break
                        await asyncio.sleep(0.02)
                await wait_until(lambda: not manager._background_tasks)
                await full.route_command(manager, factory, values, actor, "match.terminate")
                assert actor.state.status == "TERMINATED"
                receipt["subset_terminated_by_normal_owner_command"] = True
            else:
                await wait_until(lambda: actor.state.status == "FINISHED")
            await wait_until(lambda: not manager._background_tasks)
            receipt["after_finish"] = await rows(factory, actor.state.match_id)
            # Normal cleanup against an actually FINALIZED generation must preserve it.
            before_control = await rows(factory, actor.state.match_id)
            await runtime._mark_generation_failed(new_run.generation_id, "agent_cancelled")
            await runtime.reset_agent(actor.state.match_id)
            receipt["finalized_cleanup_preserved"] = before_control == await rows(
                factory, actor.state.match_id
            )
            old = next(
                s
                for s in receipt["after_finish"]["speeches"]
                if s["speech_id"] == receipt["old_speech_id"]
            )
            new = next(
                s
                for s in receipt["after_finish"]["speeches"]
                if s["speech_id"] == receipt["new_speech_id"]
            )
            receipt["retired_speech_terminal_gate"] = old["status"] == "RESET"
            receipt["new_speech_finalized_gate"] = new["status"] == "FINALIZED"
            if finalizer_control:
                receipt["finalizer_asset_retained_gate"] = any(
                    a["generation_id"] == receipt["old_generation_id"]
                    and a["status"] == "FINALIZED"
                    and a["file_exists"]
                    for a in receipt["after_finish"]["assets"]
                )
                receipt["finalizer_generation_finalized_gate"] = any(
                    g["generation_id"] == receipt["old_generation_id"]
                    and g["status"] == "FINALIZED"
                    and g["error_code"] is None
                    for g in receipt["after_finish"]["generations"]
                )
            receipt["classification"] = (
                "green"
                if all(
                    receipt[key]
                    for key in (
                        "retired_speech_terminal_gate",
                        "new_speech_finalized_gate",
                        "late_callbacks_durable_rows_unchanged",
                        "finalized_cleanup_preserved",
                    )
                )
                and all(
                    receipt[key]
                    for key in (
                        "finalizer_file_preserved",
                        "finalizer_asset_retained_gate",
                        "finalizer_generation_finalized_gate",
                        "free_old_callback_context_unchanged",
                        "free_old_callback_speeches_unchanged",
                    )
                    if key in receipt
                )
                else "red"
            )
            receipt["synthetic_provider_calls"] = providers.calls
        except BaseException as error:
            receipt.update(full.error_receipt(error))
            receipt["classification"] = "fixture_or_runtime_error"
            if "actor" in locals():
                receipt["exception_rows"] = await rows(factory, actor.state.match_id)
                receipt["exception_actor_status"] = actor.state.status
                receipt["exception_action_state"] = actor.state.action_state
        finally:
            providers.release.set()
            finalizer_release.set()
            await manager.close()
            await runtime.close()
            receipt["remaining_agent_runs"] = len(runtime._runs)
            receipt["remaining_host_runs"] = len(runtime._host_runs)
            receipt["remaining_manager_tasks"] = len(manager._background_tasks)
    return receipt


async def run(
    url: str,
    owned: full.OwnedPostgres,
    *,
    finalizer_control: bool,
    context_drift: bool = False,
    free_control: bool = False,
) -> dict[str, Any]:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(url),
        app_env="test",
        llm_key_encryption_key=SecretStr(base64.urlsafe_b64encode(b"x" * 32).decode()),
        tts_api_key=SecretStr("synthetic"),
        host_audio_storage_dir=str(owned.root / "host-audio"),
        agent_audio_storage_dir=str(owned.root / "agent-audio"),
    )
    database = Database(url)
    baseline = asyncio.all_tasks()
    try:
        cases = [
            await case(
                database.session_factory,
                settings,
                finalizer_control=finalizer_control,
                context_drift=context_drift,
                free_control=free_control,
            )
        ]
    finally:
        await database.dispose()
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() - baseline if not t.done()]
    return {
        "status": "passed"
        if all(c["classification"] == "green" for c in cases) and not pending
        else "failed",
        "cases": cases,
        "new_pending": len(pending),
        "production_settings_read": False,
        "real_media": False,
        "complete_match": False,
        "speech_status_manually_written": False,
    }


def main() -> int:
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("--free-only", action="store_true")
    args = parser.parse_args()
    controls = (
        ((True, False),) if args.free_only else ((False, False), (True, False), (False, True))
    )
    started = monotonic()
    receipt: dict[str, Any] = {
        "probe": "owned_pg_agent_pause_retirement",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixtures": [],
        "cases": [],
        "new_pending": 0,
        "production_settings_read": False,
        "real_media": False,
        "complete_match": False,
        "speech_status_manually_written": False,
    }
    for finalizer_control, context_drift in controls:
        owned = full.OwnedPostgres()
        fixture: dict[str, Any] = {
            "owned_run_label": str(uuid4()),
            "finalizer_control": finalizer_control,
            "context_drift": context_drift,
        }
        try:
            url = owned.start()
            owned.migrate(url)
            print(
                json.dumps(
                    {
                        "progress": "fresh_owned_postgres_started",
                        "finalizer_control": finalizer_control,
                        "context_drift": context_drift,
                        "source_sha256": receipt["source_sha256"],
                    }
                ),
                flush=True,
            )
            result = asyncio.run(
                run(
                    url,
                    owned,
                    finalizer_control=finalizer_control,
                    context_drift=context_drift,
                    free_control=args.free_only,
                )
            )
            receipt["cases"].extend(result["cases"])
            receipt["new_pending"] += result["new_pending"]
            fixture["runtime_status"] = result["status"]
        except BaseException as error:
            fixture.update(full.error_receipt(error))
        finally:
            fixture["owned_postgres_was_started"] = owned.started
            fixture["owned_postgres_and_audio_removed"] = owned.close()
            fixture["owned_postgres_stop_method"] = "pg_ctl exact data directory fast stop"
            fixture["owned_volume_cleanup_method"] = (
                "exact TemporaryDirectory cleanup (native PG, no Docker volume)"
            )
            receipt["fixtures"].append(fixture)
    receipt["owned_postgres_and_audio_removed"] = all(
        f["owned_postgres_and_audio_removed"] for f in receipt["fixtures"]
    )
    receipt["elapsed_ms"] = round((monotonic() - started) * 1000)
    receipt["status"] = (
        "passed"
        if len(receipt["cases"]) == len(controls)
        and all(c["classification"] == "green" for c in receipt["cases"])
        and not receipt["new_pending"]
        and receipt["owned_postgres_and_audio_removed"]
        else "failed"
    )
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
