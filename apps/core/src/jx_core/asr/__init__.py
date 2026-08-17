"""Realtime ASR adapters and bounded speech segmentation."""

from .protocol import FunAsrConnection, FunAsrError, FunAsrSentence, SegmentResult
from .session import AsrSpeechResult, AsrSpeechSession

__all__ = [
    "AsrSpeechResult",
    "AsrSpeechSession",
    "FunAsrConnection",
    "FunAsrError",
    "FunAsrSentence",
    "SegmentResult",
]
