"""Portable, credential-free Spec327 resources; writes only an explicitly fresh local DB.

No Settings/dotenv, provider client, service control, or background-task creation.
Assets are pre-staged under --asset-root; HOST_AUDIO_STORAGE_DIR must use that root.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

SCHEMA = "jx-v2-resources-v1"
HEAD = "0049_agent_human_avatars"
RELEASE_SHA = "84c44b62297dfd815b4e695cd0ff182c9c387b74483f9955d1da88e920238f43"
RULE_REVISIONS = {
    "29ea9080-5a66-43ee-9c7d-984a18dbfd21": 146,
    "d2e24b41-47d1-4d33-bba8-ee6c54ff5078": 275,
}
FIELDS = {
    "model_profiles": (
        "id name config_ref base_url model_id max_concurrency token_per_char "
        "generation_params capability_schema status modelEnvKey"
    ),
    "voice_profiles": (
        "id name kind provider_voice rate chars_per_second playback_gain "
        "avatar_key calibration_status calibrated_at status"
    ),
    "rules": (
        "id rule_key version name description side_size estimated_seconds status "
        "lifecycle_status host_voice_profile_id default_agent_model_profile_id "
        "topic_policy config_revision postmatch_questionnaire_enabled "
        "historical_read_only created_by audio_reviewed_at "
        "current_audio_generation_id"
    ),
    "rule_stages": (
        "id rule_id position name stage_kind duration_seconds start_host_text "
        "end_host_text parameters"
    ),
    "stage_actions": ("id stage_id position action_kind side seat_no duration_seconds parameters"),
    "agent_profiles": (
        "id name format_version_id rule_id model_profile_id voice_profile_id "
        "system_prompt debater_prompt generation_params status"
    ),
    "stage_prompt_templates": ("id stage_id purpose template_text variables output_contract"),
    "agent_prompt_overrides": (
        "id agent_profile_id stage_id purpose template_text variables output_contract"
    ),
    "rule_judge_configs": ("rule_id enabled model_profile_id judge_prompt include_in_leaderboard"),
    "rule_audio_generations": (
        "id rule_id config_revision manifest_hash manifest status review_status "
        "requested_by requested_at started_at completed_at approved_by "
        "approved_at total_count ready_count failed_count last_error_code "
        "is_current"
    ),
    "host_audio_assets": (
        "id rule_id generation_id segment_key text text_hash voice_profile_id "
        "storage_path status error_code input_hash input_snapshot byte_count "
        "checksum duration_ms"
    ),
    "judge_profiles": ("id model_profile_id system_prompt judge_prompt generation_params status"),
}
FIELDS = {name: tuple(value.split()) for name, value in FIELDS.items()}
TIMESTAMPS = {"created_at", "updated_at"}
ACTORS = {"created_by", "requested_by", "approved_by"}
DENIED = {
    "password",
    "password_hash",
    "api_key",
    "api_key_ciphertext",
    "api_key_nonce",
    "api_key_last4",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "token",
    "apiKey",
    "credential",
    "credentials",
    "client_secret",
    "private_key",
    "passphrase",
    "auth",
    "cookie",
    "headers",
}
DENIED_NORMALIZED = {re.sub(r"[^a-z0-9]", "", key.casefold()) for key in DENIED}
GENERATION_FIELDS = {"temperature", "top_p", "max_tokens"}
STAGE_PARAMETER_FIELDS = {"max_speech_seconds", "starting_side"}
INPUT_FIELDS = {
    "text",
    "text_hash",
    "voice_profile_id",
    "provider_voice",
    "rate",
    "model",
    "workspace_id",
    "websocket_url",
    "format",
    "sample_rate",
    "bit_rate",
}


class Refusal(ValueError):
    """Only fixed, non-sensitive error codes escape the CLI."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refusal(code)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def no_secrets(value: Any) -> None:
    if isinstance(value, dict):
        keys = {re.sub(r"[^a-z0-9]", "", str(key).casefold()) for key in value}
        require(not (keys & DENIED_NORMALIZED), "secret_field")
        for item in value.values():
            no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            no_secrets(item)
    elif isinstance(value, str):
        require(
            not re.search(
                r"sk-[A-Za-z0-9_.-]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,}",
                value,
                flags=re.IGNORECASE,
            ),
            "secret_value",
        )


