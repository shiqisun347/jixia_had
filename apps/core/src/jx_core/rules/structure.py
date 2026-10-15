"""Read and validate the complete finite stage structure, including END text."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.errors import AuthError
from ..models import Rule, RuleStage, StageAction, StagePromptTemplate
from .validation import RuleValidationError, validate_rule_draft


async def rule_draft(session: AsyncSession, rule: Rule) -> dict[str, Any]:
    stages = list(
        (
            await session.scalars(
                select(RuleStage).where(RuleStage.rule_id == rule.id).order_by(RuleStage.position)
            )
        ).all()
    )
    ids = [stage.id for stage in stages]
    actions = (
        list(
            (
                await session.scalars(
                    select(StageAction)
                    .where(StageAction.stage_id.in_(ids))
                    .order_by(StageAction.position)
                )
            ).all()
        )
        if ids
        else []
    )
    prompts = (
        list(
            (
                await session.scalars(
                    select(StagePromptTemplate).where(StagePromptTemplate.stage_id.in_(ids))
                )
            ).all()
        )
        if ids
        else []
    )
    return {
        "name": rule.name,
        "description": rule.description,
        "side_size": rule.side_size,
        "stages": [
            {
                "name": stage.name,
                "stage_kind": stage.stage_kind,
                "duration_seconds": stage.duration_seconds,
                "start_host_text": stage.start_host_text,
                "end_host_text": stage.end_host_text,
                "parameters": stage.parameters,
                "actions": [
                    {
                        "action_kind": action.action_kind,
                        "side": action.side,
                        "seat_no": action.seat_no,
                        "duration_seconds": action.duration_seconds,
                        "parameters": action.parameters,
                    }
                    for action in actions
                    if action.stage_id == stage.id
                ],
                **{
                    "speech_prompt"
                    if prompt.purpose == "SPEECH"
                    else "decision_prompt": prompt.template_text
                    for prompt in prompts
                    if prompt.stage_id == stage.id
                },
            }
            for stage in stages
        ],
    }


async def validate_structure(session: AsyncSession, rule: Rule) -> None:
    await session.flush()
    try:
        snapshot = validate_rule_draft(await rule_draft(session, rule))
    except (RuleValidationError, ValueError, TypeError) as error:
        raise AuthError("rule_invalid", {"stages": str(error)}) from error
    rule.estimated_seconds = int(snapshot["estimated_seconds"])
