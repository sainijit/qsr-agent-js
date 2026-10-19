#!/usr/bin/env bash
# Verify a backup file without touching any running database: checksum, archive readability, then a full restore
# into an isolated throw-away Postgres (no network, no volume) and schema / RLS / row-count checks.
#
#   scripts/backup/verify-backup.sh /var/backups/zuvo/zuvo_db-zuvo-ai-20260101T000000Z.dump [--quick]
#
# To verify an off-host copy, download it first (aws s3 cp s3://.../file.dump{,.sha256} .) and run this on it.
# Exit codes: 0 ok | 2 usage/environment | 3 verification failed
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

FILE=""; QUICK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) QUICK=1; shift ;;
    -h|--help) sed -n 2,8p "$0"; exit 0 ;;
    -*) die 2 "unknown argument: $1" ;;
    *) FILE=$1; shift ;;
  esac
done
[[ -n "$FILE" && -f "$FILE" ]] || die 2 "usage: verify-backup.sh <file.dump> [--quick]"

if [[ -f "$FILE.sha256" ]]; then
  [[ "$(awk '{print $1}' "$FILE.sha256")" == "$(sha256_of "$FILE")" ]] || die 3 "sha256 mismatch: file is corrupt or altered"
  log "checksum ok"
else
  log "WARNING: no $FILE.sha256 next to the file; integrity not checked"
fi

SCRATCH=$(scratch_start)
trap 'scratch_stop "$SCRATCH"' EXIT
docker exec -i "$SCRATCH" pg_restore --list < "$FILE" >/dev/null || die 3 "archive unreadable (pg_restore --list failed)"
log "archive readable"
[[ $QUICK -eq 1 ]] && { log "quick verification ok"; exit 0; }

T0=$(date +%s)
restore_into "$SCRATCH" verify_db "$FILE" >/dev/null 2>&1 || die 3 "restore into scratch postgres failed"
RESULT=$(check_restored "$SCRATCH" verify_db) || die 3 "restored database failed checks: $RESULT"
log "restore ok in $(( $(date +%s) - T0 ))s"

MANIFEST="${FILE%.dump}.manifest.json"
if [[ -f "$MANIFEST" ]]; then
  python3 - "$MANIFEST" "$RESULT" <<'PY' || die 3 "restored contents differ from the manifest recorded at backup time"
import json, sys
m = json.load(open(sys.argv[1])); r = json.loads(sys.argv[2])
want = (m.get("restored") or {}).get("row_counts") or {}
bad = {t: (want[t], r["row_counts"].get(t)) for t in want if want[t] != r["row_counts"].get(t)}
if m.get("alembic_version") != r["alembic_version"]:
    bad["alembic_version"] = (m.get("alembic_version"), r["alembic_version"])
if bad:
    print(f"mismatch: {bad}", file=sys.stderr); sys.exit(1)
print("manifest match: schema version and row counts identical", file=sys.stderr)
PY
fi
echo "$RESULT"
