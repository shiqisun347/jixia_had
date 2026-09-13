"""Safely switch enabled LLM model resources without exposing secrets."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import func, select, update

from jx_core.config import Settings
from jx_core.database import Database
from jx_core.models import AgentProfile, ModelProfile

TARGET_MODEL = "deepseek-v4-flash-0731"


async def run(*, apply: bool, model_id: str, target: str) -> None:
    settings = Settings()
    database = Database(settings.database_url.get_secret_value())
    try:
        async with database.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(
                            ModelProfile.id,
                            ModelProfile.name,
                            ModelProfile.model_id,
                            func.count(AgentProfile.id),
                        )
                        .outerjoin(AgentProfile, AgentProfile.model_profile_id == ModelProfile.id)
                        .where(ModelProfile.status == "ENABLED")
                        .group_by(ModelProfile.id, ModelProfile.name, ModelProfile.model_id)
                        .order_by(ModelProfile.name)
                    )
                ).all()
            )
            result = [
                {
                    "name": str(name),
                    "old_model_id": old or "",
                    "new_model_id": target,
                    "agent_profile_references": int(reference_count),
                }
                for _id, name, old, reference_count in rows
                if model_id == "*" or old == model_id
            ]
            if apply and result:
                ids = [row[0] for row in rows if model_id == "*" or row[2] == model_id]
                await session.execute(
                    update(ModelProfile)
                    .where(ModelProfile.id.in_(ids), ModelProfile.status == "ENABLED")
                    .values(model_id=target)
                )
                await session.commit()
            print(
                json.dumps(
                    {"applied": apply, "target": target, "models": result},
                    ensure_ascii=False,
                )
            )
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the enabled model resources")
    parser.add_argument(
        "--model-id",
        default="*",
        help="limit to this current model ID; default: all enabled",
    )
    parser.add_argument("--target", default=TARGET_MODEL, help="target provider model ID")
    args = parser.parse_args()
    if not args.target.strip():
        parser.error("--target cannot be empty")
    asyncio.run(run(apply=args.apply, model_id=args.model_id, target=args.target.strip()))


if __name__ == "__main__":
    main()
