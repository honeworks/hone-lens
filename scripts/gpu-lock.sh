#!/usr/bin/env bash
# Run a command while holding the machine-wide GPU lock shared by the honeworks test suites.
# Usage: scripts/gpu-lock.sh uv run pytest -m gpu
set -euo pipefail
LOCK="${HONE_GPU_LOCK:-/tmp/honeworks-gpu.lock}"
TIMEOUT="${HONE_GPU_LOCK_TIMEOUT:-7200}"   # seconds to wait for the lock
touch "$LOCK"
exec 9<>"$LOCK"
echo "[gpu-lock] waiting for $LOCK (timeout ${TIMEOUT}s) ..." >&2
if ! flock -w "$TIMEOUT" 9; then
  echo "[gpu-lock] timed out waiting for the GPU lock" >&2
  exit 75
fi
echo "[gpu-lock] acquired by $(basename "$PWD") pid $$ at $(date -Is)" >&2
echo "$(basename "$PWD") $$ $(date -Is)" > "${LOCK}.holder" || true
export HONE_GPU_LOCK_HELD=1
set +e
"$@"
status=$?
set -e
rm -f "${LOCK}.holder" || true
echo "[gpu-lock] released (exit $status)" >&2
exit $status
