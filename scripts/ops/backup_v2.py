"""Create private, consistent database backups without loading dotenv files.

PostgreSQL credentials come only from DATABASE_URL in this process's environment.
The dump and content manifest share one exported read-only snapshot. PostgreSQL
content manifests require primary keys and can be recomputed after restoration.
SQLite manifests are computed from the completed backup, never a later source view.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

CONNECTION_ENV = {
    "dbname": "PGDATABASE",
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "options": "PGOPTIONS",
    "sslmode": "PGSSLMODE",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "sslpassword": "PGSSLPASSWORD",
    "channel_binding": "PGCHANNELBINDING",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "gssencmode": "PGGSSENCMODE",
    "krbsrvname": "PGKRBSRVNAME",
}
TABLE_QUERY = """
SELECT c.relname,
       array_agg(a.attname ORDER BY k.ordinality) FILTER (WHERE a.attname IS NOT NULL),
       array_agg(a.attcollation <> 0 ORDER BY k.ordinality)
           FILTER (WHERE a.attname IS NOT NULL)
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_index AS i ON i.indrelid = c.oid AND i.indisprimary
LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
    ON k.ordinality <= i.indnkeyatts
LEFT JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
GROUP BY c.oid, c.relname
ORDER BY c.relname COLLATE "C"
"""
PG_CANONICAL_SETTINGS = (
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL DateStyle = 'ISO, YMD'",
    "SET LOCAL IntervalStyle = 'postgres'",
    "SET LOCAL extra_float_digits = 3",
    "SET LOCAL bytea_output = 'hex'",
)


class BackupError(Exception):
    """Only fixed, non-sensitive codes may reach operator output."""


def checked_path(value: Path) -> Path:
    if ".." in value.parts:
        raise BackupError("parent_traversal_refused")
    path = value if value.is_absolute() else Path.cwd() / value
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise BackupError("symlink_refused")
    return path


class PrivateOutput:
    """An exclusive new directory; cleanup only files with our original inodes."""

    def __init__(self, value: Path):
        self.path = checked_path(value)
        self.files: dict[str, tuple[int, int]] = {}
        self.complete = False
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            raise BackupError("destination_exists") from None
        self.path.chmod(0o700)
        info = self.path.stat()
        self.identity = (info.st_dev, info.st_ino)

    def create(self, name: str):
        if Path(name).name != name:
            raise BackupError("invalid_artifact_name")
        path = self.path / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        self.files[name] = (info.st_dev, info.st_ino)
        return os.fdopen(descriptor, "wb")

    def cleanup(self):
        if self.complete:
            return
        for name, identity in self.files.items():
            path = self.path / name
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == identity and stat.S_ISREG(info.st_mode):
                    path.unlink()
            except FileNotFoundError:
                pass
        try:
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) == self.identity and stat.S_ISDIR(info.st_mode):
                self.path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_deadline(deadline: float | None):
    if deadline is not None and time.monotonic() >= deadline:
        raise BackupError("backup_timeout")


def connection_options(environment) -> dict[str, str]:
    value = environment.get("DATABASE_URL", "")
    if value.startswith("postgresql+psycopg://"):
        value = value.replace("postgresql+psycopg://", "postgresql://", 1)
    if not value.startswith(("postgresql://", "postgres://")):
        raise BackupError("database_url_missing_or_unsupported")
    options = conninfo_to_dict(value)
    if not options.get("dbname"):
        raise BackupError("database_name_required")
    if set(options) - set(CONNECTION_ENV):
        raise BackupError("unsupported_connection_option")
    options.setdefault("connect_timeout", "15")
    options["application_name"] = "jixia-v2-readonly-backup"
    return options


@contextmanager
def without_inherited_pg_environment():
    """libpq must not silently supplement the explicit DATABASE_URL with PG* vars."""
    saved = {key: value for key, value in os.environ.items() if key.startswith("PG")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        yield
    finally:
        for key in tuple(os.environ):
            if key.startswith("PG"):
                os.environ.pop(key, None)
        os.environ.update(saved)


def dump_environment(options: dict[str, str], environment) -> dict[str, str]:
    result = {
        key: environment[key]
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT")
        if key in environment
    }
    result.update({CONNECTION_ENV[key]: value for key, value in options.items()})
    result["PGPASSFILE"] = os.devnull
    return result


def begin_postgres_readonly(connection, timeout_seconds: int):
    """Connection must be opened with autocommit=True so BEGIN is the first txn."""
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    connection.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(timeout_seconds * 1000))
    connection.execute("SET LOCAL lock_timeout = '5s'")


def postgres_content_manifest(connection, *, deadline: float | None = None) -> dict:
    """Recompute this content object in a read-only RR transaction after restore."""
    if connection.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise BackupError("readonly_transaction_required")
    if connection.execute("SHOW transaction_isolation").fetchone()[0] != "repeatable read":
        raise BackupError("repeatable_read_required")
    for command in PG_CANONICAL_SETTINGS:
        connection.execute(command)
    tables = connection.execute(TABLE_QUERY).fetchall()
    for name, primary_key, _ in tables:
        if not primary_key:
            raise BackupError("public_table_without_primary_key")
        connection.execute(
            sql.SQL("LOCK TABLE ONLY {} IN ACCESS SHARE MODE").format(
                sql.Identifier("public", name)
            )
        )
    records = []
    for name, primary_key, collatable in tables:
        check_deadline(deadline)
        ordering = [
            sql.SQL('{} COLLATE "C"').format(sql.Identifier(key))
            if is_text
            else sql.Identifier(key)
            for key, is_text in zip(primary_key, collatable, strict=True)
        ]
        statement = sql.SQL("SELECT to_jsonb(t)::text FROM {} AS t ORDER BY {}").format(
            sql.Identifier("public", name), sql.SQL(", ").join(ordering)
        )
        digest, count = hashlib.sha256(), 0
        with connection.cursor(name="jx_backup_" + uuid4().hex) as cursor:
            cursor.itersize = 256
            cursor.execute(statement)
            for (row_json,) in cursor:
                check_deadline(deadline)
                digest.update(row_json.encode("utf-8") + b"\n")
                count += 1
        records.append(
            {
                "schema": "public",
                "table": name,
                "primary_key": primary_key,
                "pk_collation_c": collatable,
                "row_count": count,
                "rows_sha256": digest.hexdigest(),
            }
        )
    heads = []
    if any(name == "alembic_version" for name, _, _ in tables):
        heads = [
            row[0]
            for row in connection.execute(
                'SELECT version_num FROM public.alembic_version ORDER BY version_num COLLATE "C"'
            ).fetchall()
        ]
    return {
        "table_scope": "all public ordinary and partitioned tables, including partition children",
        "row_encoding": "PostgreSQL to_jsonb(row)::text UTF-8 plus LF, ordered by primary key",
        "canonical_settings": list(PG_CANONICAL_SETTINGS),
        "tables": records,
        "migration_heads": heads,
    }


def finish_manifest(workspace: PrivateOutput, mode: str, artifact: str, content: dict, **extra):
    path = workspace.path / artifact
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    manifest = {
        "format": "jixia-v2-private-database-backup-v1",
        "mode": mode,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "artifact": {"file": artifact, "bytes": path.stat().st_size, "sha256": file_sha(path)},
        "content": content,
        **extra,
    }
    with workspace.create("manifest.json") as stream:
        stream.write((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(workspace.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    workspace.complete = True
    return manifest


def backup_postgres(
    destination: Path,
    *,
    timeout_seconds: int = 900,
    pg_dump: str = "pg_dump",
    environment=None,
    connect=None,
    runner=None,
):
    environment = dict(os.environ if environment is None else environment)
    options = connection_options(environment)
    workspace = PrivateOutput(destination)
    connection = None
    deadline = time.monotonic() + timeout_seconds
    try:
        with without_inherited_pg_environment():
            connection = (connect or psycopg.connect)(
                **options, passfile=os.devnull, autocommit=True
            )
        begin_postgres_readonly(connection, timeout_seconds)
        snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        content = postgres_content_manifest(connection, deadline=deadline)
        check_deadline(deadline)
        with workspace.create("database.dump") as stream:
            completed = (runner or subprocess.run)(
                [pg_dump, "-Fc", "--no-owner", "--no-acl", "--snapshot=" + snapshot],
                stdout=stream,
                stderr=subprocess.DEVNULL,
                env=dump_environment(options, environment),
                timeout=max(1, deadline - time.monotonic()),
                check=False,
            )
            if completed.returncode != 0:
                raise BackupError("pg_dump_failed")
            stream.flush()
            os.fsync(stream.fileno())
        with (workspace.path / "database.dump").open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise BackupError("invalid_custom_dump_header")
        connection.execute("ROLLBACK")
        connection.close()
        connection = None
        return finish_manifest(
            workspace,
            "postgresql",
            "database.dump",
            content,
            snapshot_consistency="pg_export_snapshot shared by manifest and pg_dump",
            dump_format="custom --no-owner --no-acl; full database dump",
        )
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        workspace.cleanup()


def sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sqlite_typed_row(row) -> bytes:
    values = []
    for value in row:
        if value is None:
            values.append(["null", None])
        elif isinstance(value, bytes):
            values.append(["blob", value.hex()])
        elif isinstance(value, float):
            values.append(["real", value.hex()])
        elif isinstance(value, int):
            values.append(["integer", value])
        elif isinstance(value, str):
            values.append(["text", value])
        else:
            raise BackupError("unsupported_sqlite_value")
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def sqlite_content_manifest(connection, *, deadline: float | None = None) -> dict:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise BackupError("sqlite_integrity_check_failed")
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name COLLATE BINARY"
    ).fetchall()
    without_rowid = {
        row[1]: bool(row[4]) for row in connection.execute("PRAGMA table_list") if row[0] == "main"
    }
    records = []
    for (name,) in tables:
        check_deadline(deadline)
        info = connection.execute("PRAGMA table_xinfo(" + sqlite_identifier(name) + ")").fetchall()
        primary_key = [row[1] for row in sorted(info, key=lambda item: item[5]) if row[5]]
        columns = {row[1].casefold() for row in info}
        rowid = None
        if not without_rowid.get(name, True):
            rowid = next((key for key in ("rowid", "_rowid_", "oid") if key not in columns), None)
        if primary_key:
            ordering = [sqlite_identifier(key) + " COLLATE BINARY" for key in primary_key]
            order_kind = "primary_key"
            if rowid is not None:
                ordering.append(rowid)
                order_kind = "primary_key_then_rowid"
            elif not without_rowid.get(name, True) and any(not row[3] for row in info if row[5]):
                raise BackupError("sqlite_nullable_primary_key_without_rowid_tiebreak")
        else:
            if rowid is None:
                raise BackupError("sqlite_table_without_stable_order")
            ordering, order_kind = [rowid], "rowid"
        statement = "SELECT * FROM " + sqlite_identifier(name) + " ORDER BY " + ", ".join(ordering)
        digest, count = hashlib.sha256(), 0
        for row in connection.execute(statement):
            check_deadline(deadline)
            digest.update(sqlite_typed_row(row))
            count += 1
        records.append(
            {
                "table": name,
                "primary_key": primary_key,
                "ordering": order_kind,
                "row_count": count,
                "rows_sha256": digest.hexdigest(),
            }
        )
    return {
        "integrity_check": "ok",
        "row_encoding": "typed SQLite values as compact UTF-8 JSON plus LF",
        "tables": records,
    }


def backup_sqlite(source: Path, destination: Path, *, timeout_seconds: int = 900):
    source = checked_path(source)
    if not source.is_file():
        raise BackupError("sqlite_source_missing")
    workspace = PrivateOutput(destination)
    origin = target = None
    deadline = time.monotonic() + timeout_seconds
    try:
        with workspace.create("database.sqlite3"):
            pass
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=15)
        origin.execute("PRAGMA query_only=ON")
        target = sqlite3.connect(workspace.path / "database.sqlite3", timeout=15)
        target.execute("PRAGMA journal_mode=OFF")

        def progress(_status, _remaining, _total):
            check_deadline(deadline)

        origin.backup(target, pages=256, progress=progress, sleep=0.01)
        target.execute("PRAGMA query_only=ON")
        target.execute("BEGIN")
        content = sqlite_content_manifest(target, deadline=deadline)
        target.close()
        target = None
        origin.close()
        origin = None
        return finish_manifest(
            workspace,
            "sqlite",
            "database.sqlite3",
            content,
            snapshot_consistency="SQLite backup API; all metadata read from completed backup",
        )
    finally:
        for connection in (target, origin):
            if connection is not None:
                connection.close()
        workspace.cleanup()


def main(argv=None) -> int:
    class SafeArgumentParser(argparse.ArgumentParser):
        def error(self, _message):
            raise BackupError("invalid_arguments")

    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("postgresql", "sqlite"))
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sqlite-source", type=Path)
    parser.add_argument("--pg-dump", default="pg_dump")
    parser.add_argument("--timeout-seconds", default=900, type=int)
    try:
        args = parser.parse_args(argv)
        if not 1 <= args.timeout_seconds <= 86400:
            raise BackupError("invalid_timeout")
        if args.mode == "postgresql":
            if args.sqlite_source is not None:
                raise BackupError("unexpected_sqlite_source")
            manifest = backup_postgres(
                args.output_dir, timeout_seconds=args.timeout_seconds, pg_dump=args.pg_dump
            )
        else:
            if args.sqlite_source is None:
                raise BackupError("sqlite_source_required")
            manifest = backup_sqlite(
                args.sqlite_source, args.output_dir, timeout_seconds=args.timeout_seconds
            )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "mode": manifest["mode"],
                    "manifest_sha256": file_sha(args.output_dir / "manifest.json"),
                }
            )
        )
        return 0
    except (Exception, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, BackupError) else "backup_failed"
        print(json.dumps({"status": "failed", "error_code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
