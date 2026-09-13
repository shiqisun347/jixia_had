#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only release preflight. The caller provides Core's runtime context;
# this script never prints environment values.

readonly EXPECTED_VERSION="2.1.0"
readonly EXPECTED_REVISION="0043_browser_device_checks"
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly CORE_URL="${JX_PREFLIGHT_CORE_URL:-http://127.0.0.1:8100}"
readonly PYTHON_BIN="${JX_PREFLIGHT_PYTHON:-python3}"
readonly SERVICES=(jx-core jx-jobs jx-web jx-livekit)

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
from sqlalchemy import text
from jx_core.config import load_settings
from jx_core.database import Database


async def main() -> None:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        async with database.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            active_matches = await connection.scalar(
                text(
                    "SELECT count(*) FROM matches "
                    "WHERE status NOT IN ('FINISHED', 'TERMINATED')"
                )
            )
        print(f"revision={revision} active_matches={int(active_matches or 0)}")
    finally:
        await database.dispose()


asyncio.run(main())
PY
  )"; then
    fail "database preflight query failed"
    return
  fi

  printf '%s\n' "$output"
  if [[ "$output" == *"revision=${EXPECTED_REVISION}"* ]]; then
    pass "database migration is at the v2.1 head"
  else
    fail "database migration is not at the v2.1 head"
  fi
  if [[ "$output" == *"active_matches=0"* ]]; then
    pass "no non-terminal matches"
  else
    fail "non-terminal matches exist; do not deploy"
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
  check_python_packages
  check_http
  check_database

  if (( failures > 0 )); then
    printf 'Preflight failed with %d issue(s). No changes were made.\n' "$failures" >&2
    exit 1
  fi
  printf 'Preflight passed. No changes were made.\n'
}

main "$@"
