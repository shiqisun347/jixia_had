from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from scripts.ops import spec308_voice_swap as swap
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.models import (
    AgentProfile,
    AuditLog,
    Match,
    ModelProfile,
    Room,
    Rule,
    User,
    VoiceProfile,
)
from test_auth_migration_integration import auth_database_url as auth_database_url

pytestmark = pytest.mark.integration


def _values(row) -> dict[str, object]:
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


@pytest_asyncio.fixture
async def resources(auth_database_url: str):
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        user = User(
            username="spec308-resource-test",
            username_normalized="spec308-resource-test",
            real_name="隔离资源测试",
            password_hash="synthetic-not-used-for-authentication",
            role="ADMIN",
        )
        model = ModelProfile(name="spec308-model", config_ref="spec308-model")
        voices = [
            VoiceProfile(
                id=voice_id,
                name=swap.NAMES[voice_id],
                kind="AGENT",
                provider_voice=provider,
                avatar_key="agent-01" if voice_id == swap.ZHIFENG_ID else "human-16",
                rate=1.0,
                playback_gain=1.1,
                chars_per_second=4.2,
                calibration_status="READY",
                calibrated_at=datetime(2026, 10, 16, tzinfo=UTC),
            )
            for voice_id, provider in swap.ORIGINAL.items()
        ]
        session.add_all([user, model, *voices])
        await session.flush()
        rules = [
            Rule(
                rule_key=f"spec308-{index}",
                version=1,
                name=f"spec308-rule-{index}",
                side_size=1 if index == 2 else 4,
                estimated_seconds=120,
                created_by=user.id,
                default_agent_model_profile_id=model.id,
                status="ARCHIVED" if index == 1 else "ENABLED",
                lifecycle_status="ARCHIVED" if index == 1 else "ENABLED",
                historical_read_only=index == 2,
            )
            for index in range(3)
        ]
        session.add_all(rules)
        await session.flush()
        for rule in rules:
            for voice in voices:
                session.add(
                    AgentProfile(
                        rule_id=rule.id,
                        name=voice.name,
                        model_profile_id=model.id,
                        voice_profile_id=voice.id,
                        status="DISABLED" if voice.id == swap.JIEWEN_ID else "ENABLED",
                        system_prompt="synthetic preserved system preference",
                        debater_prompt="synthetic preserved speech preference",
                        generation_params={"temperature": 0.42, "top_p": 0.8},
                    )
                )
        room = Room(
            code="308001",
            title="隔离资源历史房间",
            label="QA",
            topic_snapshot={"title": "synthetic fixture"},
            rule_id=rules[0].id,
            rule_snapshot={"stages": []},
            format_snapshot={
                "agents": [
                    {"voice": {"id": str(v.id), "provider_voice": v.provider_voice}} for v in voices
                ]
            },
            organizer_user_id=user.id,
            status="FINISHED",
        )
        session.add(room)
        await session.flush()
        match = Match(room_id=room.id, status="FINISHED", runtime_snapshot={})
        session.add(match)
        await session.flush()
        ids = {"room": room.id, "match": match.id, "editable_rule": rules[0].id}
    try:
        yield factory, ids
    finally:
        await engine.dispose()


async def _state(factory):
    async with factory() as session:
        return {
            "voices": {v.id: _values(v) for v in await session.scalars(select(VoiceProfile))},
            "agents": {v.id: _values(v) for v in await session.scalars(select(AgentProfile))},
            "rules": {v.id: _values(v) for v in await session.scalars(select(Rule))},
            "rooms": {v.id: _values(v) for v in await session.scalars(select(Room))},
            "matches": {v.id: _values(v) for v in await session.scalars(select(Match))},
            "swap_audits": await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "admin.spec308.voice_providers_swapped")
            ),
        }


def _providers(state):
    return {key: value["provider_voice"] for key, value in state["voices"].items()}


