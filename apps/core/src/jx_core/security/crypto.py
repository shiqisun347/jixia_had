"""Small AES-GCM boundary for administrator-managed provider secrets."""

from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretEncryptionError(ValueError):
    pass


def _key(raw: str) -> bytes:
    try:
        value = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as error:
        raise SecretEncryptionError("llm_key_encryption_key_invalid") from error
    if len(value) != 32:
        raise SecretEncryptionError("llm_key_encryption_key_invalid")
    return value


def encrypt_secret(value: str, master_key: str) -> tuple[bytes, bytes, str]:
    if not value:
        raise SecretEncryptionError("provider_api_key_empty")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_key(master_key)).encrypt(nonce, value.encode("utf-8"), None)
    return ciphertext, nonce, value[-4:]


def decrypt_secret(ciphertext: bytes, nonce: bytes, master_key: str) -> str:
    try:
        return AESGCM(_key(master_key)).decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception as error:
        raise SecretEncryptionError("provider_api_key_decrypt_failed") from error


__all__ = ["SecretEncryptionError", "decrypt_secret", "encrypt_secret"]
