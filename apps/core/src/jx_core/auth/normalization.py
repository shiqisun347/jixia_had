"""Canonical user-input normalization shared by HTTP and admin paths."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
REAL_NAME_MIN_LENGTH = 2
REAL_NAME_MAX_LENGTH = 30


class InputNormalizationError(ValueError):
    """Raised when a user-facing identity field cannot be accepted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class UsernameParts:
    """The trimmed display value and its unique login key."""

    display: str
    normalized: str


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InputNormalizationError("invalid_type", f"{field} must be text")
    return value


def prepare_username(value: object) -> UsernameParts:
    """Validate a username and return display and NFKC/casefold forms."""

    raw = _require_text(value, "username")
    display = raw.strip()
    normalized = unicodedata.normalize("NFKC", display).casefold()
    if not USERNAME_MIN_LENGTH <= len(normalized) <= USERNAME_MAX_LENGTH:
        raise InputNormalizationError(
            "username_length",
            f"username must contain {USERNAME_MIN_LENGTH}–{USERNAME_MAX_LENGTH} characters",
        )
    has_alphanumeric = False
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"}:
            has_alphanumeric = True
            continue
        if character in "._-":
            continue
        raise InputNormalizationError(
            "username_characters",
            "username may contain only letters, numbers, dot, underscore, and hyphen",
        )
    if not has_alphanumeric:
        raise InputNormalizationError(
            "username_characters",
            "username must contain at least one letter or number",
        )
    return UsernameParts(display=display, normalized=normalized)


def normalize_real_name(value: object) -> str:
    """Normalize a display name without collapsing meaningful spaces."""

    raw = _require_text(value, "real_name")
    normalized = unicodedata.normalize("NFKC", raw).strip()
    if not REAL_NAME_MIN_LENGTH <= len(normalized) <= REAL_NAME_MAX_LENGTH:
        raise InputNormalizationError(
            "real_name_length",
            f"real_name must contain {REAL_NAME_MIN_LENGTH}–{REAL_NAME_MAX_LENGTH} characters",
        )
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise InputNormalizationError(
            "real_name_characters", "real_name contains a control character"
        )
    return normalized


__all__ = [
    "InputNormalizationError",
    "UsernameParts",
    "normalize_real_name",
    "prepare_username",
]
