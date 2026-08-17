"""The small, versioned platform-terms registry used by registration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TermsDocument:
    version: str
    title: str
    body: str


PLATFORM_TERMS_V1 = TermsDocument(
    version="platform-terms-v1",
    title="稷下平台使用条款",
    body=(
        "本平台用于人类与 Agent 的辩论实验。注册后，平台会在必要范围内保存账号资料、"
        "比赛文字和评分；正常结束的比赛文字与评分对已登录用户可见。前台展示真实姓名，"
        "用户名仅用于登录和管理。首次作为人类辩手参赛前，还需单独确认录音及语音模型处理说明。"
    ),
)

CURRENT_PLATFORM_TERMS = PLATFORM_TERMS_V1
_TERMS_BY_VERSION = {PLATFORM_TERMS_V1.version: PLATFORM_TERMS_V1}

HUMAN_PARTICIPATION_TERMS_V1 = TermsDocument(
    version="human-participation-v1",
    title="人类辩手录音与模型处理说明",
    body=(
        "作为人类辩手参赛时，平台会采集麦克风音频，使用实时语音识别生成文字，并在比赛需要时"
        "将最新文字上下文提供给 Agent 和 AI 裁判。正常结束的比赛文字与评分对已登录用户可见；"
        "音频仅按平台权限和保存周期向参赛者与管理员开放。"
    ),
)
CURRENT_HUMAN_PARTICIPATION_TERMS = HUMAN_PARTICIPATION_TERMS_V1


def get_current_platform_terms() -> TermsDocument:
    return CURRENT_PLATFORM_TERMS


def get_platform_terms(version: str) -> TermsDocument | None:
    return _TERMS_BY_VERSION.get(version)


def get_current_human_participation_terms() -> TermsDocument:
    return CURRENT_HUMAN_PARTICIPATION_TERMS


__all__ = [
    "CURRENT_PLATFORM_TERMS",
    "CURRENT_HUMAN_PARTICIPATION_TERMS",
    "HUMAN_PARTICIPATION_TERMS_V1",
    "PLATFORM_TERMS_V1",
    "TermsDocument",
    "get_current_platform_terms",
    "get_current_human_participation_terms",
    "get_platform_terms",
]
