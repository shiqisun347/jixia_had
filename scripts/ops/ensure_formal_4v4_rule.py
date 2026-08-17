"""Create and finalize the reusable formal 4v4 rule without exposing secrets."""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jx_core.config import Settings
from jx_core.database import Database
from jx_core.models import HostAudioAsset, Rule, RuleStage, User, VoiceProfile
from jx_core.rules.formal_4v4 import (
    build_formal_4v4_draft,
    expected_formal_4v4_host_copy,
)
from jx_core.rules.schemas import RuleCreate
from jx_core.rules.service import RuleService

RULE_KEY = "formal-4v4-standard"


async def _host_copy_matches(session: AsyncSession, rule_id: UUID) -> bool:
    rows = list(
        (
            await session.execute(
                select(
                    RuleStage.name,
                    RuleStage.start_host_text,
                    RuleStage.end_host_text,
                )
                .where(RuleStage.rule_id == rule_id)
                .order_by(RuleStage.position)
            )
        ).all()
    )
    actual = [(name, start or "", end or "") for name, start, end in rows]
    return actual == expected_formal_4v4_host_copy()


async def _run(finalize: bool) -> None:
    settings = Settings()
    database = Database(settings.database_url.get_secret_value())
    try:
        async with database.session_factory() as session:
            admin = await session.scalar(select(User).where(User.username_normalized == "admin"))
            if admin is None:
                raise RuntimeError("admin_unavailable")
            admin_id = admin.id
            existing = await session.scalar(
                select(Rule).where(Rule.rule_key == RULE_KEY).order_by(Rule.version.desc())
            )
            copy_matches = existing is not None and await _host_copy_matches(session, existing.id)
            if existing is None or not copy_matches:
                host_voice = await session.scalar(
                    select(VoiceProfile).where(
                        VoiceProfile.kind == "HOST", VoiceProfile.status == "ENABLED"
                    )
                )
                if host_voice is None:
                    raise RuntimeError("host_voice_unavailable")
                host_voice_id = host_voice.id
                await session.rollback()
                rule = await RuleService().create_rule(
                    session,
                    creator_user_id=admin_id,
                    payload=RuleCreate(
                        rule_key=RULE_KEY,
                        host_voice_profile_id=host_voice_id,
                        draft=build_formal_4v4_draft(),
                    ),
                )
                output = {
                    "id": str(rule.id),
                    "version": rule.version,
                    "status": rule.status,
                    "action": "created",
                }
            else:
                existing_id = existing.id
                output = {
                    "id": str(existing_id),
                    "version": existing.version,
                    "status": existing.status,
                    "action": "existing",
                }
            if finalize and existing is not None and copy_matches:
                if existing.status == "ENABLED":
                    output["finalize"] = "already_enabled"
                else:
                    assets_ready = await session.scalar(
                        select(HostAudioAsset.id)
                        .where(
                            HostAudioAsset.rule_id == existing_id,
                            HostAudioAsset.status != "READY",
                        )
                        .limit(1)
                    )
                    if assets_ready is not None:
                        output["finalize"] = "waiting_for_host_audio"
                    else:
                        await session.rollback()
                        ready_rule_id = existing_id
                        if existing.status == "GENERATING_AUDIO":
                            reviewed = await RuleService().review_audio(
                                session,
                                rule_id=existing_id,
                                actor_user_id=admin_id,
                            )
                            ready_rule_id = reviewed.id
                        await RuleService().enable_rule(
                            session,
                            rule_id=ready_rule_id,
                            actor_user_id=admin_id,
                        )
                        output["finalize"] = "enabled"
                        output["status"] = "ENABLED"
            print(json.dumps(output, ensure_ascii=False))
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args.finalize))


if __name__ == "__main__":
    main()
