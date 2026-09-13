from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from jx_core.auth.errors import error_message, error_status
from jx_core.rules.paper_experiment_4v4 import build_paper_experiment_4v4_draft
from jx_core.rules.schemas import (
    AgentProfileCreate,
    AgentProfileUpdate,
    ModelApiKeyRotate,
    ModelProfileCreate,
    ModelProfileUpdate,
    RuleAgentUpdate,
    RuleCreate,
    RuleJudgeUpdate,
)


def test_agent_name_is_trimmed_and_blank_is_rejected() -> None:
    payload = AgentProfileCreate(
        name="  乾元  ",
        model_profile_id=uuid4(),
        voice_profile_id=uuid4(),
    )
    assert payload.name == "乾元"
    with pytest.raises(ValidationError):
        AgentProfileCreate(
            name="   ",
            model_profile_id=uuid4(),
            voice_profile_id=uuid4(),
        )


def test_agent_update_uses_stable_conflict_errors() -> None:
    payload = AgentProfileUpdate(
        name="明辨",
        model_profile_id=uuid4(),
        voice_profile_id=uuid4(),
        generation_params={"temperature": 0.7},
    )
    assert payload.generation_params == {"temperature": 0.7}
    assert error_status("agent_name_taken") == 409
    assert "名称已存在" in error_message("agent_name_taken")
    assert error_status("agent_in_use") == 409


def test_agent_payload_rejects_independent_avatar() -> None:
    with pytest.raises(ValidationError):
        AgentProfileCreate.model_validate(
            {
                "name": "不可双写头像",
                "model_profile_id": str(uuid4()),
                "voice_profile_id": str(uuid4()),
                "avatar_key": "agent-01",
            }
        )


def test_rule_agent_update_exposes_only_minimal_editable_fields() -> None:
    payload = {
        "model_profile_id": str(uuid4()),
        "generation_params": {"temperature": 0.7},
        "status": "ENABLED",
    }
    RuleAgentUpdate.model_validate(payload)
    with pytest.raises(ValidationError):
        RuleAgentUpdate.model_validate({**payload, "name": "不能修改"})
    with pytest.raises(ValidationError):
        RuleAgentUpdate.model_validate({**payload, "voice_profile_id": str(uuid4())})


def test_4v4_rule_requires_an_explicit_default_agent_model() -> None:
    payload = {
        "host_voice_profile_id": str(uuid4()),
        "draft": build_paper_experiment_4v4_draft(),
    }
    with pytest.raises(ValidationError):
        RuleCreate.model_validate(payload)
    RuleCreate.model_validate({**payload, "default_agent_model_profile_id": str(uuid4())})


def test_disabled_judge_cannot_enter_leaderboard() -> None:
    with pytest.raises(ValidationError):
        RuleJudgeUpdate(
            enabled=False,
            model_profile_id=None,
            judge_prompt="",
            include_in_leaderboard=True,
        )


def test_model_capabilities_have_safe_defaults_and_allow_disabling() -> None:
    default_payload = ModelProfileCreate(name="默认模型", config_ref="default-model")
    assert default_payload.capability_schema.temperature is not None
    assert default_payload.capability_schema.temperature.maximum == 2
    assert default_payload.capability_schema.max_tokens is not None
    assert default_payload.capability_schema.max_tokens.maximum == 32768

    disabled_payload = ModelProfileCreate.model_validate(
        {
            "name": "固定参数模型",
            "config_ref": "fixed-model",
            "capability_schema": {
                "temperature": None,
                "top_p": None,
                "max_tokens": None,
            },
        }
    )
    assert disabled_payload.capability_schema.temperature is None


@pytest.mark.parametrize(
    "capability_schema",
    [
        {"temperature": {"minimum": 1.5, "maximum": 0.5}},
        {"temperature": {"minimum": -0.1, "maximum": 1}},
        {"top_p": {"minimum": 0, "maximum": 1.1}},
        {"max_tokens": {"minimum": 0, "maximum": 1024}},
        {"frequency_penalty": {"minimum": 0, "maximum": 1}},
    ],
)
def test_model_capabilities_reject_invalid_or_unknown_ranges(
    capability_schema: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ModelProfileCreate.model_validate(
            {
                "name": "非法能力模型",
                "config_ref": "invalid-model",
                "capability_schema": capability_schema,
            }
        )


def test_model_update_cannot_rotate_api_key_implicitly() -> None:
    with pytest.raises(ValidationError):
        ModelProfileUpdate.model_validate(
            {"name": "模型", "config_ref": "model", "api_key": "secret"}
        )
    rotation = ModelApiKeyRotate(api_key="new-secret")
    assert rotation.api_key.get_secret_value() == "new-secret"
    assert "new-secret" not in str(rotation)
