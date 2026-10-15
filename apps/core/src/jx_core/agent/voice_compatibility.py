"""Explicit correction of unusable legacy Qwen Audio 3.1 system voice IDs.

The original room snapshot is retained. Actual IDs are recorded by the normal
TTS call and audio-asset capture. This is not a runtime provider/model fallback.
"""

from types import MappingProxyType

QWEN_31_LEGACY_VOICE_CORRECTIONS = MappingProxyType(
    {
        "longrongtaolian_v3.1": "longyingtao_v3.1",
        "longcanzhuyue_v3.1": "longanlang_v3.1",
        "longyixuanjun_v3.1": "longanwen_v3.1",
        "longyingsongliu_v3.1": "longanyuanfei_v3.1",
    }
)


def effective_tts_voice(model: str, voice: str) -> str:
    if model != "qwen-audio-3.1-tts-flash":
        return voice
    return QWEN_31_LEGACY_VOICE_CORRECTIONS.get(voice, voice)
