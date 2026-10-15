#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only release preflight. The caller provides Core's runtime context;
# this script never prints environment values.

readonly EXPECTED_VERSION="2.1.0"
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly CORE_URL="${JX_PREFLIGHT_CORE_URL:-http://127.0.0.1:8100}"
readonly PYTHON_BIN="${JX_PREFLIGHT_PYTHON:-python3}"
readonly SERVICES=(jx-core jx-jobs jx-web)
readonly LIVEKIT_CONTAINER="${JX_PREFLIGHT_LIVEKIT_CONTAINER:-jx-livekit}"
readonly LIVEKIT_IMAGE_PREFIX="${JX_PREFLIGHT_LIVEKIT_IMAGE_PREFIX:-livekit/livekit-server:}"

failures=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "required command unavailable: $1"
    return 1
  fi
}

check_services() {
  local service
  for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then
      pass "service active: $service"
    else
      fail "service not active: $service"
    fi
  done

  # LiveKit is deployed as either a systemd unit or the explicitly named,
  # pinned container used by the production host.  Keep this check read-only
  # and fail closed for an absent/stopped/unexpected container.
  if systemctl is-active --quiet jx-livekit; then
    pass "service active: jx-livekit"
    return
  fi
  if systemctl is-active --quiet livekit.service; then
    pass "service active: livekit.service"
    return
  fi
  if ! command -v docker >/dev/null 2>&1; then
    fail "LiveKit systemd units inactive and docker unavailable"
    return
  fi
  if [[ -z "$LIVEKIT_IMAGE_PREFIX" ]]; then
    fail "LiveKit image prefix is empty"
    return
  fi

  local inspect_output running image
  if ! inspect_output="$(docker inspect --format '{{.State.Running}}|{{.Config.Image}}' "$LIVEKIT_CONTAINER" 2>/dev/null)"; then
    fail "LiveKit systemd units inactive and container not found: $LIVEKIT_CONTAINER"
    return
  fi
  running="${inspect_output%%|*}"
  image="${inspect_output#*|}"
  if [[ "$running" != "true" ]]; then
    fail "LiveKit container is not running: $LIVEKIT_CONTAINER"
    return
  fi
  if [[ "$image" != "$LIVEKIT_IMAGE_PREFIX"* ]]; then
    fail "LiveKit container image is unexpected: $LIVEKIT_CONTAINER"
    return
  fi
  pass "container active: $LIVEKIT_CONTAINER"
}

check_runtime_guards() {
  local minimum_nofile output
  local -a guard_args
  minimum_nofile="${JX_PREFLIGHT_MIN_NOFILE:-524288}"
  guard_args=(--min-nofile "$minimum_nofile")
  if [[ "${JX_PREFLIGHT_REQUIRE_MEMORY_GUARDS:-0}" == "1" ]]; then
    guard_args+=(--require-memory-guards)
  fi
  if [[ "${JX_PREFLIGHT_REQUIRE_CGROUP_TELEMETRY:-0}" == "1" ]]; then
    guard_args+=(--require-cgroup-telemetry)
  fi
  if ! output="$($REPO_ROOT/scripts/ops/runtime_guard_audit.sh "${guard_args[@]}")"; then
    printf '%s\n' "$output" >&2
    fail "effective Core/Jobs runtime resource guards are below the release floor"
    return
  fi
  printf '%s\n' "$output"
  pass "effective Core/Jobs runtime resource guards satisfy the release floor"
}

check_python_packages() {
  local output
  if ! output="$(
    cd "$REPO_ROOT"
    PYTHONPATH="$REPO_ROOT/apps/core/src:$REPO_ROOT/apps/jobs/src${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" - <<'PY'
import json
import jx_core
import jx_jobs

print(json.dumps({
    "core_file": jx_core.__file__,
    "core_version": jx_core.__version__,
    "jobs_file": jx_jobs.__file__,
    "jobs_version": jx_jobs.__version__,
}, ensure_ascii=True))
PY
  )"; then
    fail "Core/Jobs package inspection failed"
    return
  fi

  if PACKAGE_OUTPUT="$output" PREFLIGHT_VERSION="$EXPECTED_VERSION" \
    PREFLIGHT_ROOT="$REPO_ROOT" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(os.environ["PACKAGE_OUTPUT"])
