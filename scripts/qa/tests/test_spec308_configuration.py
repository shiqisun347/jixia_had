"""Owned-only QA configuration controls; no provider/network or production IO."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

spec = importlib.util.spec_from_file_location(
    "spec308_configuration", Path(__file__).parents[1] / "probes/server_spec308_configuration.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
configuration = cast(Any, module)


def public_config(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = tmp_path / "scripts/qa/probes/spec308-public-config.json"
    path.parent.mkdir(parents=True)
    value = {
        "plan": {"schema": "spec308-prompt-override-plan-v1"},
        "calibrations": [
            {
                "voice_profile_id": str(uuid4()),
                "provider_voice": voice,
                "chars_per_second": 4.0,
                "rate": 1.0,
                "evidence_sha256": "a" * 64,
            }
            for voice in sorted(configuration.PROVIDERS)
        ],
    }
    path.write_text(json.dumps(value))
    return path, value


def test_calibrations_require_five_real_identities_and_bounded_positive_rates(
    tmp_path: Path,
) -> None:
    path, value = public_config(tmp_path)
    loaded, digest = configuration.read_configuration(tmp_path)
    assert loaded == value and len(digest) == 64
    for field, invalid in [
        ("chars_per_second", 0),
        ("chars_per_second", float("nan")),
        ("rate", 3),
        ("provider_voice", "longanwen_v3.1"),
        ("evidence_sha256", "not-real-evidence"),
    ]:
        candidate = json.loads(json.dumps(value))
        candidate["calibrations"][0][field] = invalid
        path.write_text(json.dumps(candidate))
        with pytest.raises(RuntimeError):
            configuration.read_configuration(tmp_path)


def test_duplicate_resource_ids_and_symlink_inputs_are_rejected(tmp_path: Path) -> None:
    path, value = public_config(tmp_path)
    value["calibrations"][1]["voice_profile_id"] = value["calibrations"][0]["voice_profile_id"]
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="qa_spec308_calibration_identity"):
        configuration.read_configuration(tmp_path)
    real = tmp_path / "public.json"
    path.rename(real)
    path.symlink_to(real)
    with pytest.raises(RuntimeError, match="qa_spec308_configuration_missing"):
        configuration.read_configuration(tmp_path)


@pytest.mark.asyncio
async def test_nonowned_database_is_rejected_before_any_query(tmp_path: Path) -> None:
    public_config(tmp_path)
    session = SimpleNamespace(
        bind=SimpleNamespace(url=make_url("postgresql+psycopg://127.0.0.1/not-owned")),
        scalar=AsyncMock(),
    )
    context = AsyncMock()
    context.__aenter__.return_value = session
    with pytest.raises(RuntimeError, match="qa_spec308_database_not_owned"):
        await configuration.prepare_configuration(lambda: context, uuid4(), tmp_path)
    session.scalar.assert_not_awaited()


def test_single_scenario_execution_and_gates_only_change_receiver_client_import() -> None:
    root = Path(__file__).parents[1] / "probes"
    before = (root / "server_spec307_app_match_probe.py").read_text()
    after = (root / "server_spec308_app_match_probe.py").read_text()
    start, end = "async def run_scenario(", "async def run(args:"
    assert before[before.index(start) : before.index(end)] == after[
        after.index(start) : after.index(end)
    ].replace(
        "from scripts.qa.probes.server_spec308_receiver import ClientFailure, ConferenceClients",
        "from scripts.qa.probes.server_spec307_clients import ClientFailure, ConferenceClients",
    )


def prompt_plan():
    key = (str(uuid4()), str(uuid4()), "SPEECH")
    template = "public reviewed prompt"
    digest = hashlib.sha256(template.encode()).hexdigest()
    slot = {
        "agent_profile_id": key[0],
        "stage_id": key[1],
        "purpose": key[2],
        "requires_write": True,
        "template_sha256": digest,
    }
    operation = {**slot, "method": "PUT", "body": {"template_text": template}}
    return key, slot, operation


def test_required_writes_must_exactly_match_the_unique_operation_set() -> None:
    key, slot, operation = prompt_plan()
    assert configuration.validate_prompt_operations([slot], [operation], {key}) == {
        key: slot["template_sha256"]
    }
    for slots, operations, code in (
        ([slot], [], "qa_spec308_prompt_operations"),
        ([{**slot, "requires_write": False}], [operation], "qa_spec308_prompt_operations"),
        ([slot], [operation, operation], "qa_spec308_prompt_operations"),
        ([{**slot, "requires_write": "false"}], [], "qa_spec308_prompt_write_flag"),
        ([slot, slot], [operation], "qa_spec308_prompt_coverage"),
    ):
        with pytest.raises(RuntimeError, match=code):
            configuration.validate_prompt_operations(slots, operations, {key})


def test_operation_body_hash_must_match_both_operation_and_the_same_slot() -> None:
    key, slot, operation = prompt_plan()
    for candidate in (
        {**operation, "template_sha256": "b" * 64},
        {
            **operation,
            "body": {"template_text": "different self-consistent prompt"},
            "template_sha256": hashlib.sha256(b"different self-consistent prompt").hexdigest(),
        },
    ):
        with pytest.raises(RuntimeError, match="qa_spec308_prompt_hash_mismatch"):
            configuration.validate_prompt_operations([slot], [candidate], {key})


def test_every_inherited_or_overridden_slot_is_rechecked_after_configuration() -> None:
    key, slot, operation = prompt_plan()
    targets = {key: slot["template_sha256"]}
    template = operation["body"]["template_text"]
    defaults = {(key[1], key[2]): template}
    configuration.verify_effective_prompt_templates(targets, defaults, {})
    configuration.verify_effective_prompt_templates(targets, {}, {key: template})
    with pytest.raises(RuntimeError, match="qa_spec308_effective_prompt_missing"):
        configuration.verify_effective_prompt_templates(targets, {}, {})
    with pytest.raises(RuntimeError, match="qa_spec308_effective_prompt_mismatch"):
        configuration.verify_effective_prompt_templates(targets, defaults, {key: "stale override"})
    extra = (str(uuid4()), key[1], key[2])
    with pytest.raises(RuntimeError, match="qa_spec308_effective_prompt_missing"):
        configuration.verify_effective_prompt_templates(
            {**targets, extra: "a" * 64}, {}, {key: template}
        )
