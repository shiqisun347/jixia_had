#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only wrapper. The Python implementation only reads systemd and /proc;
# it never reloads units, changes limits, or restarts services.
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PYTHON_BIN="${JX_PREFLIGHT_PYTHON:-python3}"
exec "$PYTHON_BIN" "$SCRIPT_DIR/runtime_guard_audit.py" "$@"
