#!/usr/bin/env bash
# Refresh the whole DuckDB dataset: nflverse, Sleeper, and FFC ADP.
#
# Usage:
#   pipeline/refresh_all.sh              # always refresh
#   pipeline/refresh_all.sh --if-stale   # skip when the DB was refreshed <20h ago
#
# Logs to data/refresh.log. A flock guard makes concurrent invocations
# (SessionStart hook + manual run) exit instead of racing on the DB file.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$REPO/data/nfl.duckdb"
LOG="$REPO/data/refresh.log"
LOCK="$REPO/data/.refresh.lock"
MAX_AGE_HOURS=20

if [[ "${1:-}" == "--if-stale" && -f "$DB" ]]; then
    age_s=$(( $(date +%s) - $(stat -c %Y "$DB") ))
    if (( age_s < MAX_AGE_HOURS * 3600 )); then
        echo "DB is $(( age_s / 3600 ))h old (< ${MAX_AGE_HOURS}h) — skipping refresh." >&2
        exit 0
    fi
fi

mkdir -p "$REPO/data"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "Another refresh is already running — skipping." >&2
    exit 0
fi

{
    echo "=== refresh started $(date -Iseconds) ==="
    cd "$REPO"
    uv run python pipeline/refresh.py
    uv run python pipeline/sleeper.py
    uv run python pipeline/adp.py
    echo "=== refresh finished $(date -Iseconds) ==="
} >>"$LOG" 2>&1
