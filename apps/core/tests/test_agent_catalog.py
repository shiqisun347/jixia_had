from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from jx_core.auth.errors import error_message, error_status
from jx_core.rules.schemas import AgentProfileCreate, AgentProfileUpdate


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
