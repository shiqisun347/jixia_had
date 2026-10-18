"""Backup boundary controls: no PostgreSQL, provider, or network calls."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.ops import backup_v2 as backup

FIXTURE_PASSWORD = "fixture-password-must-never-appear-in-output"
DATABASE_URL = "postgresql+psycopg://fixture:" + FIXTURE_PASSWORD + "@127.0.0.1/fixture"


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, statement):
        query = statement.as_string()
        self.connection.events.append(query)
        self.rows = self.connection.rows[
            next(name for name in self.connection.rows if '"public"."' + name + '"' in query)
        ]

    def __iter__(self):
        return iter((row,) for row in self.rows)


class FakeConnection:
    def __init__(self, *, primary_key=True):
        self.tables = [
            ("alembic_version", ["version_num"], [True]),
            ("users", ["id"] if primary_key else None, [False] if primary_key else None),
        ]
        self.rows = {
            "alembic_version": ['{"version_num": "0049_agent_human_avatars"}'],
            "users": ['{"id": 1, "name": "公开甲"}', '{"id": 2, "name": "公开乙"}'],
        }
        self.events = []
        self.closed = False

    def execute(self, statement):
        text = statement if isinstance(statement, str) else statement.as_string()
        self.events.append(text)
        if text == "SHOW transaction_read_only":
            return Result([("on",)])
        if text == "SHOW transaction_isolation":
            return Result([("repeatable read",)])
        if text == "SELECT pg_export_snapshot()":
            return Result([("00000003-0000001B-1",)])
        if text == backup.TABLE_QUERY:
            return Result(self.tables)
        if text.startswith("SELECT version_num"):
            return Result([("0049_agent_human_avatars",)])
        return Result()

    def cursor(self, *, name):
        assert name.startswith("jx_backup_")
        return FakeCursor(self)

    def close(self):
        self.closed = True
        self.events.append("CLOSE")


def environment():
    return {
        "DATABASE_URL": DATABASE_URL,
        "PATH": "/public-test/bin",
        "PGPASSWORD": "unrelated-inherited-password",
        "UNRELATED_SECRET": "unrelated-secret-value",
    }


def test_postgres_export_and_manifest_share_snapshot_credentials_stay_in_memory(
    tmp_path, monkeypatch
):
    connection = FakeConnection()
    events = connection.events
    monkeypatch.setenv("PGHOST", "unrelated-inherited-host")
    captured = {}

    def connect(**options):
        assert "PGHOST" not in os.environ
        assert options["password"] == FIXTURE_PASSWORD
        assert options["passfile"] == os.devnull and options["autocommit"] is True
        captured["connection"] = options
        return connection

    def runner(arguments, **options):
        assert not connection.closed and "ROLLBACK" not in events
        assert "SELECT pg_export_snapshot()" in events
        assert events[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
        assert "--snapshot=00000003-0000001B-1" in arguments
        assert "-Fc" in arguments and "--no-owner" in arguments and "--no-acl" in arguments
        assert not any(FIXTURE_PASSWORD in value or "postgresql://" in value for value in arguments)
        child = options["env"]
        assert child["PGPASSWORD"] == FIXTURE_PASSWORD
        assert child["PGDATABASE"] == "fixture" and child["PGHOST"] == "127.0.0.1"
        assert child["PGPASSFILE"] == os.devnull
        assert "DATABASE_URL" not in child and "UNRELATED_SECRET" not in child
        assert options["stderr"] == subprocess.DEVNULL
        options["stdout"].write(b"PGDMP-public-fake-dump")
        captured["dump"] = options
        events.append("DUMP_COMPLETED")
        return SimpleNamespace(returncode=0)

    output = tmp_path / "backup"
    manifest = backup.backup_postgres(
        output, environment=environment(), connect=connect, runner=runner
    )
    assert connection.closed and os.environ["PGHOST"] == "unrelated-inherited-host"
    assert events.index("DUMP_COMPLETED") < events.index("ROLLBACK") < events.index("CLOSE")
    assert manifest["content"]["migration_heads"] == ["0049_agent_human_avatars"]
    assert [row["row_count"] for row in manifest["content"]["tables"]] == [1, 2]
    assert manifest["artifact"]["sha256"] == backup.file_sha(output / "database.dump")
    stored = (output / "manifest.json").read_text()
    assert FIXTURE_PASSWORD not in stored and "unrelated-secret" not in stored
    assert captured and stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_postgres_manifest_can_be_recomputed_and_same_count_tampering_is_detected():
    source = FakeConnection()
    restored = FakeConnection()
    before = backup.postgres_content_manifest(source)
    assert before == backup.postgres_content_manifest(restored)
    restored.rows["users"][1] = '{"id": 2, "name": "被修改"}'
    after = backup.postgres_content_manifest(restored)
    assert before["tables"][1]["row_count"] == after["tables"][1]["row_count"] == 2
    assert before["tables"][1]["rows_sha256"] != after["tables"][1]["rows_sha256"]
    assert (
        before["tables"][1]["rows_sha256"]
        == hashlib.sha256(
            b"".join((row + "\n").encode() for row in source.rows["users"])
        ).hexdigest()
    )
    assert '"version_num" COLLATE "C"' in "\n".join(source.events)
    assert all(command in source.events for command in backup.PG_CANONICAL_SETTINGS)


def test_postgres_missing_primary_key_refuses_dump_and_removes_only_owned_output(tmp_path):
    connection = FakeConnection(primary_key=False)
    called = []
    with pytest.raises(backup.BackupError, match="public_table_without_primary_key"):
        backup.backup_postgres(
            tmp_path / "new",
            environment=environment(),
            connect=lambda **_kwargs: connection,
            runner=lambda *_args, **_kwargs: called.append(True),
        )
    assert connection.closed and not called and not (tmp_path / "new").exists()


@pytest.mark.parametrize("failure", ["returncode", "timeout", "invalid_header"])
def test_postgres_failed_dump_closes_exporter_and_removes_partial_files(tmp_path, failure):
    connection = FakeConnection()
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserve")

    def runner(arguments, **kwargs):
        kwargs["stdout"].write(b"partial")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 1)
        return SimpleNamespace(returncode=1 if failure == "returncode" else 0)

    with pytest.raises((backup.BackupError, subprocess.TimeoutExpired)):
        backup.backup_postgres(
            tmp_path / "new",
            environment=environment(),
            connect=lambda **_kwargs: connection,
            runner=runner,
        )
    assert connection.closed and not (tmp_path / "new").exists()
    assert sentinel.read_bytes() == b"preserve"


def test_inherited_pg_environment_restores_after_connection_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "prior-environment-value")

    def fail(**_options):
        assert "PGPASSWORD" not in os.environ
        raise RuntimeError("private provider diagnostic")

    with pytest.raises(RuntimeError):
        backup.backup_postgres(tmp_path / "new", environment=environment(), connect=fail)
    assert os.environ["PGPASSWORD"] == "prior-environment-value"
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize(
    "url",
    ["", "sqlite:///private", "postgresql://user@host", "postgresql://host/db?service=private"],
)
def test_missing_or_implicit_connection_sources_fail_closed(url):
    with pytest.raises((backup.BackupError, backup.psycopg.Error)):
        backup.connection_options({"DATABASE_URL": url})


def create_sqlite_source(path: Path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        'CREATE TABLE "votes" (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, blob BLOB);'
        'CREATE TABLE "without_key" (value TEXT, amount REAL);'
        'CREATE TABLE "composite" (a TEXT, b INTEGER, PRIMARY KEY(a,b)) WITHOUT ROWID;'
    )
    connection.execute("INSERT INTO votes(label,blob) VALUES (?,?)", ("公开\n字符", b"\x00\xff"))
    connection.execute("INSERT INTO without_key VALUES (?,?)", (None, 1.25))
    connection.execute("INSERT INTO composite VALUES (?,?)", ("双键", 2))
    connection.commit()
    return connection


def test_sqlite_backup_captures_uncheckpointed_wal_and_recomputes_after_restore(tmp_path):
    source = tmp_path / "source.sqlite3"
    origin = create_sqlite_source(source)
    try:
        assert Path(str(source) + "-wal").stat().st_size > 0
        source_before = source.read_bytes()
        wal_before = Path(str(source) + "-wal").read_bytes()
        destination = tmp_path / "backup"
        manifest = backup.backup_sqlite(source, destination)
        assert source.read_bytes() == source_before
        assert Path(str(source) + "-wal").read_bytes() == wal_before
        rows = {row["table"]: row for row in manifest["content"]["tables"]}
        assert set(rows) == {"votes", "without_key", "composite", "sqlite_sequence"}
        assert rows["votes"]["row_count"] == rows["sqlite_sequence"]["row_count"] == 1
        assert rows["without_key"]["ordering"] == "rowid"
        assert rows["composite"]["primary_key"] == ["a", "b"]
        restored = tmp_path / "restore.sqlite3"
        shutil.copyfile(destination / "database.sqlite3", restored)
        with sqlite3.connect(restored) as connection:
            assert backup.sqlite_content_manifest(connection) == manifest["content"]
            assert connection.execute("SELECT label,blob FROM votes").fetchone() == (
                "公开\n字符",
                b"\x00\xff",
            )
            connection.execute("UPDATE votes SET label='changed'")
            after = backup.sqlite_content_manifest(connection)
            assert after != manifest["content"]
        origin.execute("INSERT INTO votes(label) VALUES ('later')")
        origin.commit()
        assert (
            json.loads((destination / "manifest.json").read_text())["content"]
            == manifest["content"]
        )
        assert manifest["artifact"]["sha256"] == backup.file_sha(destination / "database.sqlite3")
    finally:
        origin.close()


def test_output_is_private_even_with_permissive_umask(tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE x (id INTEGER PRIMARY KEY)")
    old = os.umask(0)
    try:
        backup.backup_sqlite(source, tmp_path / "backup")
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "backup").stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (tmp_path / "backup").iterdir())


def test_existing_destination_never_changes(tmp_path):
    existing = tmp_path / "exists"
    existing.mkdir()
    sentinel = existing / "manifest.json"
    sentinel.write_bytes(b"original")
    with pytest.raises(backup.BackupError, match="destination_exists"):
        backup.PrivateOutput(existing)
    assert sentinel.read_bytes() == b"original"


def test_sqlite_nullable_primary_key_has_explicit_rowid_tiebreak(tmp_path):
    source = tmp_path / "nullable.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE x (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO x VALUES (?,?)", [(None, "a"), (None, "b")])
    manifest = backup.backup_sqlite(source, tmp_path / "backup")
    assert manifest["content"]["tables"][0]["ordering"] == "primary_key_then_rowid"
    with sqlite3.connect(tmp_path / "backup/database.sqlite3") as connection:
        assert backup.sqlite_content_manifest(connection) == manifest["content"]


def test_sqlite_all_rowid_aliases_shadowed_without_a_primary_key_fails_closed(tmp_path):
    source = tmp_path / "shadowed.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE x (rowid TEXT, _rowid_ TEXT, oid TEXT)")
    with pytest.raises(backup.BackupError, match="sqlite_table_without_stable_order"):
        backup.backup_sqlite(source, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_symlink_source_destination_and_parent_are_refused(tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE x (id INTEGER PRIMARY KEY)")
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(source)
    with pytest.raises(backup.BackupError, match="symlink_refused"):
        backup.backup_sqlite(alias, tmp_path / "new")
    directory_alias = tmp_path / "alias-dir"
    directory_alias.symlink_to(tmp_path, target_is_directory=True)
    for target in (directory_alias, directory_alias / "new"):
        with pytest.raises(backup.BackupError, match="symlink_refused"):
            backup.PrivateOutput(target)
    assert not (tmp_path / "new").exists()


def test_cleanup_does_not_delete_replacement_inodes(tmp_path):
    workspace = backup.PrivateOutput(tmp_path / "new")
    with workspace.create("owned") as stream:
        stream.write(b"owned")
    original = workspace.path / "owned"
    original.rename(workspace.path / "original-moved")
    original.write_bytes(b"replacement")
    workspace.cleanup()
    assert original.read_bytes() == b"replacement"
    assert (workspace.path / "original-moved").read_bytes() == b"owned"


def test_sqlite_integrity_failure_cleans_owned_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE x (id INTEGER PRIMARY KEY)")

    def fail(*_args, **_kwargs):
        raise backup.BackupError("sqlite_integrity_check_failed")

    monkeypatch.setattr(backup, "sqlite_content_manifest", fail)
    with pytest.raises(backup.BackupError, match="sqlite_integrity_check_failed"):
        backup.backup_sqlite(source, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_cli_success_is_only_json_and_errors_never_print_private_diagnostics(
    tmp_path, capsys, monkeypatch
):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE x (id INTEGER PRIMARY KEY)")
    assert (
        backup.main(
            ["sqlite", "--sqlite-source", str(source), "--output-dir", str(tmp_path / "new")]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "completed" and captured.err == ""

    def fail(*_args, **_kwargs):
        raise RuntimeError(DATABASE_URL)

    monkeypatch.setattr(backup, "backup_postgres", fail)
    assert backup.main(["postgresql", "--output-dir", str(tmp_path / "pg")]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error_code"] == "backup_failed"
    assert FIXTURE_PASSWORD not in captured.err and "postgresql://" not in captured.err
    assert backup.main([DATABASE_URL]) == 1
    captured = capsys.readouterr()
    assert FIXTURE_PASSWORD not in captured.err
    assert json.loads(captured.err)["error_code"] == "invalid_arguments"
