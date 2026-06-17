#!/usr/bin/env bash
# scripts/backup_db.sh — automated pg_dump for UAT/staging promotion gate.
#
# Usage:
#   bash scripts/backup_db.sh                  # one-shot, uses $DATABASE_URL
#   bash scripts/backup_db.sh --check-only     # exit 0 if dump healthy, no write
#
# Cron (daily 02:00):
#   0 2 * * * cd /var/www/html/ragbot && bash scripts/backup_db.sh >> /var/log/ragbot/backup.log 2>&1
#
# Honors env:
#   DATABASE_URL          — required, postgres://user:pass@host:port/db
#   RAGBOT_BACKUP_DIR     — default /var/backups/ragbot
#   RAGBOT_BACKUP_RETAIN  — default 7 (days)
#
# Sacred contract:
#   - Fail loud (non-zero exit) on any pg_dump or rotation error
#   - Atomic file move (write to .tmp, rename on success)
#   - Custom format (-Fc) for parallel restore + compression
#   - Quiet by default (only emit one summary line) so cron mail isn't noisy

set -euo pipefail

readonly BACKUP_DIR="${RAGBOT_BACKUP_DIR:-/var/backups/ragbot}"
readonly RETAIN_DAYS="${RAGBOT_BACKUP_RETAIN:-7}"
readonly TS="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL not set" >&2
    exit 2
fi

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
    CHECK_ONLY=1
fi

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"  # restrict — backups contain PII / secrets in encrypted form

# Verify pg_dump exists
if ! command -v pg_dump >/dev/null 2>&1; then
    echo "ERROR: pg_dump not on PATH (install postgresql-client)" >&2
    exit 3
fi

# Connectivity check (1s timeout) before committing to a long dump
if ! pg_isready -d "${DATABASE_URL}" -t 5 >/dev/null 2>&1; then
    echo "ERROR: pg_isready failed for DATABASE_URL host" >&2
    exit 4
fi

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    echo "OK: backup precondition green (dir=${BACKUP_DIR}, pg reachable)"
    exit 0
fi

readonly OUT_FINAL="${BACKUP_DIR}/ragbot_${TS}.dump"
readonly OUT_TMP="${OUT_FINAL}.tmp"

# pg_dump custom format. -j parallelism intentionally OMITTED — custom
# format is single-threaded and that is fine for typical sizes; -j is
# only useful with directory format (-Fd).
if ! pg_dump -Fc --no-owner --no-acl -f "${OUT_TMP}" "${DATABASE_URL}"; then
    rm -f "${OUT_TMP}"
    echo "ERROR: pg_dump failed (kept no partial file)" >&2
    exit 5
fi

# Atomic rename — only if dump completed cleanly
mv "${OUT_TMP}" "${OUT_FINAL}"
chmod 600 "${OUT_FINAL}"

# Rotate — drop dumps older than RETAIN_DAYS
find "${BACKUP_DIR}" -maxdepth 1 -name 'ragbot_*.dump' -type f -mtime "+${RETAIN_DAYS}" -delete

# Single summary line (cron-friendly)
SIZE_HUMAN="$(du -h "${OUT_FINAL}" | cut -f1)"
COUNT="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'ragbot_*.dump' -type f | wc -l)"
echo "OK: ${OUT_FINAL} (${SIZE_HUMAN}); ${COUNT} dump(s) retained ≤${RETAIN_DAYS}d"
