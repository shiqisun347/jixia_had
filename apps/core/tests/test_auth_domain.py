from __future__ import annotations

from uuid import uuid4

import pytest

from jx_core.auth.normalization import (
    InputNormalizationError,
    normalize_real_name,
    prepare_username,
)
from jx_core.auth.passwords import PasswordPolicyError, PasswordService
from jx_core.auth.permissions import PermissionError, require_admin, require_password_changed
from jx_core.auth.session import AuthContext, cookie_policy, is_safe_return_to, token_hash
from jx_core.legal.terms import (
    CURRENT_PLATFORM_TERMS,
    get_current_platform_terms,
    get_platform_terms,
)


def test_username_normalization_is_trimmed_nfkc_casefolded_and_unique() -> None:
    parts = prepare_username("  Ａlice_1  ")

    assert parts.display == "Ａlice_1"
    assert parts.normalized == "alice_1"
    assert prepare_username("ALICE_1").normalized == parts.normalized


@pytest.mark.parametrize("value", ["ab", "a" * 33, "---", "bad name", "bad/slash"])
def test_username_rejects_invalid_values(value: str) -> None:
    with pytest.raises(InputNormalizationError):
        prepare_username(value)


def test_real_name_nfkc_and_control_character_policy() -> None:
    assert normalize_real_name("  １２３  ") == "123"
    with pytest.raises(InputNormalizationError, match="control"):
        normalize_real_name("林\n知夏")


def test_password_service_uses_argon2id_and_does_not_trim() -> None:
    service = PasswordService()
    encoded = service.hash("correct horse battery")

    assert encoded.startswith("$argon2id$")
    assert service.verify(encoded, "correct horse battery").valid is True
    assert service.verify(encoded, "correct horse battery ").valid is False
    assert service.verify(encoded, "short").valid is False
    service.verify_dummy("wrong password")

    with pytest.raises(PasswordPolicyError):
        service.hash("short")


def test_platform_terms_are_versioned_and_single_source() -> None:
    assert get_current_platform_terms() is CURRENT_PLATFORM_TERMS
    assert get_platform_terms(CURRENT_PLATFORM_TERMS.version) is CURRENT_PLATFORM_TERMS
    assert get_platform_terms("platform-terms-unknown") is None


def test_session_cookie_and_return_to_policy() -> None:
    production = cookie_policy("production")
    development = cookie_policy("development")
    test = cookie_policy("test")

    assert production.name == "__Host-jx_session"
    assert production.secure is True
    assert development.name == "jx_session"
    assert test.name == "jx_session"
    assert development.secure is False
    assert is_safe_return_to("/debate") is True
    assert is_safe_return_to("/lobby?join=1#code") is True
    assert is_safe_return_to("//attacker.test") is False
    assert is_safe_return_to("https://attacker.test") is False
    assert is_safe_return_to("/safe\\attacker") is False
    assert is_safe_return_to("/safe\nattacker") is False


def test_permission_guards_apply_server_side() -> None:
    normal = AuthContext(
        user_id=uuid4(), role="USER", session_id=uuid4(), must_change_password=False
    )
    temporary = AuthContext(
        user_id=normal.user_id,
        role="USER",
        session_id=normal.session_id,
        must_change_password=True,
    )

    assert require_password_changed(normal) is normal
    with pytest.raises(PermissionError, match="forbidden"):
        require_admin(normal)
    with pytest.raises(PermissionError, match="password_change_required"):
        require_password_changed(temporary)
    assert len(token_hash("test-token")) == 64
