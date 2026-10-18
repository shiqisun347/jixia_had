"""No database/provider calls; bootstrap rejects anything except its fresh seed."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from scripts.ops import bootstrap_v2_admin as admin


@pytest.fixture
def seed(monkeypatch):
    password_hash = "synthetic-test-hash-only"
    monkeypatch.setattr(
        admin, "SEED_HASH_SHA256", hashlib.sha256(password_hash.encode()).hexdigest()
    )
    now = datetime.now(UTC)
    return {
        **admin.SEED_FIELDS,
        "password_hash": password_hash,
        "created_at": now,
        "updated_at": now,
        "password_changed_at": now,
    }


def test_exact_untouched_seed_permitted(seed):
    admin.verify_seed(seed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", uuid4()),
        ("username", "newadmin"),
        ("username_normalized", "newadmin"),
        ("real_name", "其他管理员"),
        ("role", "USER"),
        ("status", "DISABLED"),
        ("must_change_password", False),
        ("failed_login_count", 1),
        ("avatar_version", 1),
        ("default_avatar_key", "human-02"),
        ("avatar_path", "uploaded.webp"),
        ("last_login_at", datetime.now(UTC)),
        ("locked_until", datetime.now(UTC)),
        ("password_hash", "different-hash"),
        ("updated_at", datetime(2020, 1, 1, tzinfo=UTC)),
    ],
)
def test_modified_or_nonseed_admin_refused(seed, field, value):
    seed[field] = value
    with pytest.raises(admin.Refusal):
        admin.verify_seed(seed)


def test_extra_user_column_refused(seed):
    seed["unreviewed_field"] = None
    with pytest.raises(admin.Refusal):
        admin.verify_seed(seed)


def test_confirmation_mismatch_refused_without_database():
    inputs = iter(["new-admin", "测试管理员"])
    passwords = iter(["synthetic-one", "synthetic-two"])
    with pytest.raises(admin.Refusal, match="password_confirmation_mismatch"):
        admin.collect_credentials(
            input_fn=lambda _: next(inputs), password_fn=lambda _: next(passwords)
        )


def test_credentials_are_interactive_only_and_not_emitted(capsys):
    inputs = iter(["new-admin", "测试管理员"])
    result = admin.collect_credentials(
        input_fn=lambda _: next(inputs), password_fn=lambda _: "synthetic-only"
    )
    assert result == ("new-admin", "测试管理员", "synthetic-only")
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_wrong_target_cannot_create_engine_or_prompt(monkeypatch):
    import sqlalchemy.ext.asyncio

    def forbidden(*args, **kwargs):
        pytest.fail("side effect before database guard")

    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", forbidden)
    with pytest.raises(admin.Refusal):
        await admin.bootstrap(
            url="postgresql+psycopg://127.0.0.1/jx_spec328_public_init",
            expected_db_name="jx_spec328_wrong_target",
            apply=True,
            credential_prompt=forbidden,
        )
