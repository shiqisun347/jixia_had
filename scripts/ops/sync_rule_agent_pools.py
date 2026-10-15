"""Preview or repair missing voice/rule Agent pairs without changing existing preferences."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import func, select

from jx_core.config import load_settings
from jx_core.database import Database
from jx_core.models import AgentProfile, Match, Rule, VoiceProfile
from jx_core.rules.agent_pool import lock_agent_pool, sync_agent_pools


async def run(*, apply: bool) -> dict[str, object]:
    database = Database(load_settings().database_url_value)
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await lock_agent_pool(session)
                rules = list(
                    (
                        await session.scalars(
                            select(Rule)
                            .where(
                                Rule.side_size == 4,
                                Rule.historical_read_only.is_(False),
                                Rule.status != "ARCHIVED",
                            )
                            .order_by(Rule.id)
                        )
                    ).all()
                )
                voices = list(
                    (
                        await session.scalars(
                            select(VoiceProfile).where(
                                VoiceProfile.kind == "AGENT",
                                VoiceProfile.status == "ENABLED",
                            )
                        )
                    ).all()
                )
                agents = list((await session.scalars(select(AgentProfile))).all())
                pairs = {(a.rule_id, a.voice_profile_id) for a in agents}
                eligible = [r for r in rules if r.default_agent_model_profile_id is not None]
                missing = [
                    (r.id, v.id) for r in eligible for v in voices if (r.id, v.id) not in pairs
                ]
                original = {
                    a.id: (a.status, a.model_profile_id, dict(a.generation_params)) for a in agents
                }
                report: dict[str, object] = {
                    "apply": apply,
                    "enabled_agent_voices": len(voices),
                    "eligible_rules": len(eligible),
                    "missing_pairs": len(missing),
                    "rules_without_default_model": [
                        str(r.id) for r in rules if r.default_agent_model_profile_id is None
                    ],
                }
                if apply:
                    active = await session.scalar(
                        select(func.count())
                        .select_from(Match)
                        .where(Match.status.not_in({"FINISHED", "TERMINATED"}))
                    )
                    if active:
                        raise RuntimeError("nonterminal_matches_block_repair")
                    added = await sync_agent_pools(session)
                    if added != len(missing):
                        raise RuntimeError("repair_count_mismatch")
                    for agent in agents:
                        if original[agent.id] != (
                            agent.status,
                            agent.model_profile_id,
                            dict(agent.generation_params),
                        ):
                            raise RuntimeError("existing_agent_configuration_changed")
                    report["added_agents"] = added
                    report["existing_preferences_preserved"] = True
            return report
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the reviewed missing pairs")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"failed": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