def validate_public_json(name: str, row: dict[str, Any]) -> None:
    """Public baseline JSON is a closed configuration schema, not an opaque bag."""
    if "generation_params" in row:
        parameters = row["generation_params"]
        require(
            isinstance(parameters, dict) and set(parameters) <= GENERATION_FIELDS,
            "generation_parameter_fields",
        )
        for key, value in parameters.items():
            require(type(value) in (float, int), "generation_parameter_value")
            if key == "max_tokens":
                require(type(value) is int and 1 <= value <= 32768, "generation_parameter_value")
            else:
                maximum = 2 if key == "temperature" else 1
                require(0 <= value <= maximum, "generation_parameter_value")
    if name == "model_profiles":
        capabilities = row["capability_schema"]
        require(
            isinstance(capabilities, dict) and set(capabilities) <= GENERATION_FIELDS,
            "capability_fields",
        )
        for key, limits in capabilities.items():
            if limits is None:
                continue
            require(
                isinstance(limits, dict) and set(limits) == {"minimum", "maximum"},
                "capability_limit_fields",
            )
            minimum, maximum = limits["minimum"], limits["maximum"]
            require(
                type(minimum) in (int, float) and type(maximum) in (int, float),
                "capability_limit_value",
            )
            if key == "max_tokens":
                require(
                    type(minimum) is type(maximum) is int and 1 <= minimum <= maximum <= 32768,
                    "capability_limit_value",
                )
            else:
                require(
                    0 <= minimum <= maximum <= (2 if key == "temperature" else 1),
                    "capability_limit_value",
                )
    if "parameters" in row:
        parameters = row["parameters"]
        allowed = STAGE_PARAMETER_FIELDS if name == "rule_stages" else set()
        require(
            isinstance(parameters, dict) and set(parameters) <= allowed,
            "stage_parameter_fields",
        )
        if "max_speech_seconds" in parameters:
            seconds = parameters["max_speech_seconds"]
            require(type(seconds) is int and 1 <= seconds <= 180, "stage_parameter_value")
        if "starting_side" in parameters:
            require(
                parameters["starting_side"] in ("AFFIRMATIVE", "NEGATIVE"),
                "stage_parameter_value",
            )


def public_url(value: str | None) -> None:
    if value is None:
        return
    parsed = urlsplit(value)
    require(
        parsed.scheme in ("http", "https", "ws", "wss")
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment,
        "endpoint_not_public",
    )


def relative_asset(value: str) -> str:
    path = PurePosixPath(value)
    require(
        isinstance(value, str)
        and "\\" not in value
        and not path.is_absolute()
        and path.parts
        and ".." not in path.parts
        and path.as_posix() == value
        and path.parts[0] in ("host-audio", "avatars"),
        "asset_path",
    )
    return value


