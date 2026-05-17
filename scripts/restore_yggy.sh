#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR=""
APPLY=false
MYSQL_CONTAINER="${YGGY_MYSQL_CONTAINER:-automation-mysql}"

usage() {
  cat <<'EOF'
Restore Yggy MySQL state from a local backup directory.

Default mode is dry-run. Use --apply to import the database dump.

Usage:
  scripts/restore_yggy.sh --backup-dir backups/yggy-YYYYmmdd-HHMMSSZ
  scripts/restore_yggy.sh --backup-dir backups/yggy-YYYYmmdd-HHMMSSZ --apply

This script never restores .env or secrets. Stop automation-api and automation-worker
before applying a database restore.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir)
      BACKUP_DIR="${2:?--backup-dir requires a path}"
      shift 2
      ;;
    --apply)
      APPLY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${BACKUP_DIR}" ]]; then
  usage >&2
  exit 2
fi

if [[ "${BACKUP_DIR}" != /* ]]; then
  BACKUP_DIR="${ROOT}/${BACKUP_DIR}"
fi

MANIFEST="${BACKUP_DIR}/manifest.json"
SQL_DUMP="${BACKUP_DIR}/mysql/automation.sql"

if [[ ! -r "${MANIFEST}" ]]; then
  echo "Backup manifest not found: ${MANIFEST}" >&2
  exit 1
fi
if [[ ! -r "${SQL_DUMP}" ]]; then
  echo "MySQL dump not found: ${SQL_DUMP}" >&2
  exit 1
fi

echo "Yggy restore"
echo "  mode: $([[ "${APPLY}" == "true" ]] && echo apply || echo dry-run)"
echo "  backup: ${BACKUP_DIR}"
echo "  mysql container: ${MYSQL_CONTAINER}"
echo

python3 - <<PY
import json
from pathlib import Path
manifest = json.loads(Path("${MANIFEST}").read_text(encoding="utf-8"))
print("Backup created:", manifest.get("backup_created_at"))
print("Git commit:", manifest.get("git_commit"))
print("Contains .env:", manifest.get("contains_env_file"))
print("Contains API keys:", manifest.get("contains_api_keys"))
print("Contains Discord tokens:", manifest.get("contains_discord_tokens"))
PY

echo
echo "Database dump:"
wc -c "${SQL_DUMP}" | awk '{print "  bytes: " $1}'
grep -E '^-- MySQL dump|^-- Host:|^-- Server version' "${SQL_DUMP}" | sed 's/^/  /' || true

if [[ "${APPLY}" != "true" ]]; then
  cat <<EOF

Dry-run only. To apply:
  docker compose -f docker-compose.automation.yml -f docker-compose.https.yml stop automation-worker automation-api
  scripts/restore_yggy.sh --backup-dir "${BACKUP_DIR}" --apply
  docker compose -f docker-compose.automation.yml -f docker-compose.https.yml up -d automation-api automation-worker
EOF
  exit 0
fi

echo
echo "Applying database restore. This replaces tables in the configured MySQL database."
docker exec -i "${MYSQL_CONTAINER}" sh -lc '
  set -eu
  export MYSQL_PWD="${MYSQL_PASSWORD}"
  mysql -u"${MYSQL_USER}" "${MYSQL_DATABASE}"
' < "${SQL_DUMP}"

echo "Restore applied."
echo "Run scripts/validate_configs.py and check http://127.0.0.1:8088/health after restarting services."
