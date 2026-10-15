"""The approved conference host voice is reserved across legacy aliases."""

from ..agent.voice_compatibility import effective_tts_voice

CONFERENCE_HOST_VOICE = "longanwen_v3.1"
CONFERENCE_HOST_NAME = "龙风月瑶"


def is_host_reserved_voice(provider_voice: str) -> bool:
    return effective_tts_voice("qwen-audio-3.1-tts-flash", provider_voice) == CONFERENCE_HOST_VOICE
