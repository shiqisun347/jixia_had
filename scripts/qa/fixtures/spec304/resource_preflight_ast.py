"""Historical read-only resource preflight AST fixture; never an installer.

Only the original schema guard remains at the mutation boundary. Tests inject an
owned fake mutation callback; this file has no settings or database implementation.
All runtime dependencies are inert None placeholders and there is no main invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

json: Any = None
text: Any = None
select: Any = None
VoiceProfile: Any = None
Rule: Any = None
ModelProfile: Any = None
AgentProfile: Any = None
CONFERENCE_HOST_VOICE: Any = None
CONFERENCE_HOST_NAME: Any = None
is_host_reserved_voice: Any = None
rule_draft: Any = None
validate_rule_draft: Any = None
argparse: Any = None
load_settings: Any = None
Database: Any = None
verify_and_approve: Any = None


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(384)


async def plan(database):
    async with database.session_factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        assert (
            await session.scalar(
                text("SELECT count(*) FROM matches WHERE status NOT IN ('FINISHED','TERMINATED')")
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM rooms WHERE status NOT IN ('FINISHED','TERMINATED')")
            )
            == 0
        )
        voices = list((await session.scalars(select(VoiceProfile).order_by(VoiceProfile.id))).all())
        rules = list(
            (
                await session.scalars(
                    select(Rule)
                    .where(
                        Rule.side_size == 4,
                        Rule.historical_read_only.is_(False),
                        Rule.lifecycle_status != "ARCHIVED",
                    )
                    .order_by(Rule.id)
                )
            ).all()
        )
        selected = [
            rule
            for rule in rules
            if rule.default_agent_model_profile_id is not None
            and rule.host_voice_profile_id is not None
        ]
        canonical = [voice for voice in voices if voice.provider_voice == CONFERENCE_HOST_VOICE]
        assert not canonical or (
            len(canonical) == 1
            and canonical[0].kind == "HOST"
            and (canonical[0].name == CONFERENCE_HOST_NAME)
            and (canonical[0].rate == 1.0)
        ), "canonical_voice_conflict_before_any_mutation"
        enabled_models = set(
            (
                await session.scalars(
                    select(ModelProfile.id).where(ModelProfile.status == "ENABLED")
                )
            ).all()
        )
        assert all(rule.default_agent_model_profile_id in enabled_models for rule in selected), (
            "selected_rule_model_unavailable_before_any_mutation"
        )
        for rule in selected:
            draft = await rule_draft(session, rule)
            validate_rule_draft(draft)
            assert any(stage["stage_kind"] != "END" for stage in draft["stages"]), (
                "incomplete_rule_before_any_mutation"
            )
        reserved_voice_ids = {
            voice.id for voice in voices if is_host_reserved_voice(voice.provider_voice)
        }
        reserved_agents = list(
            (
                await session.scalars(
                    select(AgentProfile)
                    .where(AgentProfile.voice_profile_id.in_(reserved_voice_ids))
                    .order_by(AgentProfile.id)
                )
            ).all()
        )
        editable_rule_ids = {rule.id for rule in rules}
        return {
            "schema": list(
                (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
            ),
            "target_host": {"name": CONFERENCE_HOST_NAME, "provider_voice": CONFERENCE_HOST_VOICE},
            "voice_status_before": [
                {
                    "id": str(voice.id),
                    "kind": voice.kind,
                    "provider_voice": voice.provider_voice,
                    "status": voice.status,
                }
                for voice in voices
                if voice.kind == "HOST" or is_host_reserved_voice(voice.provider_voice)
            ],
            "reserved_agent_status_before": [
                {
                    "id": str(agent.id),
                    "rule_id": str(agent.rule_id),
                    "voice_profile_id": str(agent.voice_profile_id),
                    "status": agent.status,
                }
                for agent in reserved_agents
                if agent.rule_id in editable_rule_ids
            ],
            "historical_reserved_agents_left_unchanged": [
                {
                    "id": str(agent.id),
                    "rule_id": str(agent.rule_id) if agent.rule_id else None,
                    "status": agent.status,
                }
                for agent in reserved_agents
                if agent.rule_id not in editable_rule_ids
            ],
            "rules": [
                {
                    "id": str(rule.id),
                    "lifecycle_status": rule.lifecycle_status,
                    "host_voice_profile_id": str(rule.host_voice_profile_id),
                    "generation_id": str(rule.current_audio_generation_id)
                    if rule.current_audio_generation_id
                    else None,
                    "basic": {
                        "name": rule.name,
                        "description": rule.description,
                        "host_voice_profile_id": str(rule.host_voice_profile_id),
                        "default_agent_model_profile_id": str(rule.default_agent_model_profile_id),
                        "topic_policy": rule.topic_policy,
                        "postmatch_questionnaire_enabled": rule.postmatch_questionnaire_enabled,
                    },
                    "draft": await rule_draft(session, rule),
                }
                for rule in selected
            ],
            "incomplete_test_rules_left_unchanged": [
                str(rule.id) for rule in rules if rule not in selected
            ],
        }


async def configure(database, settings, before):
    assert before["schema"] == ["0047_speech_origin"]
    raise RuntimeError("fixture_requires_injected_mutation_boundary")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=["plan", "configure", "verify", "approve"], default="plan"
    )
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        if args.phase in {"plan", "configure"}:
            assert not args.state.exists(), "before_snapshot_must_not_be_overwritten"
            before = await plan(database)
            save(args.state, before)
            receipt = {
                "status": "passed",
                "phase": args.phase,
                "rules": len(before["rules"]),
                "incomplete_test_rules_left_unchanged": before[
                    "incomplete_test_rules_left_unchanged"
                ],
            }
            if args.phase == "configure":
                receipt.update(await configure(database, settings, before))
        else:
            before = json.loads(args.state.read_text())
            configured = json.loads(args.state.with_name("configure-receipt.json").read_text())
            receipt = await verify_and_approve(
                database, settings, before, configured, approve=args.phase == "approve"
            )
            receipt.update(status="passed", phase=args.phase)
        save(args.state.with_name(args.phase + "-receipt.json"), receipt)
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
    finally:
        await database.dispose()