@pytest.mark.asyncio
async def test_forward_retry_and_explicit_rollback_preserve_identity_and_archived_rows(resources):
    factory, ids = resources
    before = await _state(factory)
    async with factory() as session, session.begin():
        result = await swap.swap_voices(session)
    assert result["status"] == "applied"
    after = await _state(factory)
    assert _providers(after) == swap.DESIRED
    assert after["agents"] == before["agents"]
    assert after["rooms"] == before["rooms"]
    assert after["matches"] == before["matches"]
    for key, previous in before["rules"].items():
        if key != ids["editable_rule"]:
            assert after["rules"][key] == previous
    assert after["rules"][ids["editable_rule"]]["config_revision"] == (
        before["rules"][ids["editable_rule"]]["config_revision"] + 2
    )
    for key, previous in before["voices"].items():
        for field in ("id", "name", "kind", "avatar_key", "rate", "playback_gain", "status"):
            assert after["voices"][key][field] == previous[field]
        assert after["voices"][key]["chars_per_second"] is None
        assert after["voices"][key]["calibration_status"] == "NOT_CALIBRATED"
        assert after["voices"][key]["calibrated_at"] is None
    assert after["swap_audits"] == 1

    async with factory() as session, session.begin():
        assert (await swap.swap_voices(session))["status"] == "already_applied"
    assert await _state(factory) == after
    async with factory() as session, session.begin():
        assert (await swap.swap_voices(session, rollback=True))["status"] == "applied"
    restored = await _state(factory)
    assert _providers(restored) == swap.ORIGINAL
    assert restored["agents"] == before["agents"]
    assert restored["swap_audits"] == 2
    async with factory() as session, session.begin():
        assert (await swap.swap_voices(session, rollback=True))["status"] == "already_applied"
    assert await _state(factory) == restored


@pytest.mark.asyncio
async def test_midflush_failure_rolls_back_all_provider_and_preference_writes(
    resources, monkeypatch
):
    factory, _ = resources
    before = await _state(factory)
    async with factory() as session:
        original_flush = session.flush
        flushes = 0

        async def fail_after_second_flush(*args, **kwargs):
            nonlocal flushes
            flushes += 1
            await original_flush(*args, **kwargs)
            if flushes == 2:
                raise RuntimeError("synthetic midflush failure")

        monkeypatch.setattr(session, "flush", fail_after_second_flush)
        with pytest.raises(RuntimeError, match="synthetic midflush failure"):
            async with session.begin():
                await swap.swap_voices(session)
        assert flushes == 2
    assert await _state(factory) == before


@pytest.mark.asyncio
async def test_provider_uniqueness_stays_immediate_and_cannot_be_bypassed(resources):
    factory, _ = resources
    async with factory() as session, session.begin():
        deferrable = await session.scalar(
            text(
                "SELECT condeferrable FROM pg_constraint "
                "WHERE conname='uq_voice_profiles_provider_voice'"
            )
        )
        assert deferrable is False
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    VoiceProfile(
                        name="synthetic duplicate",
                        kind="AGENT",
                        provider_voice=swap.ORIGINAL[swap.ZHIFENG_ID],
                        avatar_key="agent-02",
                    )
                )
                await session.flush()
        await swap.swap_voices(session)
    assert _providers(await _state(factory)) == swap.DESIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("active_entity", ["room", "match"])
async def test_any_nonterminal_room_or_match_rejects_swap_without_writes(resources, active_entity):
    factory, ids = resources
    async with factory() as session, session.begin():
        row = await session.get(Room if active_entity == "room" else Match, ids[active_entity])
        row.status = "WAITING" if active_entity == "room" else "RUNNING"
    before = await _state(factory)
    with pytest.raises(swap.SwapConflict, match="nonterminal_room_or_match"):
        async with factory() as session, session.begin():
            await swap.swap_voices(session)
    assert await _state(factory) == before


@pytest.mark.asyncio
async def test_provider_compare_and_swap_rejects_an_unexpected_resource_value(resources):
    factory, _ = resources
    async with factory() as session, session.begin():
        row = await session.get(VoiceProfile, swap.ZHIFENG_ID)
        row.provider_voice = "synthetic-third-voice"
    before = await _state(factory)
    with pytest.raises(swap.SwapConflict, match="voice_provider_conflict"):
        async with factory() as session, session.begin():
            await swap.swap_voices(session)
    assert await _state(factory) == before


@pytest.mark.asyncio
async def test_transaction_is_required_before_any_resource_write(resources):
    factory, _ = resources
    before = await _state(factory)
    async with factory() as session:
        with pytest.raises(swap.SwapConflict, match="transaction_required"):
            await swap.swap_voices(session)
    assert await _state(factory) == before