def export_bundle(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a separately reviewed field-whitelist export, never a raw DB dump."""
    require(set(raw) == {"sourceInfo", "tables", "assets"}, "source_schema")
    no_secrets(raw)
    tables = raw["tables"]
    require(set(tables) == set(FIELDS), "source_tables")
    rules = [r for r in tables["rules"] if r["id"] in RULE_REVISIONS]
    rule_ids = {r["id"] for r in rules}
    stages = [r for r in tables["rule_stages"] if r["rule_id"] in rule_ids]
    stage_ids = {r["id"] for r in stages}
    agents = [r for r in tables["agent_profiles"] if r["rule_id"] in rule_ids]
    current_ids = {r["current_audio_generation_id"] for r in rules}
    assets = [r for r in tables["host_audio_assets"] if r["generation_id"] in current_ids]
    judges = [r for r in tables["rule_judge_configs"] if r["rule_id"] in rule_ids]
    global_judges = [r for r in tables["judge_profiles"] if r["status"] == "ENABLED"]
    model_ids = {r["model_profile_id"] for r in agents + judges + global_judges}
    model_ids |= {r["default_agent_model_profile_id"] for r in rules}
    voice_ids = {r["voice_profile_id"] for r in agents + assets}
    voice_ids |= {r["host_voice_profile_id"] for r in rules}
    selected = {
        "rules": rules,
        "rule_stages": stages,
        "agent_profiles": agents,
        "host_audio_assets": assets,
        "rule_judge_configs": judges,
        "judge_profiles": global_judges,
        "stage_actions": [r for r in tables["stage_actions"] if r["stage_id"] in stage_ids],
        "stage_prompt_templates": [
            r for r in tables["stage_prompt_templates"] if r["stage_id"] in stage_ids
        ],
        "agent_prompt_overrides": [
            r for r in tables["agent_prompt_overrides"] if r["stage_id"] in stage_ids
        ],
        "rule_audio_generations": [
            r for r in tables["rule_audio_generations"] if r["id"] in current_ids
        ],
        "model_profiles": [r for r in tables["model_profiles"] if r["id"] in model_ids],
        "voice_profiles": [r for r in tables["voice_profiles"] if r["id"] in voice_ids],
    }
    manifest = copy.deepcopy(raw["assets"])
    host_files = {r["id"]: r["path"] for r in manifest if r["kind"] == "host_audio"}
    projected = {}
    for name in FIELDS:
        projected[name] = []
        for source in selected[name]:
            require(
                set(source) <= set(FIELDS[name]) | TIMESTAMPS | {"active_task_id"},
                "source_unknown_field",
            )
            row = {k: copy.deepcopy(source[k]) for k in FIELDS[name] if k in source}
            if name == "model_profiles":
                row["modelEnvKey"] = source.get("modelEnvKey", "JX_LLM_API_KEY")
            for field in ACTORS & row.keys():
                row[field] = "@admin" if row[field] is not None else None
            if name == "host_audio_assets":
                require(row["id"] in host_files, "host_manifest_missing")
                row["storage_path"] = host_files[row["id"]]
            projected[name].append(row)
        projected[name].sort(key=lambda r: r.get("id", r.get("rule_id")))
    bundle = {
        "schemaVersion": SCHEMA,
        "sourceInfo": copy.deepcopy(raw["sourceInfo"]),
        "tables": projected,
        "assets": sorted(manifest, key=lambda r: (r["kind"], r["id"])),
    }
    bundle["sourceInfo"]["sourceConfigurationSha256"] = fingerprint(tables)
    bundle["contentSha256"] = fingerprint(bundle)
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: dict[str, Any]) -> None:
    require(
        set(bundle) == {"schemaVersion", "sourceInfo", "tables", "assets", "contentSha256"}
        and bundle["schemaVersion"] == SCHEMA,
        "bundle_schema",
    )
    no_secrets(bundle)
    body = {k: v for k, v in bundle.items() if k != "contentSha256"}
    require(fingerprint(body) == bundle["contentSha256"], "bundle_hash")
    info = bundle["sourceInfo"]
    require(
        set(info)
        == {
            "spec",
            "migrationHead",
            "releaseReceiptSha256",
            "ruleRevisions",
            "sourceConfigurationSha256",
        },
        "source_info_fields",
    )
    require(
        info["spec"] == 327
        and info["migrationHead"] == HEAD
        and info["releaseReceiptSha256"] == RELEASE_SHA
        and info["ruleRevisions"] == RULE_REVISIONS
        and re.fullmatch(r"[0-9a-f]{64}", info["sourceConfigurationSha256"]),
        "source_info",
    )
    tables = bundle["tables"]
    require(set(tables) == set(FIELDS), "bundle_tables")
    index = {}
    for name, fields in FIELDS.items():
        require(isinstance(tables[name], list), "table_rows")
        index[name] = {}
        for row in tables[name]:
            require(set(row) == set(fields), "row_fields")
            validate_public_json(name, row)
            key = row.get("id", row.get("rule_id"))
            require(str(UUID(key)) == key and key not in index[name], "row_identity")
            index[name][key] = row
            for field in ACTORS & row.keys():
                require(row[field] in (None, "@admin"), "production_actor")
    require(set(index["rules"]) == set(RULE_REVISIONS), "rule_scope")
    for r in tables["rules"]:
        require(
            r["side_size"] == 4
            and r["status"] == r["lifecycle_status"] == "ENABLED"
            and r["historical_read_only"] is False
            and r["created_by"] == "@admin"
            and r["config_revision"] == RULE_REVISIONS[r["id"]],
            "rule_configuration",
        )
    require(
        len(tables["stage_prompt_templates"]) + len(tables["agent_prompt_overrides"]) == 198,
        "prompt_count",
    )
    refs = {
        "rules": {
            "host_voice_profile_id": "voice_profiles",
            "default_agent_model_profile_id": "model_profiles",
            "current_audio_generation_id": "rule_audio_generations",
        },
        "rule_stages": {"rule_id": "rules"},
        "stage_actions": {"stage_id": "rule_stages"},
        "agent_profiles": {
            "rule_id": "rules",
            "model_profile_id": "model_profiles",
            "voice_profile_id": "voice_profiles",
        },
        "stage_prompt_templates": {"stage_id": "rule_stages"},
        "agent_prompt_overrides": {"stage_id": "rule_stages", "agent_profile_id": "agent_profiles"},
        "rule_judge_configs": {"rule_id": "rules", "model_profile_id": "model_profiles"},
        "rule_audio_generations": {"rule_id": "rules"},
        "host_audio_assets": {
            "rule_id": "rules",
            "generation_id": "rule_audio_generations",
            "voice_profile_id": "voice_profiles",
        },
        "judge_profiles": {"model_profile_id": "model_profiles"},
    }
    for name, columns in refs.items():
        for row in tables[name]:
            for column, target in columns.items():
                require(
                    row[column] is None
                    and name == "rule_judge_configs"
                    and not row["enabled"]
                    or row[column] in index[target],
                    "dangling_reference",
                )
    for row in tables["agent_profiles"]:
        require(row["format_version_id"] is None, "historical_format_reference")
    for row in tables["model_profiles"]:
        require(
            re.fullmatch(r"JX_[A-Z0-9_]{1,80}_API_KEY", row["modelEnvKey"])
            and row["status"] in ("ENABLED", "DISABLED"),
            "model_environment_key",
        )
        public_url(row["base_url"])
    for name in ("stage_prompt_templates", "agent_prompt_overrides"):
        for row in tables[name]:
            from jx_core.rules.prompts import (
                FIXED_SPEECH_VARIABLES,
                FREE_DECISION_VARIABLES,
                FREE_SPEECH_VARIABLES,
                extract_prompt_variables,
                validate_prompt_template,
            )

            require(
                sorted(extract_prompt_variables(row["template_text"])) == row["variables"],
                "template_variables",
            )
            stage = index["rule_stages"][row["stage_id"]]
            require(
                stage["stage_kind"] in ("FIXED_SPEECH", "FREE_DEBATE")
                and (row["purpose"] != "DECISION" or stage["stage_kind"] == "FREE_DEBATE"),
                "prompt_stage",
            )
            required = (
                FREE_DECISION_VARIABLES
                if row["purpose"] == "DECISION"
                else (
                    FREE_SPEECH_VARIABLES
                    if stage["stage_kind"] == "FREE_DEBATE"
                    else FIXED_SPEECH_VARIABLES
                )
            )
            validate_prompt_template(row["template_text"], required_variables=required)
            require(
                row["purpose"] in ("SPEECH", "DECISION")
                and row["output_contract"]
                == ("TEXT" if row["purpose"] == "SPEECH" else "SHOULD_SPEAK_V1"),
                "contract",
            )
            if name == "agent_prompt_overrides":
                require(
                    index["agent_profiles"][row["agent_profile_id"]]["rule_id"]
                    == index["rule_stages"][row["stage_id"]]["rule_id"],
                    "cross_rule_prompt",
                )
    asset_index = {}
    for entry in bundle["assets"]:
        require(
            set(entry) == {"id", "kind", "path", "sha256", "bytes", "duration_ms"}
            and entry["kind"] in ("host_audio", "avatar"),
            "asset_fields",
        )
        key = (entry["kind"], entry["id"])
        require(
            key not in asset_index
            and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            and type(entry["bytes"]) is int
            and entry["bytes"] > 0,
            "asset_identity",
        )
        relative_asset(entry["path"])
        asset_index[key] = entry
    required_assets = set()
    for voice in tables["voice_profiles"]:
        if voice["avatar_key"]:
            required_assets.add(("avatar", voice["avatar_key"]))
    for asset in tables["host_audio_assets"]:
        key = ("host_audio", asset["id"])
        require(key in asset_index, "host_asset_missing")
        required_assets.add(key)
        entry = asset_index[key]
        require(
            asset["status"] == "READY"
            and asset["error_code"] is None
            and asset["storage_path"] == entry["path"]
            and asset["checksum"] == entry["sha256"]
            and asset["byte_count"] == entry["bytes"]
            and asset["duration_ms"] == entry["duration_ms"]
            and type(asset["duration_ms"]) is int
            and asset["duration_ms"] > 0
            and hashlib.sha256(asset["text"].encode()).hexdigest() == asset["text_hash"],
            "host_asset_metadata",
        )
        require(
            set(asset["input_snapshot"]) == INPUT_FIELDS
            and fingerprint(asset["input_snapshot"]) == asset["input_hash"],
            "host_input_hash",
        )
        public_url(asset["input_snapshot"]["websocket_url"])
        require(
            asset["text"] == asset["input_snapshot"]["text"]
            and asset["text_hash"] == asset["input_snapshot"]["text_hash"]
            and asset["voice_profile_id"] == asset["input_snapshot"]["voice_profile_id"],
            "host_input_identity",
        )
    require(set(asset_index) == required_assets, "asset_scope")
    for generation in tables["rule_audio_generations"]:
        require(
            generation["is_current"] is True
            and generation["status"] == "SUCCEEDED"
            and generation["review_status"] == "APPROVED"
            and generation["failed_count"] == 0
            and generation["last_error_code"] is None
            and fingerprint(generation["manifest"]) == generation["manifest_hash"],
            "generation_not_ready",
        )
        require(
            set(generation["manifest"]) == {"schema", "segments"}
            and generation["manifest"]["schema"] == "host-audio-v1",
            "generation_manifest",
        )
        assets = [r for r in tables["host_audio_assets"] if r["generation_id"] == generation["id"]]
        require(
            generation["total_count"]
            == generation["ready_count"]
            == len(assets)
            == len(generation["manifest"]["segments"]),
            "generation_count",
        )
        require(
            index["rules"][generation["rule_id"]]["current_audio_generation_id"]
            == generation["id"],
            "generation_rule",
        )
        by_segment = {asset["segment_key"]: asset for asset in assets}
        require(len(by_segment) == len(assets), "duplicate_host_segment")
        for segment in generation["manifest"]["segments"]:
            require(
                set(segment)
                == {"stage_id", "boundary", "position", "segment_key", "input_hash", "input"}
                and segment["stage_id"] in index["rule_stages"],
                "manifest_segment",
            )
            stage = index["rule_stages"][segment["stage_id"]]
            require(
                stage["rule_id"] == generation["rule_id"]
                and segment["boundary"] in ("start", "end")
                and segment["position"] == stage["position"]
                and segment["segment_key"] in by_segment,
                "manifest_stage",
            )
            asset = by_segment[segment["segment_key"]]
            require(
                asset["rule_id"] == generation["rule_id"]
                and segment["input"] == asset["input_snapshot"]
                and segment["input_hash"] == asset["input_hash"]
                and stage[segment["boundary"] + "_host_text"] == asset["text"],
                "manifest_asset",
            )


def verify_assets(bundle: dict[str, Any], root: Path) -> None:
    root = root.resolve(strict=True)
    for entry in bundle["assets"]:
        path = root / relative_asset(entry["path"])
        require(
            not any(p.is_symlink() for p in (path, *path.parents))
            and path.is_file()
            and path.resolve().is_relative_to(root),
            "asset_file",
        )
        with path.open("rb") as stream:
            require(
                path.stat().st_size == entry["bytes"]
                and hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"],
                "asset_file_hash",
            )


def database_guard(url: str, expected: str) -> None:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    require(
        parsed.drivername == "postgresql+psycopg"
        and parsed.host in ("127.0.0.1", "localhost")
        and parsed.database == expected
        and re.fullmatch(r"jx_(?:v2|spec328)_[a-z0-9_]{8,80}", expected)
        and not parsed.query,
        "fresh_local_database_required",
    )


async def fresh_target(connection: Any) -> str:
    from sqlalchemy import func, select, text

    from jx_core.config import CORE_INSTANCE_LOCK_KEY
    from jx_core.models import Base

    # Explicitly put pg_temp last: otherwise PostgreSQL searches it before public.
    await connection.execute(text("SET LOCAL search_path = public, pg_catalog, pg_temp"))
    require(
        await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": CORE_INSTANCE_LOCK_KEY}
        ),
        "core_or_import_running",
    )
    relations = (
        await connection.execute(
            text(
                "SELECT c.relname, c.relkind FROM pg_catalog.pg_class AS c "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','f','v','m')"
            )
        )
    ).all()
    expected_tables = set(Base.metadata.tables) | {"alembic_version"}
    require(
        {row[0] for row in relations} == expected_tables
        and all(row[1] == "r" for row in relations),
        "target_schema_not_exact",
    )
    for name in sorted(expected_tables):
        await connection.execute(
            text(f'LOCK TABLE public."{name}" IN SHARE ROW EXCLUSIVE MODE NOWAIT')
        )
    require(
        await connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD,
        "migration_head",
    )
    require(
        await connection.scalar(text("SELECT count(*) FROM alembic_version")) == 1,
        "migration_multiple_heads",
    )
    users = Base.metadata.tables["users"]
    rows = (await connection.execute(select(users.c.id, users.c.role, users.c.status))).all()
    require(
        len(rows) == 1 and rows[0].role == "ADMIN" and rows[0].status == "ACTIVE", "fresh_admin"
    )
    admin = str(rows[0].id)
    for name, table in Base.metadata.tables.items():
        if name in ("users", "audit_logs", "capacity_guards"):
            continue
        require(
            await connection.scalar(select(func.count()).select_from(table)) == 0,
            "target_not_empty",
        )
    audit = Base.metadata.tables["audit_logs"]
    audit_rows = (await connection.execute(select(audit))).mappings().all()
    require(
        len(audit_rows) <= 1
        and all(
            r["action"] == "admin.created"
            and r["target_id"] == admin
            and r["actor_user_id"] is None
            for r in audit_rows
        ),
        "target_has_audit_history",
    )
    return admin


def typed_row(table: Any, row: dict[str, Any], admin: str) -> dict[str, Any]:
    from sqlalchemy import DateTime
    from sqlalchemy.dialects.postgresql import UUID as PGUUID

    result = {}
    for key, value in row.items():
        if key == "modelEnvKey":
            continue
        if key in ACTORS and value == "@admin":
            value = admin
        if value is not None and isinstance(table.c[key].type, PGUUID):
            value = UUID(value)
        if value is not None and isinstance(table.c[key].type, DateTime):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(value.tzinfo is not None, "timezone_required")
        result[key] = value
    return result


async def import_bundle(
    bundle: dict[str, Any],
    *,
    url: str,
    expected_db_name: str,
    asset_root: Path,
    apply: bool = False,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    from sqlalchemy import insert, select, text, update
    from sqlalchemy.ext.asyncio import create_async_engine

    from jx_core.models import Base
    from jx_core.security.crypto import encrypt_secret

    validate_bundle(bundle)
    verify_assets(bundle, asset_root)
    database_guard(url, expected_db_name)
    environment = os.environ if environment is None else environment
    engine = create_async_engine(url, echo=False)
    try:
        async with asyncio.timeout(60):
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL statement_timeout='5s'"))
                await connection.execute(text("SET LOCAL lock_timeout='500ms'"))
                admin = await fresh_target(connection)
                if not apply:
                    return {
                        "status": "dry_run_fresh_target_verified",
                        "configuration_writes": 0,
                        "provider_calls": 0,
                        "bundle_sha256": bundle["contentSha256"],
                    }
                prepared = {}
                for name, rows in bundle["tables"].items():
                    table = Base.metadata.tables[name]
                    prepared[name] = [typed_row(table, row, admin) for row in rows]
                    if name == "model_profiles":
                        for row, incoming in zip(prepared[name], rows, strict=True):
                            key = environment.get(incoming["modelEnvKey"], "")
                            master = environment.get("LLM_KEY_ENCRYPTION_KEY", "")
                            require(bool(key) and bool(master), "model_secrets_not_injected")
                            cipher, nonce, last4 = encrypt_secret(key, master)
                            row.update(
                                api_key_ciphertext=cipher, api_key_nonce=nonce, api_key_last4=last4
                            )
                for name in FIELDS:
                    table = Base.metadata.tables[name]
                    for incoming in prepared[name]:
                        row = dict(incoming)
                        if name == "rules":
                            row["current_audio_generation_id"] = None
                        await connection.execute(insert(table).values(**row))
                table = Base.metadata.tables["rules"]
                for row in prepared["rules"]:
                    await connection.execute(
                        update(table)
                        .where(table.c.id == row["id"])
                        .values(current_audio_generation_id=row["current_audio_generation_id"])
                    )
                for name, expected in prepared.items():
                    table = Base.metadata.tables[name]
                    actual = [dict(r) for r in (await connection.execute(select(table))).mappings()]
                    require(
                        len(actual) == len(expected)
                        and all(
                            any(all(row[k] == v for k, v in item.items()) for row in actual)
                            for item in expected
                        ),
                        "import_postcheck",
                    )
                await connection.execute(
                    insert(Base.metadata.tables["audit_logs"]).values(
                        actor_user_id=UUID(admin),
                        action="v2.resources.imported",
                        target_type="configuration",
                        result="SUCCESS",
                        details={"bundle_sha256": bundle["contentSha256"], "prompts": 198},
                    )
                )
        return {
            "status": "fresh_resources_committed",
            "configuration_writes": True,
            "provider_calls": 0,
            "rules": 2,
            "prompts": 198,
            "bundle_sha256": bundle["contentSha256"],
            "host_audio_storage_root": str(asset_root.resolve()),
        }
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "validate", "import"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-db-name")
    parser.add_argument(
        "--database-env",
        choices=("V2_RESOURCE_DATABASE_URL", "TEST_DATABASE_URL"),
        default="V2_RESOURCE_DATABASE_URL",
    )
    args = parser.parse_args()
    try:
        require(args.input.stat().st_size <= 16 * 1024 * 1024, "input_size")
        value = json.loads(args.input.read_bytes())
        if args.mode == "export":
            require(
                args.output is not None and not args.apply and not args.output.exists(),
                "new_output_required",
            )
            bundle = export_bundle(value)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                )
            result = {
                "status": "public_bundle_exported",
                "bundle_sha256": bundle["contentSha256"],
                "provider_calls": 0,
            }
        else:
            validate_bundle(value)
            require(args.asset_root is not None, "asset_root_required")
            if args.mode == "validate":
                require(not args.apply, "apply_only_on_import")
                verify_assets(value, args.asset_root)
                result = {"status": "bundle_and_files_verified", "provider_calls": 0}
            else:
                require(bool(args.expected_db_name), "expected_database_required")
                result = asyncio.run(
                    import_bundle(
                        value,
                        url=os.environ.get(args.database_env, ""),
                        expected_db_name=args.expected_db_name,
                        asset_root=args.asset_root,
                        apply=args.apply,
                    )
                )
    except BaseException as error:
        result = {"status": "failed_safe_do_not_retry", "exception_class": type(error).__name__}
        if isinstance(error, Refusal):
            result["error_code"] = str(error)
        print(json.dumps(result))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
