"""Explicit host-TTS/PostgreSQL risk probe; excluded from normal green gates.

Run this file explicitly against localhost jx_debate_qa_agent_faults with
RUN_DATABASE_INTEGRATION=1 and an independent TEST_DATABASE_URL. The actual
manifest, claim, worker, shared capture writer, audio decoder and authority rows
are used. Only TTS and optional writer storage are synthetic. The original
authority/error/locking assertions remain the approved Spec 300 acceptance.

Core regenerate_voice_preview is audited separately: it replaces the Ogg file
and releases its preview semaphore before awaiting capture; SUCCEEDED, READY
and its audit still await capture in the same completion transaction. This probe
chooses the Jobs path, whose completion also locks Rule/Generation/Asset/Task.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID, uuid4

import av
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import jx_core.data_capture.provider_writer as capture_module
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_projection import capture_projection
from jx_core.data_capture.provider_writer import ProviderCaptureWriter
from jx_core.models import (
    BackgroundTask,
    CallContentBlob,
    ExternalCall,
    HostAudioAsset,
    Rule,
    RuleAudioGeneration,
    RuleStage,
    User,
    VoiceProfile,
)
from jx_core.rules.audio import AudioConfiguration, create_generation, fingerprint
from jx_jobs.host_tts_worker import (  # pyright: ignore[reportMissingTypeStubs]
    audio_duration_ms,
    process_one_host_tts,
)
from jx_jobs.tts import TTSProviderError  # pyright: ignore[reportMissingTypeStubs]

pytestmark = pytest.mark.integration

CAPTURE_ENTRY_SECONDS = 0.8
OBSERVATION_SECONDS = 1.0
RESULT_WAIT_SECONDS = 0.55
CLEANUP_SECONDS = 1.0
ORIGINAL_ERROR = "tts_provider_failed"


@pytest.fixture(scope="module")
def probe_database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("explicit RUN_DATABASE_INTEGRATION=1 is required for this risk probe")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value or value == os.environ.get("DATABASE_URL"):
        pytest.fail("an independent TEST_DATABASE_URL is required")
    parsed = make_url(value)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "jx_debate_qa_agent_faults"
    ):
        pytest.fail("voice probes require the dedicated local QA PostgreSQL database")
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=SecretStr(value),
        app_env="test",
    )
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    # Migrations use only this explicit synthetic Settings, never dotenv.
    with patch("jx_core.config.load_settings", return_value=settings):
        command.upgrade(config, "head")
    return value


class SyntheticTTS:
    model = "synthetic-tts"

    def __init__(
        self, *, voice: str, suffix: str, provider_failure: bool, writer: ProviderCaptureWriter
    ) -> None:
        self.voice = voice
        self.provider_failure = provider_failure
        self.calls = 0
        self.output_path: Path | None = None
        self.capture = writer.new_capture()
        self.capture.capture_request(
            transport="WEBSOCKET",
            url="wss://synthetic.invalid/tts",
            body={"probe": suffix, "synthetic": True},
        )
        self.capture.capture_response(
            transport="WEBSOCKET",
            url="wss://synthetic.invalid/tts",
            body={"probe": suffix, "synthetic": True, "provider_failed": provider_failure},
        )

    async def synthesize_to_file(
        self, *, text: str, voice: str, rate: float, output_path: Path
    ) -> None:
        self.calls += 1
        assert text == "主持测试文本。" and voice == self.voice and rate == 1.0
        self.output_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Valid 100ms Ogg/Opus: production duration/checksum code is not mocked.
        with av.open(str(output_path), mode="w", format="ogg") as container:
            stream = container.add_stream(  # pyright: ignore[reportUnknownMemberType]
                "libopus", rate=48_000
            )
            stream.layout = "mono"
            for index in range(5):
                frame = av.AudioFrame(format="s16", layout="mono", samples=960)
                frame.sample_rate = 48_000
                frame.pts = index * 960
                frame.time_base = Fraction(1, 48_000)
                frame.planes[0].update(bytes(960 * 2))
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        if self.provider_failure:
            # A provider may fail after delivering some valid audio bytes.
            raise TTSProviderError(ORIGINAL_ERROR)

    def take_capture(self) -> ProviderCallCapture:
        return self.capture


@dataclass
class HostCaptureProbe:
    factory: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    storage_root: Path
    user_id: UUID
    voice_id: UUID
    rule_id: UUID
    generation_id: UUID
    asset_id: UUID
    task_id: UUID
    provider_voice: str
    suffix: str
    writer: ProviderCaptureWriter
    capture_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release_capture: asyncio.Event = field(default_factory=asyncio.Event)
    capture_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    workers: list[asyncio.Task[bool]] = field(default_factory=lambda: list[asyncio.Task[bool]]())
    blob_ids: list[UUID] = field(default_factory=lambda: list[UUID]())

    def start(self, client: SyntheticTTS) -> asyncio.Task[bool]:
        task = asyncio.create_task(
            process_one_host_tts(
                self.factory,
                client=client,
                storage_root=self.storage_root,
                capture_writer=self.writer,
            ),
            name="qa-host-capture-worker",
        )
        self.workers.append(task)
        return task

    async def stop_workers(self) -> None:
        for worker in self.workers:
            if not worker.done():
                worker.cancel()
        if self.workers:
            _, pending = await asyncio.wait(self.workers, timeout=CLEANUP_SECONDS)
            if pending:
                self.release_capture.set()
                pytest.fail("bounded probe worker cancellation did not finish")
            for worker in self.workers:
                if not worker.cancelled():
                    worker.exception()

    async def snapshot(self) -> dict[str, Any]:
        async with self.factory() as session:
            rule = await session.get(Rule, self.rule_id)
            generation = await session.get(RuleAudioGeneration, self.generation_id)
            asset = await session.get(HostAudioAsset, self.asset_id)
            task = await session.get(BackgroundTask, self.task_id)
            voice = await session.get(VoiceProfile, self.voice_id)
            call = await session.scalar(
                select(ExternalCall).where(
                    ExternalCall.source_kind == "HOST_AUDIO",
                    ExternalCall.source_resource_id == str(self.asset_id),
                )
            )
            assert rule and generation and asset and task and voice and call
            raw = await capture_projection(session, call)
            return {
                "rule_status": rule.status,
                "generation_status": generation.status,
                "generation_completed": generation.completed_at is not None,
                "counts": [generation.total_count, generation.ready_count, generation.failed_count],
                "asset_status": asset.status,
                "asset_error": asset.error_code,
                "asset_published": asset.storage_path is not None,
                "asset_duration_ms": asset.duration_ms,
                "asset_audio_bytes": asset.byte_count,
                "task_status": task.status,
                "task_error": task.error_code,
                "attempts": task.attempts,
                "max_attempts": task.max_attempts,
                "lease_held": task.lease_until is not None,
                "call_status": call.status,
                "call_error": call.error_code,
                "call_completed": call.completed_at is not None,
                "capture_error": raw["capture_error_code"],
                "capture_statuses": [raw["request_capture_status"], raw["response_capture_status"]],
                "voice_calibration": voice.calibration_status,
                "ogg_files": len(list(self.storage_root.rglob("*.ogg"))),
            }

    async def authority_locks(self) -> dict[str, bool]:
        async with self.factory() as session:
            call_id = await session.scalar(
                select(ExternalCall.id).where(
                    ExternalCall.source_kind == "HOST_AUDIO",
                    ExternalCall.source_resource_id == str(self.asset_id),
                )
            )
        assert call_id is not None
        acquired: dict[str, bool] = {}
        for table, row_id in (
            ("rules", self.rule_id),
            ("rule_audio_generations", self.generation_id),
            ("host_audio_assets", self.asset_id),
            ("background_tasks", self.task_id),
            ("external_calls", call_id),
        ):
            try:
                async with self.factory() as session:
                    async with session.begin():
                        await session.execute(text("SET LOCAL lock_timeout = '50ms'"))
                        # Constant table names, no updates or FK KEY SHARE conflicts.
                        await session.execute(
                            text(f"SELECT id FROM {table} WHERE id = :id FOR NO KEY UPDATE"),
                            {"id": row_id},
                        )
            except DBAPIError as error:
                if getattr(error.orig, "sqlstate", None) != "55P03":
                    raise
                acquired[table] = False
            else:
                acquired[table] = True
        return acquired


@pytest.fixture
async def host_capture_probe(
    probe_database_url: str, tmp_path: Path
) -> AsyncGenerator[HostCaptureProbe, None]:
    engine = create_async_engine(probe_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    writer = ProviderCaptureWriter(probe_database_url, service="jx-jobs")
    await writer.start()
    user_id, voice_id, rule_id = (uuid4() for _ in range(3))
    suffix = uuid4().hex
    provider_voice = f"host-capture-{suffix}"
    probe: HostCaptureProbe | None = None
    try:
        async with factory() as session:
            async with session.begin():
                # Global claim/reconcile must not touch another test's live fixture.
                assert (
                    await session.scalar(
                        select(BackgroundTask.id)
                        .where(
                            BackgroundTask.task_type == "HOST_TTS",
                            BackgroundTask.status.in_(["PENDING", "RUNNING"]),
                        )
                        .limit(1)
                    )
                    is None
                ), "exclusive QA database has another active host task"
                assert (
                    await session.scalar(
                        select(RuleAudioGeneration.id)
                        .where(
                            RuleAudioGeneration.is_current.is_(True),
                            RuleAudioGeneration.status.in_(["QUEUED", "RUNNING"]),
                        )
                        .limit(1)
                    )
                    is None
                ), "exclusive QA database has another active host generation"
                session.add_all(
                    [
                        User(
                            id=user_id,
                            username=f"hc-{suffix[:20]}",
                            username_normalized=f"hc-{suffix[:20]}",
                            real_name="Synthetic host admin",
                            password_hash="not-used",
                        ),
                        VoiceProfile(
                            id=voice_id,
                            name=f"host-capture-{suffix}",
                            kind="HOST",
                            provider_voice=provider_voice,
                            rate=1.0,
                            status="ENABLED",
                        ),
                    ]
                )
                await session.flush()
                rule = Rule(
                    id=rule_id,
                    rule_key=f"host-capture-{suffix}",
                    version=1,
                    name="Synthetic host rule",
                    side_size=4,
                    estimated_seconds=60,
                    created_by=user_id,
                    host_voice_profile_id=voice_id,
                    lifecycle_status="DISABLED",
                    config_revision=3,
                )
                session.add(rule)
                await session.flush()
                session.add_all(
                    [
                        RuleStage(
                            rule_id=rule_id,
                            position=1,
                            name="准备",
                            stage_kind="PREPARATION",
                            duration_seconds=60,
                            start_host_text="主持测试文本。",
                        ),
                        RuleStage(rule_id=rule_id, position=2, name="结束", stage_kind="END"),
                    ]
                )
                await session.flush()
                generation = await create_generation(
                    session,
                    rule=rule,
                    actor_user_id=user_id,
                    configuration=AudioConfiguration(
                        model=SyntheticTTS.model,
                        websocket_url="wss://synthetic.invalid/tts",
                        storage_root=str(tmp_path),
                    ),
                )
                asset = (
                    await session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                    )
                ).one()
                assert asset.active_task_id is not None
                task = await session.get(BackgroundTask, asset.active_task_id)
                assert task and task.max_attempts == 2 and task.attempts == 0
                assert generation.is_current and rule.current_audio_generation_id == generation.id
                assert generation.manifest_hash == fingerprint(generation.manifest)
                assert asset.input_hash == fingerprint(asset.input_snapshot)
                assert task.payload == {
                    "rule_id": str(rule_id),
                    "generation_id": str(generation.id),
                    "asset_id": str(asset.id),
                    "manifest_hash": generation.manifest_hash,
                    "config_revision": 3,
                }
                probe = HostCaptureProbe(
                    factory,
                    engine,
                    tmp_path,
                    user_id,
                    voice_id,
                    rule_id,
                    generation.id,
                    asset.id,
                    task.id,
                    provider_voice,
                    suffix,
                    writer,
                )
        assert probe is not None
        yield probe
    finally:
        if probe is not None:
            await probe.stop_workers()
            probe.release_capture.set()
        await writer.close()
        try:
            async with asyncio.timeout(CLEANUP_SECONDS):
                async with factory() as session:
                    async with session.begin():
                        await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                        if probe is not None:
                            await session.execute(
                                delete(ExternalCall).where(
                                    ExternalCall.source_kind == "HOST_AUDIO",
                                    ExternalCall.source_resource_id == str(probe.asset_id),
                                )
                            )
                            await session.execute(
                                delete(BackgroundTask).where(
                                    BackgroundTask.id == probe.task_id,
                                )
                            )
                        await session.execute(delete(Rule).where(Rule.id == rule_id))
                        await session.execute(
                            delete(VoiceProfile).where(VoiceProfile.id == voice_id)
                        )
                        await session.execute(delete(User).where(User.id == user_id))
                        if probe is not None and probe.blob_ids:
                            # Captures contain this fixture's unique suffix; never shared payloads.
                            await session.execute(
                                delete(CallContentBlob).where(
                                    CallContentBlob.id.in_(probe.blob_ids),
                                )
                            )
        finally:
            async with asyncio.timeout(CLEANUP_SECONDS):
                await engine.dispose()


def inject_capture_storage(
    probe: HostCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["normal", "raise", "hang"],
) -> None:
    original = capture_module._store_encoded_blob  # pyright: ignore[reportPrivateUsage]

    async def synthetic_storage(
        session: AsyncSession,
        kind: str,
        raw: bytes,
        prepared: tuple[bytes, str],
    ) -> UUID:
        probe.capture_entered.set()
        if mode == "raise":
            raise RuntimeError("synthetic_capture_storage_failed")
        if mode == "hang":
            try:
                await probe.release_capture.wait()
            except asyncio.CancelledError:
                probe.capture_cancelled.set()
                raise
        blob_id = await original(session, kind, raw, prepared)
        probe.blob_ids.append(blob_id)
        return blob_id

    monkeypatch.setattr(capture_module, "_store_encoded_blob", synthetic_storage)


def assert_authority_result(snapshot: dict[str, Any], *, provider_failure: bool) -> None:
    assert snapshot["attempts"] == 1 and snapshot["max_attempts"] == 2
    assert snapshot["lease_held"] is False and snapshot["call_completed"] is True
    assert snapshot["voice_calibration"] == "NOT_CALIBRATED"
    if provider_failure:
        assert snapshot["call_status"] == "FAILED" and snapshot["call_error"] == ORIGINAL_ERROR
        assert snapshot["task_status"] == "PENDING" and snapshot["task_error"] == ORIGINAL_ERROR
        assert snapshot["asset_status"] == "PENDING" and snapshot["asset_error"] == ORIGINAL_ERROR
        assert snapshot["generation_status"] == "QUEUED" and snapshot["counts"] == [1, 0, 0]
        assert snapshot["asset_published"] is False and snapshot["generation_completed"] is False
    else:
        assert snapshot["call_status"] == "SUCCEEDED" and snapshot["call_error"] is None
        assert snapshot["task_status"] == "SUCCEEDED" and snapshot["task_error"] is None
        assert snapshot["asset_status"] == "READY" and snapshot["asset_error"] is None
        assert snapshot["asset_published"] is True and snapshot["generation_completed"] is True
        assert snapshot["generation_status"] == "SUCCEEDED" and snapshot["counts"] == [1, 1, 0]
        assert snapshot["asset_duration_ms"] > 0 and snapshot["asset_audio_bytes"] > 0
        assert snapshot["rule_status"] == "READY"


@pytest.mark.parametrize(
    "provider_failure", [False, True], ids=["provider-success", "provider-error"]
)
@pytest.mark.parametrize("capture_mode", ["normal", "raise"], ids=["capture-ok", "storage-error"])
async def test_host_tts_storage_error_preserves_real_authority_result(
    host_capture_probe: HostCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    provider_failure: bool,
    capture_mode: Literal["normal", "raise"],
) -> None:
    probe = host_capture_probe
    inject_capture_storage(probe, monkeypatch, capture_mode)
    client = SyntheticTTS(
        voice=probe.provider_voice,
        suffix=probe.suffix,
        provider_failure=provider_failure,
        writer=probe.writer,
    )
    worker = probe.start(client)
    async with asyncio.timeout(OBSERVATION_SECONDS):
        assert await worker is True
        await probe.capture_entered.wait()
        while probe.writer.stats()["items"]:
            await asyncio.sleep(0.005)
        snapshot = await probe.snapshot()
        locks = await probe.authority_locks()
    print(
        json.dumps(
            {
                "case": f"{capture_mode}/{provider_failure}",
                "authority": snapshot,
                "authority_lock_acquired": locks,
            },
            sort_keys=True,
        )
    )
    assert client.calls == 1 and client.output_path is not None
    assert audio_duration_ms(client.output_path) == 100
    assert_authority_result(snapshot, provider_failure=provider_failure)
    assert all(locks.values())
    if capture_mode == "raise":
        assert snapshot["capture_error"] == "capture_not_persisted"
        assert snapshot["capture_statuses"] == ["UNAVAILABLE", "UNAVAILABLE"]
        assert probe.writer.stats()["persist_failed"] == 1
        assert probe.writer.stats()["retry"] == 1
    else:
        assert snapshot["capture_error"] is None
        assert snapshot["capture_statuses"] == ["COMPLETE", "COMPLETE"]
    assert probe.writer.stats()["retained_bytes"] == 0


@pytest.mark.parametrize(
    "provider_failure", [False, True], ids=["provider-success", "provider-error"]
)
async def test_host_tts_capture_hang_cannot_hold_authority_or_original_error(
    host_capture_probe: HostCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    provider_failure: bool,
) -> None:
    probe = host_capture_probe
    inject_capture_storage(probe, monkeypatch, "hang")
    client = SyntheticTTS(
        voice=probe.provider_voice,
        suffix=probe.suffix,
        provider_failure=provider_failure,
        writer=probe.writer,
    )
    worker = probe.start(client)
    await asyncio.wait_for(probe.capture_entered.wait(), CAPTURE_ENTRY_SECONDS)
    started = monotonic()
    try:
        async with asyncio.timeout(OBSERVATION_SECONDS):
            done, _ = await asyncio.wait({worker}, timeout=RESULT_WAIT_SECONDS)
            snapshot = await probe.snapshot()
            locks = await probe.authority_locks()
    finally:
        # External cancellation is a probe cleanup, not a product success.
        await probe.stop_workers()
    async with asyncio.timeout(OBSERVATION_SECONDS):
        after_cancel = await probe.snapshot()
        after_cancel_locks = await probe.authority_locks()
    print(
        json.dumps(
            {
                "case": f"hang/{provider_failure}",
                "completed_before_cancel": bool(done),
                "observation_ms": round((monotonic() - started) * 1000, 2),
                "authority_before_cancel": snapshot,
                "authority_lock_acquired": locks,
                "capture_cancel_received": probe.capture_cancelled.is_set(),
                "authority_after_cancel": after_cancel,
                "authority_lock_acquired_after_cancel": after_cancel_locks,
            },
            sort_keys=True,
        )
    )
    assert client.calls == 1 and client.output_path is not None
    assert audio_duration_ms(client.output_path) == 100
    assert worker.done() and all(after_cancel_locks.values()), "probe must cleanly release DB locks"
    # TechDesign §19: optional diagnostics cannot delay authority convergence.
    # Retain the red requirement; do not xfail or count cancellation as success.
    assert done, "optional capture blocked host-TTS authority completion within bounded observation"
    assert all(locks.values()), "optional capture held authority row locks"
    assert_authority_result(snapshot, provider_failure=provider_failure)


async def test_host_tts_stale_claim_discards_capture_and_returns_budget(
    host_capture_probe: HostCaptureProbe,
) -> None:
    probe = host_capture_probe

    class StaleTTS(SyntheticTTS):
        async def synthesize_to_file(
            self, *, text: str, voice: str, rate: float, output_path: Path
        ) -> None:
            await super().synthesize_to_file(
                text=text, voice=voice, rate=rate, output_path=output_path
            )
            async with probe.factory() as session, session.begin():
                asset = await session.get(HostAudioAsset, probe.asset_id, with_for_update=True)
                assert asset is not None
                asset.active_task_id = None

    client = StaleTTS(
        voice=probe.provider_voice,
        suffix=probe.suffix,
        provider_failure=False,
        writer=probe.writer,
    )
    async with asyncio.timeout(OBSERVATION_SECONDS):
        assert await probe.start(client) is True
        snapshot = await probe.snapshot()
    assert snapshot["call_status"] == "FAILED"
    assert snapshot["call_error"] == "tts_generation_stale"
    assert snapshot["asset_published"] is False
    assert probe.writer.stats().get("accepted", 0) == 0
    assert probe.writer.stats()["items"] == probe.writer.stats()["retained_bytes"] == 0


async def test_host_tts_authority_rollback_never_submits_capture_and_returns_budget(
    host_capture_probe: HostCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jx_jobs.host_tts_worker as worker_module  # pyright: ignore[reportMissingTypeStubs]

    probe = host_capture_probe
    original = worker_module.aggregate
    aggregate_calls = 0

    async def fail_result_transaction(
        session: AsyncSession, rule: Rule, generation: RuleAudioGeneration
    ):
        nonlocal aggregate_calls
        aggregate_calls += 1
        if aggregate_calls == 2:
            # prepare has transferred the scalar bytes lease, but commit has
            # not happened. A genuine authority error must roll back and escape.
            raise RuntimeError("synthetic_authority_commit_failed")
        return await original(session, rule, generation)

    monkeypatch.setattr(worker_module, "aggregate", fail_result_transaction)
    client = SyntheticTTS(
        voice=probe.provider_voice,
        suffix=probe.suffix,
        provider_failure=False,
        writer=probe.writer,
    )
    with pytest.raises(RuntimeError, match="synthetic_authority_commit_failed"):
        async with asyncio.timeout(OBSERVATION_SECONDS):
            await probe.start(client)
    snapshot = await probe.snapshot()
    assert aggregate_calls == 2
    assert snapshot["call_status"] == "STARTED" and snapshot["call_completed"] is False
    assert snapshot["task_status"] == "RUNNING" and snapshot["lease_held"] is True
    assert snapshot["asset_published"] is False
    assert probe.writer.stats().get("accepted", 0) == 0
    assert probe.writer.stats()["items"] == probe.writer.stats()["retained_bytes"] == 0