expected = os.environ["PREFLIGHT_VERSION"]
root = Path(os.environ["PREFLIGHT_ROOT"]).resolve()
assert payload["core_version"] == expected
assert payload["jobs_version"] == expected
for key in ("core_file", "jobs_file"):
    assert Path(payload[key]).resolve().is_relative_to(root)
print(
    f"versions={payload['core_version']}/{payload['jobs_version']} "
    f"core_file={payload['core_file']} jobs_file={payload['jobs_file']}"
)
PY
  then
    pass "Core/Jobs version and import path match the v2.1 checkout"
  else
    fail "Core/Jobs version or import path does not match the v2.1 checkout"
  fi
}

check_http() {
  local endpoint body status
  for endpoint in /health/live /health/ready; do
    body="$(mktemp)"
    status="$(curl --silent --show-error --output "$body" --write-out '%{http_code}' \
      --connect-timeout 3 --max-time 10 "${CORE_URL}${endpoint}" || true)"
    if [[ "$status" == "200" ]]; then
      pass "Core ${endpoint} returned 200"
    else
      fail "Core ${endpoint} returned ${status:-no response}"
    fi
    rm -f "$body"
  done

  body="$(mktemp)"
  status="$(curl --silent --show-error --output "$body" --write-out '%{http_code}' \
    --connect-timeout 3 --max-time 10 \
    "${CORE_URL}/api/experiments/capabilities" || true)"
  if [[ "$status" == "200" ]] && CAPABILITY_FILE="$body" \
    PREFLIGHT_VERSION="$EXPECTED_VERSION" \
    python3 - <<'PY'
import json
import os

with open(os.environ["CAPABILITY_FILE"], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["target_version"] == os.environ["PREFLIGHT_VERSION"]
assert isinstance(payload["creation_enabled"], bool)
assert payload["history_readable"] is True
print(
    "target_version=" + payload["target_version"]
    + " creation_enabled=" + str(payload["creation_enabled"]).lower()
    + " history_readable=true"
)
PY
  then
    pass "experiment capability is valid"
  else
    fail "experiment capability is unavailable or invalid"
  fi
  rm -f "$body"
}

check_database() {
  local output
  if ! output="$(
    cd "$REPO_ROOT"
    PYTHONPATH="$REPO_ROOT/apps/core/src:$REPO_ROOT/apps/jobs/src${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" - <<'PY'
import asyncio
import json
from pathlib import Path
from sqlalchemy import text
from jx_core.config import load_settings
from jx_core.database import Database
from scripts.ops.migration_preflight import current_migration_head


async def main() -> None:
    expected_revision = current_migration_head(Path.cwd())
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        async with database.engine.connect() as connection:
            revisions = list(
                (await connection.scalars(text("SELECT version_num FROM alembic_version"))).all()
            )
            active_matches = await connection.scalar(
                text(
                    "SELECT count(*) FROM matches "
                    "WHERE status NOT IN ('FINISHED', 'TERMINATED')"
                )
            )
            nonterminal_room_rows = (
                await connection.execute(
                    text(
                        "SELECT status, count(*) FROM rooms "
                        "WHERE status NOT IN ('FINISHED', 'TERMINATED') "
                        "GROUP BY status ORDER BY status"
                    )
                )
            ).all()
            active_status_rows = (
                await connection.execute(
                    text(
                        "SELECT status, count(*) FROM matches "
                        "WHERE status NOT IN ('FINISHED', 'TERMINATED') "
                        "GROUP BY status ORDER BY status"
                    )
                )
            ).all()
            terminal_rooms_missing_end_at = await connection.scalar(
                text(
                    "SELECT count(*) FROM rooms "
                    "WHERE status IN ('FINISHED', 'TERMINATED') AND ended_at IS NULL"
                )
            )
            active_leases = await connection.scalar(
                text("SELECT count(*) FROM room_connection_leases")
            )
            terminal_room_leases = await connection.scalar(
                text(
                    "SELECT count(*) FROM room_connection_leases l "
                    "JOIN rooms r ON r.id = l.room_id "
                    "WHERE r.status IN ('FINISHED', 'TERMINATED')"
                )
            )
        print(json.dumps({
            "expected_revision": expected_revision,
            "revisions": revisions,
            "active_matches": int(active_matches or 0),
            "nonterminal_rooms": sum(int(count or 0) for _, count in nonterminal_room_rows),
            "nonterminal_room_statuses": {
                str(status): int(count or 0) for status, count in nonterminal_room_rows
            },
            "active_statuses": {
                str(status): int(count or 0) for status, count in active_status_rows
            },
            "terminal_rooms_missing_end_at": int(terminal_rooms_missing_end_at or 0),
            "active_leases": int(active_leases or 0),
            "terminal_room_leases": int(terminal_room_leases or 0),
        }))
    finally:
        await database.dispose()


asyncio.run(main())
PY
  )"; then
    fail "database preflight query failed"
    return
  fi

  if DATABASE_OUTPUT="$output" PREFLIGHT_ROOT="$REPO_ROOT" \
    "$PYTHON_BIN" - <<'PY'
import json
import os
import sys

sys.path.insert(0, os.environ["PREFLIGHT_ROOT"])
from scripts.ops.migration_preflight import database_at_migration_head

payload = json.loads(os.environ["DATABASE_OUTPUT"])
expected = payload["expected_revision"]
revisions = payload["revisions"]
print(f"expected_revision={expected} revisions={','.join(revisions) or 'none'}")
raise SystemExit(0 if database_at_migration_head(revisions, expected) else 1)
PY
  then
    pass "database migration matches the checkout head"
  else
    fail "database migration does not match the checkout head"
  fi
  if DATABASE_OUTPUT="$output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DATABASE_OUTPUT"])
