"""Actual resource-tool AST with synthetic read-only DB, no settings or SQL IO."""

from __future__ import annotations

import ast
import asyncio
import json
import stat
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from jx_core.models import AgentProfile, ModelProfile, Rule, VoiceProfile
from jx_core.rules.formal_4v4 import build_formal_4v4_draft
from jx_core.rules.host_voice import (
    CONFERENCE_HOST_NAME,
    CONFERENCE_HOST_VOICE,
    is_host_reserved_voice,
)
from jx_core.rules.validation import validate_rule_draft

SOURCE = Path("profile_output/workflow_candidate/spec304/conference_resources.py")


def execute_resource_main(
    tmp_path: Path,
    *,
    phase: str,
    schema: str = "0046_provider_capture_snapshots",
    fault: str = "none",
) -> dict[str, Any]:
    tree = ast.parse(SOURCE.read_text())
    functions: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"save", "plan", "configure", "main"}
    ]
    namespace: dict[str, Any] = {
        "json": json,
        "select": select,
        "text": text,
        "Path": Path,
        "VoiceProfile": VoiceProfile,
        "Rule": Rule,
        "ModelProfile": ModelProfile,
        "AgentProfile": AgentProfile,
        "CONFERENCE_HOST_NAME": CONFERENCE_HOST_NAME,
        "CONFERENCE_HOST_VOICE": CONFERENCE_HOST_VOICE,
        "is_host_reserved_voice": is_host_reserved_voice,
        "validate_rule_draft": validate_rule_draft,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), namespace)
    events: list[str] = []
    statements: list[str] = []
    model_id, host_id = uuid4(), uuid4()
    rule = SimpleNamespace(
        id=uuid4(),
        lifecycle_status="ENABLED",
        host_voice_profile_id=host_id,
        default_agent_model_profile_id=model_id,
        current_audio_generation_id=uuid4(),
        name="Owned rule",
        description="Owned synthetic rule",
        topic_policy="BOTH",
        postmatch_questionnaire_enabled=False,
    )
    voice = SimpleNamespace(
        id=host_id,
        kind="AGENT" if fault == "canonical_conflict" else "HOST",
        name=CONFERENCE_HOST_NAME,
        provider_voice=CONFERENCE_HOST_VOICE,
        rate=1.0,
        status="ENABLED",
    )
    reserved_agents = [
        SimpleNamespace(id=uuid4(), rule_id=rule.id, voice_profile_id=host_id, status="ENABLED"),
        SimpleNamespace(id=uuid4(), rule_id=None, voice_profile_id=host_id, status="ENABLED"),
    ]

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def execute(self, query: Any) -> None:
            statements.append(str(query))
            assert str(query) == "SET TRANSACTION READ ONLY"
            events.append("readonly")

        async def scalar(self, query: Any) -> int:
            statements.append(str(query))
            assert str(query).startswith("SELECT count(*)")
            return 0

        async def scalars(self, query: Any) -> Any:
            statements.append(str(query))
            if not hasattr(query, "column_descriptions"):
                result = [schema]
            else:
                entity = query.column_descriptions[0]["entity"]
                if entity is VoiceProfile:
                    result = [voice]
                elif entity is Rule:
                    result = [rule]
                elif entity is AgentProfile:
                    result = reserved_agents if fault == "reserved_profiles" else []
                else:
                    result = [] if fault == "model_unavailable" else [model_id]
            return SimpleNamespace(all=lambda: result)

    class Database:
        def __init__(self, _: str) -> None:
            pass

        def session_factory(self) -> Session:
            return Session()

        async def dispose(self) -> None:
            events.append("disposed")

    async def draft(_: Any, __: Any) -> Any:
        if fault == "invalid_draft":
            return {"name": "Invalid", "side_size": 4, "stages": []}
        return deepcopy(build_formal_4v4_draft())

    state = tmp_path / "before.json"
    if fault == "existing_state":
        state.write_text("existing owned evidence")

    async def configure(_: Any, __: Any, before: dict[str, Any]) -> dict[str, Any]:
        # This is the first possible mutation boundary, never a real API call.
        assert state.is_file() and stat.S_IMODE(state.stat().st_mode) == 0o600
        assert statements[0] == "SET TRANSACTION READ ONLY"
        assert before["schema"] == [schema]
        events.append("configure")
        return {"historical_rooms_untouched": True}

    class Parser:
        def add_argument(self, *_: Any, **__: Any) -> None:
            pass

        def parse_args(self) -> Any:
            return SimpleNamespace(phase=phase, state=state)

    namespace.update(
        argparse=SimpleNamespace(ArgumentParser=Parser),
        load_settings=lambda: SimpleNamespace(database_url_value="synthetic-owned-db"),
        Database=Database,
        rule_draft=draft,
    )
    if schema == "0047_speech_origin":
        namespace["configure"] = configure
    caught: str | None = None
    try:
        asyncio.run(namespace["main"]())
    except Exception as error:
        caught = type(error).__name__
    return {"caught": caught, "events": events, "statements": statements, "state": state}


def test_production_0046_plan_is_read_only_and_does_not_select_candidate_speech(
    tmp_path: Path,
) -> None:
    result = execute_resource_main(tmp_path, phase="plan")
    assert result["caught"] is None
    assert result["events"] == ["readonly", "disposed"]
    assert result["state"].is_file()
    assert stat.S_IMODE(result["state"].stat().st_mode) == 0o600
    assert not any("speech" in query.lower() for query in result["statements"])
    assert json.loads(result["state"].read_text())["schema"] == ["0046_provider_capture_snapshots"]


@pytest.mark.parametrize("phase", ["plan", "configure"])
def test_exclusive_before_state_blocks_all_database_access_and_mutation(
    tmp_path: Path, phase: str
) -> None:
    result = execute_resource_main(tmp_path, phase=phase, fault="existing_state")
    assert result["caught"] == "AssertionError"
    assert result["events"] == ["disposed"] and not result["statements"]
    assert result["state"].read_text() == "existing owned evidence"


@pytest.mark.parametrize("fault", ["canonical_conflict", "model_unavailable", "invalid_draft"])
def test_all_resource_prechecks_fail_before_state_or_any_mutation(
    tmp_path: Path, fault: str
) -> None:
    result = execute_resource_main(
        tmp_path, phase="configure", schema="0047_speech_origin", fault=fault
    )
    assert result["caught"] is not None
    assert "configure" not in result["events"] and not result["state"].exists()
    assert result["events"][0] == "readonly" and result["events"][-1] == "disposed"


def test_configure_requires_migration_0047_before_any_admin_catalog_call(tmp_path: Path) -> None:
    result = execute_resource_main(tmp_path, phase="configure")
    assert result["caught"] == "AssertionError"
    assert result["events"] == ["readonly", "disposed"]
    # Persisting the immutable before snapshot is permitted and required.
    assert result["state"].is_file()


def test_configure_reaches_mutation_only_after_preflight_and_private_before_snapshot(
    tmp_path: Path,
) -> None:
    result = execute_resource_main(tmp_path, phase="configure", schema="0047_speech_origin")
    assert result["caught"] is None
    assert result["events"] == ["readonly", "configure", "disposed"]
