"""Read-only incident replay, paired against the approved pure Agent assembler.

This tool never writes production rows, changes match state or records OPS_PROBE.
Only bounded new output excerpts and aggregate measurements leave the server.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from time import monotonic
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, text

from jx_core.agent.llm import OpenAIStreamingClient, close_llm_client_bounded
from jx_core.config import load_settings
from jx_core.data_capture.content import load_content_blob
from jx_core.data_capture.provider import ProviderCallCapture as AdapterCapture
from jx_core.database import Database
from jx_core.models import (
    AgentGeneration,
    ExternalCall,
    FreeDebateOpportunity,
    Match,
    MatchParticipant,
    ModelProfile,
    ProviderCallCapture,
    Room,
    Speech,
)
from jx_core.security.crypto import decrypt_secret

MATCH_ID = UUID("540a67d7-9e9c-4fea-9394-0395708322d3")
REPEATED_SPEECH_IDS = (
    UUID("8204e995-73a8-4e97-8ffb-4f8166fd2dd2"),
    UUID("0318ca8b-686d-4871-9eba-4363e576a472"),
)
MAX_HISTORY_RECORDS = 128
MAX_HISTORY_CHARS = 24_000
MAX_BODY_CHARS = 256_000
EXCERPT_CHARS = 180


class CompareError(RuntimeError):
    pass


@dataclass(frozen=True)
class Access:
    base_url: str = field(repr=False)
    model_id: str
    api_key: str = field(repr=False)


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def speech_statement() -> Any:
    # Production is still schema 0046. Never SELECT the candidate Speech ORM
    # entity: its new origin column does not exist there before publication.
    return (
        select(
            Speech.id,
            Speech.action_key,
            Speech.user_id,
            Speech.agent_profile_id,
            Speech.speaker_kind,
            Speech.side,
            Speech.seat_no,
            Speech.status,
            Speech.display_text,
            Speech.created_at,
            Speech.finalized_at,
            Speech.generation_id,
        )
        .where(Speech.match_id == MATCH_ID)
        .order_by(Speech.created_at, Speech.id)
        .limit(301)
    )


def grouped_history(content: str) -> tuple[int, int, list[dict[str, Any]]] | None:
    if len(content) > MAX_BODY_CHARS:
        raise CompareError("qa_original_prompt_too_large")
    decoder = json.JSONDecoder()
    found: list[tuple[int, int, list[dict[str, Any]]]] = []
    for match in re.finditer(r"\[", content):
        try:
            value, size = decoder.raw_decode(content[match.start() :])
        except ValueError:
            continue
        if not isinstance(value, list) or not value:
            continue
        groups = cast(list[Any], value)
        if all(
            isinstance(item, dict)
            and isinstance(cast(dict[str, Any], item).get("stage"), str)
            and isinstance(cast(dict[str, Any], item).get("message"), list)
            for item in groups
        ):
            found.append((match.start(), match.start() + size, cast(list[dict[str, Any]], groups)))
    if len(found) > 1:
        raise CompareError("qa_original_prompt_has_multiple_histories")
    return found[0] if found else None


def stage_labels(action_key: str, rule: dict[str, Any]) -> tuple[str, str]:
    position = int(action_key.split(":", 1)[0])
    stages = sorted(
        cast(list[dict[str, Any]], rule["stages"]), key=lambda item: int(item["position"])
    )
    current = next((item for item in stages if int(item["position"]) == position), None)
    following = next((item for item in stages if int(item["position"]) > position), None)
    if current is None or not current.get("name"):
        raise CompareError("qa_original_stage_missing")
    return str(current["name"]), str(following["name"]) if following else "比赛结束"


def speaker_identity(label: str) -> tuple[str, int]:
    match = re.fullmatch(r"(正方|反方)([一二三四1234])辩", label)
    if match is None:
        raise CompareError("qa_original_speaker_not_canonical")
    seat = {"一": 1, "二": 2, "三": 3, "四": 4}.get(match[2])
    return ("AFFIRMATIVE" if match[1] == "正方" else "NEGATIVE", seat or int(match[2]))


def typed_entry(
    row: dict[str, Any],
    *,
    content: str,
    cutoff: datetime,
    participant: dict[str, Any],
    roster: list[dict[str, Any]],
    rule: dict[str, Any],
) -> dict[str, Any]:
    owner = next(
        (
            item
            for item in roster
            if item["side"] == row["side"] and item["seat_no"] == row["seat_no"]
        ),
        None,
    )
    if owner is None:
        raise CompareError("qa_history_participant_missing")
    final = row["finalized_at"] is not None and row["finalized_at"] <= cutoff
    side_name = "正方" if row["side"] == "AFFIRMATIVE" else "反方"
    seat_name = {1: "一辩", 2: "二辩", 3: "三辩", 4: "四辩"}[row["seat_no"]]
    return {
        "speech_id": str(row["id"]),
        "participant_id": str(owner["id"]),
        "stage": stage_labels(row["action_key"], rule)[0],
        "speaker": side_name + seat_name,
        "side": row["side"],
        "seat_no": row["seat_no"],
        "speaker_kind": row["speaker_kind"],
        "agent_profile_id": str(row["agent_profile_id"]) if row["agent_profile_id"] else None,
        "relation": "SELF"
        if row["agent_profile_id"] == participant["agent_profile_id"]
        else "TEAMMATE"
        if row["side"] == participant["side"]
        else "OPPONENT",
        "status": "FINALIZED" if final else "FINALIZING",
        "content_state": "FINAL" if final else "PROVISIONAL" if content else "PENDING",
        # The incident predates additive migration 0047 and has no placeholders.
        "origin": "ORIGINAL",
        "delivery": "PARTICIPANT_SPEECH",
        "content": content,
    }


def reconstruct_free_history(
    groups: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    cutoff: datetime,
    participant: dict[str, Any],
    roster: list[dict[str, Any]],
    rule: dict[str, Any],
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    used: set[UUID] = set()
    last_created: datetime | None = None
    for group in groups:
        for raw in cast(list[dict[str, Any]], group["message"]):
            side, seat = speaker_identity(str(raw["speaker"]))
            content = str(raw["content"])
            eligible = [
                row
                for row in rows
                if row["id"] not in used
                and row["side"] == side
                and row["seat_no"] == seat
                and row["created_at"] < cutoff
                and (last_created is None or row["created_at"] >= last_created)
                and stage_labels(row["action_key"], rule)[0] == group["stage"]
            ]
            exact = [row for row in eligible if str(row["display_text"] or "").strip() == content]
            if len(exact) != 1:
                # An unfinished ASR snapshot can later have a longer final.
                # Keep the exact captured prefix, never substitute future text.
                exact = [
                    row
                    for row in eligible
                    if len(content) >= 20
                    and str(row["display_text"] or "").strip().startswith(content)
                    and (row["finalized_at"] is None or row["finalized_at"] > cutoff)
                ]
            if len(exact) > 1:
                raise CompareError("qa_captured_history_identity_ambiguous")
            if not exact:
                raise CompareError("qa_captured_history_identity_ambiguous")
            row = exact[0]
            used.add(row["id"])
            last_created = row["created_at"]
            history.append(
                typed_entry(
                    row,
                    content=content,
                    cutoff=cutoff,
                    participant=participant,
                    roster=roster,
                    rule=rule,
                )
            )
    return history


def fixed_history(
    rows: list[dict[str, Any]],
    *,
    cutoff: datetime,
    participant: dict[str, Any],
    roster: list[dict[str, Any]],
    rule: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        typed_entry(
            row,
            content=str(row["display_text"] or ""),
            cutoff=cutoff,
            participant=participant,
            roster=roster,
            rule=rule,
        )
        for row in rows
        if row["status"] == "FINALIZED"
        and row["created_at"] < cutoff
        and row["finalized_at"] is not None
        and row["finalized_at"] <= cutoff
        and row["display_text"] is not None
    ]


def candidate_messages(
    body: dict[str, Any],
    *,
    generation: Any,
    participant: dict[str, Any],
    rows: list[dict[str, Any]],
    roster: list[dict[str, Any]],
    room: Any,
    assemble: Callable[..., list[dict[str, str]]],
    history_reference: str,
    source_id: UUID | None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    baseline = cast(list[dict[str, str]], body["messages"])
    if len(baseline) != 1 or baseline[0]["role"] != "user":
        raise CompareError("qa_original_request_shape_changed")
    current, following = stage_labels(generation.action_key, room.rule_snapshot)
    topic = room.topic_snapshot
    identity = {
        "participant_id": str(participant["id"]),
        "agent_profile_id": str(participant["agent_profile_id"]),
        "side": participant["side"],
        "seat_no": participant["seat_no"],
        "stance": str(
            topic["affirmative_text" if participant["side"] == "AFFIRMATIVE" else "negative_text"]
        ),
        "topic": str(topic["title"]),
        "stage": current,
        "next_stage": following,
        "action_key": generation.action_key,
    }
    template = baseline[0]["content"]
    grouped = grouped_history(template)
    cutoff = generation.created_at
    action_kind = "FREE_DEBATE" if generation.action_key == "7:0" else "FIXED_SPEECH"
    if action_kind == "FREE_DEBATE":
        if grouped is None:
            raise CompareError("qa_free_request_history_missing")
        start, end, groups = grouped
        history = reconstruct_free_history(
            groups,
            rows,
            cutoff=cutoff,
            participant=participant,
            roster=roster,
            rule=room.rule_snapshot,
        )
        template = template[:start] + history_reference + template[end:]
    else:
        if grouped is not None:
            raise CompareError("qa_fixed_request_shape_changed")
        history = fixed_history(
            rows, cutoff=cutoff, participant=participant, roster=roster, rule=room.rule_snapshot
        )
    source = None
    if source_id is not None:
        item = next((entry for entry in history if entry["speech_id"] == str(source_id)), None)
        if item is None and action_kind == "FREE_DEBATE":
            row = next(
                (
                    entry
                    for entry in rows
                    if entry["id"] == source_id and entry["created_at"] < cutoff
                ),
                None,
            )
            if row is None:
                raise CompareError("qa_source_speech_not_available_at_cutoff")
            is_final = row["finalized_at"] is not None and row["finalized_at"] <= cutoff
            content = str(row["display_text"] or "") if is_final else ""
            item = typed_entry(
                row,
                content=content,
                cutoff=cutoff,
                participant=participant,
                roster=roster,
                rule=room.rule_snapshot,
            )
            history.append(item)
            order = {str(row["id"]): index for index, row in enumerate(rows)}
            history.sort(key=lambda entry: order[entry["speech_id"]])
        if item is not None:
            source = {
                "speech_id": item["speech_id"],
                "status": item["status"],
                "content_state": item["content_state"],
                "included_in_history": True,
            }
    if (
        len(history) > MAX_HISTORY_RECORDS
        or sum(len(json.dumps(item, ensure_ascii=False)) + 2 for item in history)
        > MAX_HISTORY_CHARS
    ):
        raise CompareError("qa_comparison_scene_exceeds_candidate_history_budget")
    messages = assemble(
        template=template,
        identity=identity,
        history=history,
        context_version=generation.context_version,
        history_truncated=False,
        purpose="SPEECH",
        action_kind=action_kind,
        source=source,
    )
    return messages, history, identity


def repetition_metrics(output: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    value = output.strip()
    own = [item for item in history if item["relation"] in {"SELF", "TEAMMATE"} and item["content"]]
    exact = [item for item in own if value == str(item["content"]).strip()]
    maximum, source = 0, None
    for item in own:
        size = (
            SequenceMatcher(None, value, str(item["content"]), autojunk=False)
            .find_longest_match()
            .size
        )
        if size > maximum:
            maximum, source = size, item["speech_id"]
    return {
        "self_exact_repeat": any(item["relation"] == "SELF" for item in exact),
        "teammate_exact_repeat": any(item["relation"] == "TEAMMATE" for item in exact),
        "exact_repeat_speech_ids": [item["speech_id"] for item in exact],
        "max_contiguous_overlap_chars": maximum,
        "max_overlap_fraction_of_output": round(maximum / max(1, len(value)), 4),
        "max_overlap_reference_speech_id": source,
    }


def review_excerpt(output: str) -> str:
    if len(output) <= EXCERPT_CHARS:
        return output
    return output[:90] + "…" + output[-89:]


def private_json(root: Path, name: str, payload: Any, *, credential: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    if credential and credential.encode() in encoded:
        raise CompareError("qa_private_artifact_sensitive_value_rejected")
    if Path(name).name != name:
        raise CompareError("qa_private_artifact_name_invalid")
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)


async def load_cases(
    database: Any, settings: Any, assemble: Any, reference: str
) -> list[dict[str, Any]]:
    async with database.session_factory() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        match = await session.get(Match, MATCH_ID)
        room = await session.get(Room, match.room_id) if match else None
        if match is None or match.status not in {"FINISHED", "TERMINATED"} or room is None:
            raise CompareError("qa_incident_not_terminal")
        rows = [dict(item) for item in (await session.execute(speech_statement())).mappings()]
        participants = list(
            (
                await session.scalars(
                    select(MatchParticipant).where(MatchParticipant.match_id == MATCH_ID).limit(9)
                )
            ).all()
        )
        calls = list(
            (
                await session.scalars(
                    select(ExternalCall)
                    .where(
                        ExternalCall.match_id == MATCH_ID,
                        ExternalCall.call_kind == "LLM_SPEECH",
                        ExternalCall.status == "SUCCEEDED",
                    )
                    .order_by(ExternalCall.started_at)
                    .limit(101)
                )
            ).all()
        )
        if len(rows) > 300 or len(participants) != 8 or len(calls) > 100:
            raise CompareError("qa_incident_scope_limit_exceeded")
        roster = [
            {
                key: getattr(item, key)
                for key in ("id", "side", "seat_no", "kind", "agent_profile_id")
            }
            for item in participants
        ]
        by_generation = {
            item.agent_generation_id: item for item in calls if item.agent_generation_id
        }
        seeds = [
            next((row for row in rows if row["id"] == identifier), None)
            for identifier in REPEATED_SPEECH_IDS
        ]
        if any(row is None for row in seeds):
            raise CompareError("qa_incident_repeat_speeches_missing")
        selected = cast(list[dict[str, Any]], seeds)
        if any(row["side"] != "NEGATIVE" or row["action_key"] != "7:0" for row in selected):
            raise CompareError("qa_incident_repeat_identity_changed")
        if (
            len(str(selected[0]["display_text"] or "")) != 98
            or selected[0]["display_text"] != selected[1]["display_text"]
        ):
            raise CompareError("qa_incident_exact_repeat_changed")
        if any(row["generation_id"] not in by_generation for row in selected):
            raise CompareError("qa_incident_repeat_generation_missing")
        late = by_generation[selected[1]["generation_id"]].started_at
        affirmative = [
            row
            for row in rows
            if row["side"] == "AFFIRMATIVE"
            and row["speaker_kind"] == "AGENT"
            and row["action_key"] == "7:0"
            and row["generation_id"] in by_generation
            and by_generation[row["generation_id"]].started_at < late
        ]
        closing = [
            row
            for row in rows
            if row["speaker_kind"] == "AGENT"
            and row["seat_no"] == 4
            and row["action_key"] in {"8:1", "9:1"}
            and row["generation_id"] in by_generation
        ]
        if not affirmative or not closing:
            raise CompareError("qa_incident_four_case_coverage_missing")
        selected += [affirmative[-1], closing[0]]
        cases: list[dict[str, Any]] = []
        for label, row in zip(
            ("negative_repeat_early", "negative_repeat_late", "affirmative_free", "fourth_closing"),
            selected,
            strict=True,
        ):
            call = by_generation[row["generation_id"]]
            generation = await session.get(AgentGeneration, call.agent_generation_id)
            participant = (
                next(
                    (
                        item
                        for item in roster
                        if item["agent_profile_id"] == generation.agent_profile_id
                    ),
                    None,
                )
                if generation
                else None
            )
            if (
                generation is None
                or participant is None
                or participant["side"] != row["side"]
                or participant["seat_no"] != row["seat_no"]
            ):
                raise CompareError("qa_incident_generation_identity_mismatch")
            captured = await session.scalar(
                select(ProviderCallCapture)
                .where(ProviderCallCapture.external_call_id == call.id)
                .order_by(ProviderCallCapture.revision.desc())
                .limit(1)
            )
            blob_id = captured.request_blob_id if captured else call.request_blob_id
            status = captured.request_capture_status if captured else call.request_capture_status
            if (
                status != "COMPLETE"
                or blob_id is None
                or (captured is None and call.capture_storage_mode == "ASYNC_V1")
            ):
                raise CompareError("qa_original_raw_request_unavailable")
            raw = await load_content_blob(session, blob_id)
            body_raw: object = raw.get("body")
            if not isinstance(body_raw, dict):
                raise CompareError("qa_original_raw_request_invalid")
            body = cast(dict[str, Any], body_raw)
            if not isinstance(body.get("messages"), list):
                raise CompareError("qa_original_raw_request_invalid")
            snapshot = cast(dict[str, Any], room.format_snapshot or {})
            frozen_agents = cast(list[dict[str, Any]], snapshot.get("agents", []))
            frozen = next(
                (
                    item
                    for item in frozen_agents
                    if item.get("id") == str(generation.agent_profile_id)
                ),
                None,
            )
            model = (
                await session.get(ModelProfile, UUID(str(frozen["model"]["id"])))
                if frozen
                else None
            )
            if (
                model is None
                or model.status != "ENABLED"
                or model.model_id != body.get("model")
                or model.api_key_ciphertext is None
                or model.api_key_nonce is None
                or not model.base_url
                or settings.llm_key_encryption_key is None
            ):
                raise CompareError("qa_current_model_identity_unavailable_or_changed")
            access = Access(
                model.base_url,
                model.model_id,
                decrypt_secret(
                    model.api_key_ciphertext,
                    model.api_key_nonce,
                    settings.llm_key_encryption_key.get_secret_value(),
                ),
            )
            opportunity = (
                await session.get(FreeDebateOpportunity, call.opportunity_id)
                if call.opportunity_id
                else None
            )
            source_id = opportunity.source_speech_id if opportunity else None
            candidate, history, identity = candidate_messages(
                body,
                generation=generation,
                participant=participant,
                rows=rows,
                roster=roster,
                room=room,
                assemble=assemble,
                history_reference=reference,
                source_id=source_id,
            )
            params = {
                key: value
                for key, value in body.items()
                if key not in {"model", "messages", "stream", "stream_options", "max_tokens"}
            }
            if (
                body.get("stream") is not True
                or body.get("stream_options") != {"include_usage": True}
                or not isinstance(body.get("max_tokens"), int)
            ):
                raise CompareError("qa_original_transport_contract_changed")
            cases.append(
                {
                    "case": label,
                    "call_id": str(call.id),
                    "speech_id": str(row["id"]),
                    "access": access,
                    "body": body,
                    "candidate": candidate,
                    "history": history,
                    "identity": identity,
                    "params": params,
                    "cutoff": generation.created_at.isoformat(),
                }
            )
        await session.rollback()
        if len({(item["access"].model_id, item["access"].base_url) for item in cases}) != 1:
            raise CompareError("qa_comparison_requires_one_current_model")
        return cases


async def run_pairs(cases: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        access = cast(Access, case["access"])
        for repeat in range(3):
            order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
            for variant in order:
                capture = AdapterCapture()
                client = OpenAIStreamingClient(
                    base_url=access.base_url, api_key=access.api_key, model=access.model_id
                )
                started = monotonic()
                filename = f"{case['case']}-{repeat + 1}-{variant}"
                record: dict[str, Any] = {
                    "case": case["case"],
                    "repeat": repeat + 1,
                    "variant": variant,
                    "model": access.model_id,
                    "params_sha256": digest(case["params"]),
                    "max_tokens": case["body"]["max_tokens"],
                }
                try:
                    async with asyncio.timeout(60):
                        response = await client.stream_chat(
                            messages=case["body"]["messages"]
                            if variant == "baseline"
                            else case["candidate"],
                            max_tokens=case["body"]["max_tokens"],
                            generation_params=case["params"],
                            on_delta=_ignore_delta,
                            capture=capture,
                        )
                    if not response.text.strip():
                        raise CompareError("qa_provider_output_empty")
                    if capture.request is None or capture.request.status != "COMPLETE":
                        raise CompareError("qa_actual_request_capture_incomplete")
                    actual = capture.request.payload["body"]
                    if variant == "baseline" and actual != case["body"]:
                        raise CompareError("qa_baseline_request_not_original")
                    if variant == "candidate" and {
                        key: value for key, value in actual.items() if key != "messages"
                    } != {key: value for key, value in case["body"].items() if key != "messages"}:
                        raise CompareError("qa_candidate_parameters_changed")
                    private_json(
                        root, filename + "-request.json", actual, credential=access.api_key
                    )
                    private_json(
                        root,
                        filename + "-response.json",
                        {"text": response.text},
                        credential=access.api_key,
                    )
                    record.update(
                        status="succeeded",
                        output_sha256=hashlib.sha256(response.text.encode()).hexdigest(),
                        output_chars=len(response.text),
                        output_excerpt=review_excerpt(response.text),
                        output_excerpt_truncated=len(response.text) > EXCERPT_CHARS,
                        request_sha256=digest(actual),
                        first_token_latency_ms=response.first_token_latency_ms,
                        completed_latency_ms=response.completed_latency_ms,
                        wall_ms=round((monotonic() - started) * 1000),
                        **repetition_metrics(response.text, case["history"]),
                    )
                except Exception as error:
                    record.update(
                        status="failed",
                        error_code=str(error)
                        if isinstance(error, CompareError)
                        else "qa_provider_request_failed",
                        exception_type=type(error).__name__,
                    )
                finally:
                    await close_llm_client_bounded(client)
                    capture.close()
                rows.append(record)
                private_json(root, filename + "-metrics.json", record, credential=access.api_key)
    cases_metadata = [
        {
            "case": item["case"],
            "original_call_id": item["call_id"],
            "original_speech_id": item["speech_id"],
            "identity": item["identity"],
            "history_entries": len(item["history"]),
            "history_cutoff_utc": item["cutoff"],
            "source": (
                "exact raw captured free history / "
                "finalized rows before generation creation for fixed"
            ),
            "baseline_request_sha256": digest(item["body"]),
            "candidate_request_messages_sha256": digest(item["candidate"]),
        }
        for item in cases
    ]
    summaries: list[dict[str, Any]] = []
    for case in cases:
        for variant in ("baseline", "candidate"):
            trials = [
                item
                for item in rows
                if item["case"] == case["case"]
                and item["variant"] == variant
                and item["status"] == "succeeded"
            ]
            summaries.append(
                {
                    "case": case["case"],
                    "variant": variant,
                    "successful_trials": len(trials),
                    "exact_self_or_teammate_repeats": sum(
                        item["self_exact_repeat"] or item["teammate_exact_repeat"]
                        for item in trials
                    ),
                    "distinct_output_sha256": len({item["output_sha256"] for item in trials}),
                    "maximum_overlap_fraction": max(
                        (item["max_overlap_fraction_of_output"] for item in trials), default=None
                    ),
                    "median_first_token_ms": median(
                        item["first_token_latency_ms"] for item in trials
                    )
                    if trials
                    else None,
                    "median_completed_ms": median(item["completed_latency_ms"] for item in trials)
                    if trials
                    else None,
                }
            )
    return {
        "probe": "spec304_actual_incident_prompt_pairs",
        "status": "passed"
        if len(rows) == 24 and all(item["status"] == "succeeded" for item in rows)
        else "failed",
        "production_read_only": True,
        "production_rows_written": 0,
        "production_match_state_changed": False,
        "requests_planned": 24,
        "requests_completed": len(rows),
        "max_inflight_requests": 1,
        "repeats_per_variant_per_case": 3,
        "semantic_acceptance_automatic": False,
        "status_scope": "API executions and paired request invariants only",
        "manual_side_review_required": True,
        "private_artifact_directory": str(root),
        "cases": cases_metadata,
        "case_variant_summaries": summaries,
        "trials": rows,
    }


async def _ignore_delta(_: str) -> None:
    return None


def candidate_assembler(candidate: Path) -> tuple[Any, str, str]:
    source = candidate.resolve() / "apps/core/src/jx_core/agent/prompt_context.py"
    if not source.is_file():
        raise CompareError("qa_candidate_assembler_missing")
    spec = importlib.util.spec_from_file_location("_spec304_candidate_prompt_context", source)
    if spec is None or spec.loader is None:
        raise CompareError("qa_candidate_assembler_unloadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        module.assemble_agent_messages,
        module.HISTORY_REFERENCE,
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )


async def run(candidate: Path, root: Path) -> dict[str, Any]:
    assemble, reference, assembler_sha = candidate_assembler(candidate)
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        cases = await load_cases(database, settings, assemble, reference)
    finally:
        await database.dispose()
    result = await run_pairs(cases, root)
    result["candidate_assembler_sha256"] = assembler_sha
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    root = Path(tempfile.mkdtemp(prefix="jx-spec304-prompt-compare-"))
    root.chmod(0o700)
    try:
        result = asyncio.run(run(args.candidate_root, root))
    except Exception as error:
        result = {
            "status": "failed",
            "error_code": str(error)
            if isinstance(error, CompareError)
            else "qa_prompt_compare_failed",
            "exception_type": type(error).__name__,
            "private_artifact_directory": str(root),
            "production_rows_written": 0,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
