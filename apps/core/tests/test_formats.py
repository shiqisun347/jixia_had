from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jx_core.agent.llm import LlmProviderError
from jx_core.agent.runtime import _render_snapshot_prompt, _snapshot_prompt
from jx_core.formats.schemas import PromptTemplatePayload
from jx_core.formats.service import build_published_snapshot
from jx_core.models import (
    AgentProfile,
    FormatJudgeProfile,
    FormatModule,
    FormatTopicReference,
    FormatVersion,
    ModelProfile,
    PromptTemplate,
    VoiceProfile,
)

BASE_VARIABLES = {
    "TOPIC",
    "POSITION",
    "STANCE",
    "AFFIRMATIVE_STANCE",
    "NEGATIVE_STANCE",
    "DEBATER_SEAT",
    "DEBATE_HISTORY",
    "SIDE_REMAINING_MS",
    "OPPONENT_REMAINING_MS",
}


def test_free_debate_prompt_extracts_the_code_whitelist() -> None:
    template = "\n".join(f"{name}: {{{{{name}}}}}" for name in sorted(BASE_VARIABLES))
    payload = PromptTemplatePayload(
        stage_key="FREE_DEBATE",
        purpose="DECISION",
        template_text=template,
        output_contract="SHOULD_SPEAK_V1",
    )
    assert set(payload.variables) == BASE_VARIABLES


@pytest.mark.parametrize(
    "template",
    [
        "{{TOPIC}} {{UNKNOWN}}",
        "{{TOPIC}}",
    ],
)
def test_free_debate_prompt_rejects_unknown_or_missing_variables(template: str) -> None:
    with pytest.raises(ValidationError):
        PromptTemplatePayload(
            stage_key="FREE_DEBATE",
            purpose="DECISION",
            template_text=template,
        )


def test_declared_variables_cannot_disagree_with_template() -> None:
    with pytest.raises(ValidationError):
        PromptTemplatePayload(
            stage_key="FIXED_SPEECH",
            purpose="SPEECH",
            template_text="辩题：{{TOPIC}}",
            variables=["POSITION"],
        )


def test_published_snapshot_freezes_all_version_owned_configuration() -> None:
    version_id = uuid4()
    rule_id = uuid4()
    model = ModelProfile(
        name="辩手模型",
        config_ref="agent-primary",
        model_id="model-v1",
        max_concurrency=7,
        generation_params={"temperature": 0.5},
    )
    voice = VoiceProfile(
        name="辩手音色",
        kind="AGENT",
        provider_voice="voice-v1",
        rate=1.0,
        playback_gain=1.0,
        avatar_key="agent-01",
    )
    agent = AgentProfile(
        name="乾元",
        format_version_id=version_id,
        model_profile_id=model.id,
        voice_profile_id=voice.id,
        generation_params={"temperature": 0.7},
    )
    prompt = PromptTemplate(
        format_version_id=version_id,
        agent_profile_id=agent.id,
        stage_key="FREE_DEBATE",
        purpose="DECISION",
        template_text="辩题：{{TOPIC}}",
        variables=["TOPIC"],
        output_contract="SHOULD_SPEAK_V1",
    )
    module = FormatModule(
        format_version_id=version_id,
        module_key="ROOMS",
        enabled=True,
        config={"auto_fill": True},
    )
    judge = FormatJudgeProfile(
        format_version_id=version_id,
        model_profile_id=model.id,
        system_prompt="系统裁判",
        judge_prompt="请评分",
        generation_params={"temperature": 0},
        dimensions=[{"key": "logic", "weight": 1}],
        output_contract="JUDGE_RESULT_V1",
    )
    topic = FormatTopicReference(
        format_version_id=version_id,
        position=1,
        private_snapshot={
            "title": "测试辩题",
            "affirmative_text": "正方",
            "negative_text": "反方",
        },
    )
    version = FormatVersion(
        id=version_id,
        format_key="paper-experiment",
        version=2,
        name="论文实验",
        rule_id=rule_id,
        created_by=uuid4(),
    )

    snapshot = build_published_snapshot(
        version=version,
        agents=[agent],
        models=[model],
        voices=[voice],
        prompts=[prompt],
        modules=[module],
        judge=judge,
        topic_references=[topic],
        published_at=datetime(2026, 8, 22, tzinfo=UTC),
    )
    frozen = deepcopy(snapshot)

    agent.generation_params["temperature"] = 1.8
    prompt.variables.append("POSITION")
    module.config["auto_fill"] = False
    judge.dimensions[0]["weight"] = 0
    assert snapshot == frozen
    assert snapshot["format_version_id"] == str(version_id)
    assert frozen["agents"][0]["model"]["model_id"] == "model-v1"  # type: ignore[index]
    assert frozen["agents"][0]["voice"]["provider_voice"] == "voice-v1"  # type: ignore[index]
    assert frozen["prompts"][0]["output_contract"] == "SHOULD_SPEAK_V1"  # type: ignore[index]
    assert frozen["modules"][0]["module_key"] == "ROOMS"  # type: ignore[index]
    assert frozen["judge"]["dimensions"][0]["key"] == "logic"  # type: ignore[index]
    assert frozen["judge"]["model"]["max_concurrency"] == 7  # type: ignore[index]
    assert frozen["topic_references"][0]["private_snapshot"]["title"] == "测试辩题"  # type: ignore[index]


def test_runtime_prefers_agent_prompt_and_falls_back_to_version_default() -> None:
    first_agent, second_agent = uuid4(), uuid4()
    snapshot = {
        "prompts": [
            {
                "agent_profile_id": None,
                "stage_key": "FREE_DEBATE",
                "purpose": "SPEECH",
                "template_text": "默认 {{TOPIC}}",
            },
            {
                "agent_profile_id": str(first_agent),
                "stage_key": "FREE_DEBATE",
                "purpose": "SPEECH",
                "template_text": "专属 {{TOPIC}}",
            },
        ]
    }

    assert (
        _snapshot_prompt(
            snapshot,
            agent_profile_id=first_agent,
            stage_key="FREE_DEBATE",
            purpose="SPEECH",
        )["template_text"]
        == "专属 {{TOPIC}}"
    )  # type: ignore[index]
    assert (
        _snapshot_prompt(
            snapshot,
            agent_profile_id=second_agent,
            stage_key="FREE_DEBATE",
            purpose="SPEECH",
        )["template_text"]
        == "默认 {{TOPIC}}"
    )  # type: ignore[index]


def test_runtime_renders_only_declared_snapshot_variables() -> None:
    assert (
        _render_snapshot_prompt(
            "辩题：{{ TOPIC }}；席位：{{DEBATER_SEAT}}",
            {"TOPIC": "测试题", "DEBATER_SEAT": "二辩"},
        )
        == "辩题：测试题；席位：二辩"
    )
    with pytest.raises(LlmProviderError):
        _render_snapshot_prompt("{{TOPIC}} {{POSITION}}", {"TOPIC": "测试题"})
