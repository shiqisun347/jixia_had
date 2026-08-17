"""Argon2id password policy and verification primitives."""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 64
PASSWORD_VERIFY_MIN_LENGTH = 1
ARGON2_MEMORY_COST_KIB = 19_456
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1


class PasswordPolicyError(ValueError):
    """Raised when a password violates the product length policy."""


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool = False


class PasswordService:
    """Small stateless wrapper around an explicitly configured Argon2id hasher."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            memory_cost=ARGON2_MEMORY_COST_KIB,
            time_cost=ARGON2_TIME_COST,
            parallelism=ARGON2_PARALLELISM,
            type=Type.ID,
        )
        # A process-local valid hash makes unknown-user login timing comparable
        # without persisting or logging a user password.
        self._dummy_hash = self._hasher.hash("jx-internal-dummy-password-v1")

    def validate(self, password: object) -> str:
        if not isinstance(password, str):
            raise PasswordPolicyError("password must be text")
        if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise PasswordPolicyError(
                f"password must contain {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters"
            )
        return password

    def hash(self, password: object) -> str:
        return self._hasher.hash(self.validate(password))

    def verify(self, encoded_hash: str, password: object) -> PasswordVerification:
        candidate = self._validate_verification_candidate(password)
        try:
            valid = self._hasher.verify(encoded_hash, candidate)
        except VerificationError:
            return PasswordVerification(valid=False)
        return PasswordVerification(
            valid=valid,
            needs_rehash=valid and self._hasher.check_needs_rehash(encoded_hash),
        )

    def verify_dummy(self, password: object) -> None:
        """Perform one Argon2id verification for an unknown username."""

        candidate = self._validate_verification_candidate(password)
        try:
            self._hasher.verify(self._dummy_hash, candidate)
        except VerificationError:
            pass

    def _validate_verification_candidate(self, password: object) -> str:
        if not isinstance(password, str):
            raise PasswordPolicyError("password must be text")
        if not PASSWORD_VERIFY_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise PasswordPolicyError(
                f"password must contain "
                f"{PASSWORD_VERIFY_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters"
            )
        return password


__all__ = [
    "ARGON2_MEMORY_COST_KIB",
    "ARGON2_PARALLELISM",
    "ARGON2_TIME_COST",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_VERIFY_MIN_LENGTH",
    "PasswordPolicyError",
    "PasswordService",
    "PasswordVerification",
]
