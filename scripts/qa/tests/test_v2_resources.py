"""Portable catalog controls with synthetic resources; no DB/provider/environment files."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from scripts.ops import v2_resources as resources

from jx_core.rules.prompts import FIXED_SPEECH_VARIABLES


def identifier(value):
    return str(uuid5(NAMESPACE_URL, "v2-resources-test:" + value))


def row(table, **values):
    result = dict.fromkeys(resources.FIELDS[table])
    result.update(values)
    return result


@pytest.fixture
def source():
    tables = {name: [] for name in resources.FIELDS}
    files = {}
    manifest = []
    model = identifier("model")
    tables["model_profiles"].append(
        row(
            "model_profiles",
            id=model,
            name="Synthetic model",
            config_ref="synthetic",
            base_url="https://model.example.test/v1",
            model_id="synthetic",
            max_concurrency=5,
            token_per_char=1.0,
            generation_params={},
            capability_schema={},
            status="ENABLED",
            modelEnvKey="JX_LLM_API_KEY",
        )
    )
    voices = [identifier("voice" + str(i)) for i in range(11)]
    for i, voice in enumerate(voices):
        avatar = "agent-" + str(i + 1).zfill(2) if i < 10 else None
        tables["voice_profiles"].append(
            row(
                "voice_profiles",
                id=voice,
                name="Voice " + str(i),
                kind="AGENT" if avatar else "HOST",
                provider_voice="synthetic_voice_" + str(i),
                rate=1.0,
                chars_per_second=4.5,
                playback_gain=1.0,
                avatar_key=avatar,
                calibration_status="READY",
                calibrated_at="2026-10-18T00:00:00+00:00",
                status="ENABLED",
            )
        )
        if avatar:
            path = "avatars/" + avatar + ".webp"
            body = ("synthetic " + avatar).encode()
            files[path] = body
            manifest.append(
                dict(
                    id=avatar,
                    kind="avatar",
                    path=path,
                    sha256=hashlib.sha256(body).hexdigest(),
                    bytes=len(body),
                    duration_ms=None,
                )
            )
    template = " ".join("{{" + key + "}}" for key in sorted(FIXED_SPEECH_VARIABLES))
    for number, (rule, revision) in enumerate(resources.RULE_REVISIONS.items()):
        generation, asset = identifier(rule + "generation"), identifier(rule + "asset")
        tables["rules"].append(
            row(
                "rules",
                id=rule,
                rule_key="synthetic-rule-" + str(number),
                version=1,
                name="Synthetic rule " + str(number),
                description="fixture",
                side_size=4,
                estimated_seconds=1080,
                status="ENABLED",
                lifecycle_status="ENABLED",
                host_voice_profile_id=voices[-1],
                default_agent_model_profile_id=model,
                topic_policy="BOTH",
                config_revision=revision,
                postmatch_questionnaire_enabled=False,
                historical_read_only=False,
                created_by=identifier("old-private-admin"),
                audio_reviewed_at="2026-10-18T00:00:00+00:00",
                current_audio_generation_id=generation,
            )
        )
        tables["rule_judge_configs"].append(
            row(
                "rule_judge_configs",
                rule_id=rule,
                enabled=False,
                model_profile_id=None,
                judge_prompt="",
                include_in_leaderboard=False,
            )
        )
        stages = []
        for position in range(1, 10):
            stage = identifier(rule + "stage" + str(position))
            stages.append(stage)
            tables["rule_stages"].append(
                row(
                    "rule_stages",
                    id=stage,
                    rule_id=rule,
                    position=position,
                    name="Synthetic stage",
                    stage_kind="FIXED_SPEECH",
                    duration_seconds=120,
                    start_host_text="Welcome" if position == 1 else "",
                    end_host_text="",
                    parameters={},
                )
            )
            tables["stage_actions"].append(
                row(
                    "stage_actions",
                    id=identifier(stage + "action"),
                    stage_id=stage,
                    position=1,
                    action_kind="SPEECH",
                    side="AFFIRMATIVE",
                    seat_no=1,
                    duration_seconds=120,
                    parameters={},
                )
            )
            tables["stage_prompt_templates"].append(
                row(
                    "stage_prompt_templates",
                    id=identifier(stage + "default"),
                    stage_id=stage,
                    purpose="SPEECH",
                    template_text=template,
                    variables=sorted(FIXED_SPEECH_VARIABLES),
                    output_contract="TEXT",
                )
            )
        for i in range(10):
            agent = identifier(rule + "agent" + str(i))
            tables["agent_profiles"].append(
                row(
                    "agent_profiles",
                    id=agent,
                    name="Agent " + str(i),
                    format_version_id=None,
                    rule_id=rule,
                    model_profile_id=model,
                    voice_profile_id=voices[i],
                    system_prompt="Professional synthetic debater",
                    debater_prompt="Maintain assigned stance",
                    generation_params={},
                    status="ENABLED",
                )
            )
            for stage in stages:
                tables["agent_prompt_overrides"].append(
                    row(
                        "agent_prompt_overrides",
                        id=identifier(stage + agent),
                        agent_profile_id=agent,
                        stage_id=stage,
                        purpose="SPEECH",
                        template_text=template,
                        variables=sorted(FIXED_SPEECH_VARIABLES),
                        output_contract="TEXT",
                    )
                )
        inputs = dict(
            text="Welcome",
            text_hash=hashlib.sha256(b"Welcome").hexdigest(),
            voice_profile_id=voices[-1],
            provider_voice="synthetic_voice_10",
            rate=1.0,
            model="synthetic-tts",
            workspace_id="",
            websocket_url="wss://voice.example.test/ws",
            format="ogg_opus",
            sample_rate=24000,
            bit_rate=32,
        )
        segment = dict(
            stage_id=stages[0],
            boundary="start",
            position=1,
            segment_key="stage-1-start",
            input_hash=resources.fingerprint(inputs),
            input=inputs,
        )
        host_manifest = dict(schema="host-audio-v1", segments=[segment])
        tables["rule_audio_generations"].append(
            row(
                "rule_audio_generations",
                id=generation,
                rule_id=rule,
                config_revision=1,
                manifest_hash=resources.fingerprint(host_manifest),
                manifest=host_manifest,
                status="SUCCEEDED",
                review_status="APPROVED",
                requested_by=identifier("old-private-admin"),
                requested_at="2026-10-18T00:00:00+00:00",
                started_at="2026-10-18T00:00:00+00:00",
                completed_at="2026-10-18T00:00:01+00:00",
                approved_by=identifier("old-private-admin"),
                approved_at="2026-10-18T00:00:02+00:00",
                total_count=1,
                ready_count=1,
                failed_count=0,
                last_error_code=None,
                is_current=True,
            )
        )
        path, body = "host-audio/" + asset + ".ogg", b"synthetic host file" + str(number).encode()
        files[path] = body
        checksum = hashlib.sha256(body).hexdigest()
        manifest.append(
            dict(
                id=asset,
                kind="host_audio",
                path=path,
                sha256=checksum,
                bytes=len(body),
                duration_ms=1000,
            )
        )
        tables["host_audio_assets"].append(
            row(
                "host_audio_assets",
                id=asset,
                rule_id=rule,
                generation_id=generation,
                segment_key="stage-1-start",
                text="Welcome",
                text_hash=inputs["text_hash"],
                voice_profile_id=voices[-1],
                storage_path="/private/old-host-path.ogg",
                status="READY",
                error_code=None,
                input_hash=resources.fingerprint(inputs),
                input_snapshot=inputs,
                byte_count=len(body),
                checksum=checksum,
                duration_ms=1000,
            )
        )
    return dict(
        sourceInfo=dict(
            spec=327,
            migrationHead=resources.HEAD,
            releaseReceiptSha256=resources.RELEASE_SHA,
            ruleRevisions=resources.RULE_REVISIONS,
        ),
        tables=tables,
        assets=manifest,
    ), files


def seal(bundle):
    bundle["contentSha256"] = resources.fingerprint(
        {k: v for k, v in bundle.items() if k != "contentSha256"}
    )
    return bundle


def test_export_is_deterministic_exact_prompts_and_remapped_private_owners(source):
    raw, _ = source
    bundle = resources.export_bundle(raw)
    assert bundle == resources.export_bundle(raw)
    assert (
        len(bundle["tables"]["stage_prompt_templates"])
        + len(bundle["tables"]["agent_prompt_overrides"])
        == 198
    )
    assert bundle["tables"]["rules"][0]["created_by"] == "@admin"
    assert bundle["tables"]["model_profiles"][0]["modelEnvKey"] == "JX_LLM_API_KEY"
    assert all(not r["storage_path"].startswith("/") for r in bundle["tables"]["host_audio_assets"])
    assert identifier("old-private-admin") not in str(bundle)
    assert raw["tables"]["rules"][0]["created_by"] == identifier("old-private-admin")


@pytest.mark.parametrize(
    "field", ["api_key_ciphertext", "api_key_nonce", "api_key", "password_hash"]
)
def test_private_fields_rejected_not_silently_exported(source, field):
    raw, _ = source
    raw["tables"]["model_profiles"][0][field] = "private fixture value"
    with pytest.raises(resources.Refusal, match="secret_field"):
        resources.export_bundle(raw)


@pytest.mark.parametrize(
    "field",
    ["token", "apiKey", "API-KEY", "api.key", "clientSecret", "private-key", "credentials"],
)
def test_nested_secret_aliases_are_refused_before_public_export(source, field):
    raw, _ = source
    raw["tables"]["model_profiles"][0]["capability_schema"][field] = "private fixture value"
    with pytest.raises(resources.Refusal, match="secret_field"):
        resources.export_bundle(raw)


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("model_profiles", "capability_schema", {"unreviewed": {"minimum": 0, "maximum": 1}}),
        (
            "model_profiles",
            "capability_schema",
            {"temperature": {"minimum": "private", "maximum": 2}},
        ),
        ("model_profiles", "generation_params", {"unreviewed": "private fixture value"}),
        ("agent_profiles", "generation_params", {"temperature": "private fixture value"}),
        ("judge_profiles", "generation_params", {"temperature": "private fixture value"}),
        ("rule_stages", "parameters", {"unreviewed": "private fixture value"}),
        ("stage_actions", "parameters", {"unreviewed": "private fixture value"}),
    ],
)
def test_public_json_configuration_is_closed_and_not_arbitrary_text(source, table, column, value):
    bundle = resources.export_bundle(source[0])
    if table == "judge_profiles":
        bundle["tables"][table].append(
            row(
                "judge_profiles",
                id=identifier("judge"),
                model_profile_id=bundle["tables"]["model_profiles"][0]["id"],
                system_prompt="Public judge",
                judge_prompt="Public evaluation",
                generation_params={},
                status="ENABLED",
            )
        )
    bundle["tables"][table][0][column] = value
    seal(bundle)
    with pytest.raises(resources.Refusal):
        resources.validate_bundle(bundle)


def test_lowercase_bearer_secret_value_is_refused():
    with pytest.raises(resources.Refusal, match="secret_value"):
        resources.no_secrets("bearer " + "publicfixture" * 4)


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "source",
        "count",
        "cross_rule",
        "contract",
        "variables",
        "host_hash",
        "manifest",
        "actor",
        "unknown_table",
    ],
)
def test_invalid_portable_bundle_refused(source, mutation):
    bundle = resources.export_bundle(source[0])
    if mutation == "hash":
        bundle["contentSha256"] = "0" * 64
    elif mutation == "source":
        bundle["sourceInfo"]["releaseReceiptSha256"] = "0" * 64
    elif mutation == "count":
        bundle["tables"]["agent_prompt_overrides"].pop()
    elif mutation == "cross_rule":
        prompt = bundle["tables"]["agent_prompt_overrides"][0]
        current = next(
            r for r in bundle["tables"]["agent_profiles"] if r["id"] == prompt["agent_profile_id"]
        )
        prompt["agent_profile_id"] = next(
            r["id"]
            for r in bundle["tables"]["agent_profiles"]
            if r["rule_id"] != current["rule_id"]
        )
    elif mutation == "contract":
        bundle["tables"]["agent_prompt_overrides"][0]["output_contract"] = "JSON"
    elif mutation == "variables":
        bundle["tables"]["stage_prompt_templates"][0]["variables"] = []
    elif mutation == "host_hash":
        bundle["tables"]["host_audio_assets"][0]["input_hash"] = "0" * 64
    elif mutation == "manifest":
        bundle["tables"]["rule_audio_generations"][0]["manifest_hash"] = "0" * 64
    elif mutation == "actor":
        bundle["tables"]["rules"][0]["created_by"] = identifier("private")
    elif mutation == "unknown_table":
        bundle["tables"]["users"] = []
    if mutation != "hash":
        seal(bundle)
    with pytest.raises((resources.Refusal, ValueError)):
        resources.validate_bundle(bundle)


def test_assets_hash_scope_and_symlink_controls(source, tmp_path):
    raw, files = source
    bundle = resources.export_bundle(raw)
    for path, body in files.items():
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
    resources.verify_assets(bundle, tmp_path)
    path = tmp_path / next(iter(files))
    path.write_bytes(b"changed")
    with pytest.raises(resources.Refusal, match="asset_file_hash"):
        resources.verify_assets(bundle, tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / next(iter(files)))
    with pytest.raises(resources.Refusal, match="asset_file"):
        resources.verify_assets(bundle, tmp_path)


@pytest.mark.parametrize(
    "path", ["/opt/private.ogg", "../private.ogg", "host-audio/../x", "host-audio\\x", "users/x"]
)
def test_asset_paths_cannot_escape(path):
    with pytest.raises(resources.Refusal):
        resources.relative_asset(path)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql+psycopg://127.0.0.1/jx_v2_restore_12345678", "jx_v2_restore_12345678"),
        (
            "postgresql+psycopg://localhost/jx_spec328_restore_12345678",
            "jx_spec328_restore_12345678",
        ),
    ],
)
def test_only_explicit_owned_local_target_name_allowed(url, expected):
    resources.database_guard(url, expected)
    with pytest.raises(resources.Refusal):
        resources.database_guard(url, expected + "other")
    with pytest.raises(resources.Refusal):
        resources.database_guard(
            url.replace("127.0.0.1", "db.example.test").replace("localhost", "db.example.test"),
            expected,
        )


def test_typed_owner_remapping_never_reuses_production_actor(source):
    from jx_core.models import Base

    bundle = resources.export_bundle(source[0])
    admin = identifier("fresh-admin")
    mapped = resources.typed_row(Base.metadata.tables["rules"], bundle["tables"]["rules"][0], admin)
    assert mapped["created_by"] == UUID(admin)
    assert mapped["audio_reviewed_at"].tzinfo is not None


class FakeConnection:
    def __init__(self, bad=None):
        self.bad, self.calls = bad, []

    async def scalar(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append(sql)
        if "pg_try_advisory" in sql:
            return self.bad != "lock"
        if "version_num" in sql:
            return "old-head" if self.bad == "head" else resources.HEAD
        if "FROM alembic_version" in sql:
            return 1
        return 1 if self.bad == "history" and "matches" in sql else 0

    async def execute(self, statement):
        sql = str(statement)
        self.calls.append(sql)
        if "FROM pg_catalog.pg_class" in sql:
            from jx_core.models import Base

            names = sorted(set(Base.metadata.tables) | {"alembic_version"})
            rows = [(name, "r") for name in names]
            if self.bad == "extra_table":
                rows.append(("private_history", "r"))
            elif self.bad == "missing_table":
                rows.pop()
            elif self.bad == "unexpected_relation_kind":
                rows[0] = (rows[0][0], "v")
            return SimpleNamespace(all=lambda: rows)
        if "FROM users" in sql:
            role = "USER" if self.bad == "admin" else "ADMIN"
            rows = [SimpleNamespace(id=UUID(identifier("fresh-admin")), role=role, status="ACTIVE")]
            return SimpleNamespace(all=lambda: rows)
        if "FROM audit_logs" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        None,
        "lock",
        "head",
        "history",
        "admin",
        "extra_table",
        "missing_table",
        "unexpected_relation_kind",
    ],
)
async def test_fresh_target_checks_are_read_and_lock_only(bad):
    connection = FakeConnection(bad)
    if bad:
        with pytest.raises(resources.Refusal):
            await resources.fresh_target(connection)
    else:
        assert await resources.fresh_target(connection) == identifier("fresh-admin")
        assert connection.calls[0] == "SET LOCAL search_path = public, pg_catalog, pg_temp"
        locks = [statement for statement in connection.calls if statement.startswith("LOCK TABLE")]
        assert locks and all(statement.startswith('LOCK TABLE public."') for statement in locks)
        assert any('public."alembic_version"' in statement for statement in locks)
    assert not any(
        word in sql.upper() for sql in connection.calls for word in ("INSERT", "DELETE", "TRUNCATE")
    )


@pytest.mark.asyncio
async def test_invalid_input_never_opens_database(source, tmp_path, monkeypatch):
    import sqlalchemy.ext.asyncio

    bundle = resources.export_bundle(source[0])
    bundle["contentSha256"] = "0" * 64

    def forbidden(*args, **kwargs):
        pytest.fail("Database opened before pure validation")

    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", forbidden)
    with pytest.raises(resources.Refusal):
        await resources.import_bundle(
            bundle, url="not-a-url", expected_db_name="ignored", asset_root=tmp_path
        )