active_matches = payload["active_matches"]
active_statuses = payload["active_statuses"]
status_summary = ",".join(
    f"{status}={count}" for status, count in sorted(active_statuses.items())
) or "none"
print(f"active_matches={active_matches} active_statuses={status_summary}")
raise SystemExit(0 if type(active_matches) is int and active_matches == 0 else 1)
PY
  then
    pass "no non-terminal matches"
  else
    fail "non-terminal matches exist; do not deploy"
  fi
  if DATABASE_OUTPUT="$output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DATABASE_OUTPUT"])
nonterminal_rooms = payload["nonterminal_rooms"]
status_summary = ",".join(
    f"{status}={count}" for status, count in sorted(payload["nonterminal_room_statuses"].items())
) or "none"
print(f"nonterminal_rooms={nonterminal_rooms} nonterminal_room_statuses={status_summary}")
raise SystemExit(0 if type(nonterminal_rooms) is int and nonterminal_rooms == 0 else 1)
PY
  then
    pass "no non-terminal rooms"
  else
    fail "non-terminal rooms exist; do not deploy"
  fi
  if DATABASE_OUTPUT="$output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DATABASE_OUTPUT"])
missing_end_at = payload["terminal_rooms_missing_end_at"]
terminal_leases = payload["terminal_room_leases"]
active_leases = payload["active_leases"]
print(
    f"terminal_rooms_missing_end_at={missing_end_at} "
    f"terminal_room_leases={terminal_leases} active_leases={active_leases}"
)
raise SystemExit(0 if missing_end_at == 0 and terminal_leases == 0 else 1)
PY
  then
    pass "terminal room timestamps and leases are reconciled"
  else
    fail "terminal rooms are missing ended_at or retain active leases; do not deploy"
  fi
  if DATABASE_OUTPUT="$output" python3 - <<'PY'
import json
import os

active_leases = json.loads(os.environ["DATABASE_OUTPUT"])["active_leases"]
print(f"active_leases={active_leases}")
raise SystemExit(0 if active_leases == 0 else 1)
PY
  then
    pass "no active room connection leases remain"
  else
    fail "active room connection leases remain; drain clients before deploy"
  fi
}

main() {
  require_command systemctl || true
  require_command curl || true
  require_command "$PYTHON_BIN" || true
  require_command python3 || true
  if (( failures > 0 )); then
    printf 'Preflight stopped: missing required commands.\n' >&2
    exit 1
  fi

  printf 'Jixia v2.1 read-only release preflight\n'
  printf 'repo=%s core_url=%s\n' "$REPO_ROOT" "$CORE_URL"
  check_services
  check_runtime_guards
  check_python_packages
  check_http
  check_database

  if (( failures > 0 )); then
    printf 'Preflight failed with %d issue(s). No changes were made.\n' "$failures" >&2
    exit 1
  fi
  printf 'Preflight passed. No changes were made.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
