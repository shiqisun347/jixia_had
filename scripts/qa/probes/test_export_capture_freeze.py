"""Explicit real-PostgreSQL risk probe for Spec 214's export cutoff.

Run only against the dedicated local jx_debate_qa_agent_faults database with
RUN_DATABASE_INTEGRATION=1 and an independent TEST_DATABASE_URL. The actual
create_export route, capture persistence, immutable blobs and Jobs package
reader are used. Payloads are synthetic; no provider or audio is accessed.

The existing worker reader is named _match_package, not _files_for_match.
Only agent-calls.jsonl is hashed: manifest.generated_at and ZIP timestamps
are intentionally outside these frozen research-content assertions. The
late-capture assertion does not prescribe whether a bodyless call index must
be present. These strong risk assertions are outside the default green gate.
"""

from __future__ import annotations

import json

import pytest
from apps.core.tests.export_snapshot_fixture import (
    ExportCaptureProbe,
)
from apps.core.tests.export_snapshot_fixture import (
    export_capture_probe as export_capture_probe,
)
from apps.core.tests.export_snapshot_fixture import (
    probe_database_url as probe_database_url,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_complete_pre_cutoff_capture_is_included_and_stable_control(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    call_id = await probe.seed_call(captured=True)
    frozen = await probe.export()
    state = await probe.call_state(call_id)
    assert state["started_at"] <= frozen.cutoff_at
    assert state["captured_at"] <= frozen.cutoff_at
    first, retry = await probe.read(frozen), await probe.read(frozen)
    assert len(first.rows) == 1
    assert first.rows[0]["id"] == str(call_id)
    assert all(first.rows[0][side] is not None for side in ("request", "response"))
    assert first.rows[0]["content_errors"] == []
    assert first.sha256 == retry.sha256


@pytest.mark.asyncio
async def test_new_post_cutoff_call_is_excluded_control(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    pre_call_id = await probe.seed_call(captured=True)
    frozen = await probe.export()
    before = await probe.read(frozen)
    new_call_id = await probe.seed_call(captured=True)
    state = await probe.call_state(new_call_id)
    assert state["started_at"] > frozen.cutoff_at
    after = await probe.read(frozen)
    assert {row["id"] for row in after.rows} == {str(pre_call_id)}
    assert before.sha256 == after.sha256


@pytest.mark.asyncio
async def test_pre_cutoff_call_with_late_capture_cannot_add_frozen_body(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    call_id = await probe.seed_call(captured=False)
    frozen = await probe.export()
    before = await probe.read(frozen)
    assert before.rows == []
    await probe.write_capture(call_id, revision=1)
    state = await probe.call_state(call_id)
    assert state["started_at"] <= frozen.cutoff_at < state["captured_at"]
    after = await probe.read(frozen)
    has_late_body = any(
        row["id"] == str(call_id) and any(row[side] is not None for side in ("request", "response"))
        for row in after.rows
    )
    print(
        json.dumps(
            {"scenario": "late_capture", "rows": len(after.rows), "late_body": has_late_body}
        )
    )
    assert not has_late_body, "capture committed after cutoff entered the frozen research body"


@pytest.mark.asyncio
async def test_same_export_read_retry_preserves_capture_body_hash_after_revision(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    call_id = await probe.seed_call(captured=True)
    frozen = await probe.export()
    before = await probe.read(frozen)
    first_state = await probe.call_state(call_id)
    assert len(before.rows) == 1 and first_state["captured_at"] <= frozen.cutoff_at
    # Repoint the legacy call to newly stored immutable blobs; the newly
    # approved freeze service must retain the original immutable pointers.
    await probe.write_capture(call_id, revision=2)
    revised_state = await probe.call_state(call_id)
    assert revised_state["captured_at"] > frozen.cutoff_at
    assert all(
        first_state[f"{side}_blob_id"] != revised_state[f"{side}_blob_id"]
        for side in ("request", "response")
    )
    retry = await probe.read(frozen)
    assert len(retry.rows) == 1
    changed = before.sha256 != retry.sha256
    print(
        json.dumps(
            {
                "scenario": "capture_revision",
                "research_hash_changed": changed,
                "request_body_changed": before.rows[0]["request"] != retry.rows[0]["request"],
                "response_body_changed": before.rows[0]["response"] != retry.rows[0]["response"],
            }
        )
    )
    assert not changed, "the same frozen export's research-content hash changed on read retry"
