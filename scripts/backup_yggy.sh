#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="${AUTOMATION_API_BASE_URL:-http://127.0.0.1:8088}"
BACKUP_ROOT="${YGGY_BACKUP_ROOT:-${ROOT}/backups}"
STAMP="$(date -u +%Y%m%d-%H%M%SZ)"
OUT_DIR="${BACKUP_ROOT}/yggy-${STAMP}"
MYSQL_CONTAINER="${YGGY_MYSQL_CONTAINER:-automation-mysql}"
COMPOSE_FILES="${YGGY_COMPOSE_FILES:--f docker-compose.automation.yml -f docker-compose.https.yml}"

mkdir -p "${OUT_DIR}/api" "${OUT_DIR}/mysql" "${OUT_DIR}/compose"
chmod 0700 "${BACKUP_ROOT}"
chmod 0700 "${OUT_DIR}"

load_env() {
  if [[ -f "${ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "${ROOT}/.env"
    set +a
  fi
}

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command missing: $1" >&2
    exit 2
  fi
}

api_get() {
  local path="$1"
  local out="$2"
  curl -fsS \
    -H "X-Automation-Api-Key: ${AUTOMATION_ADMIN_API_KEY}" \
    "${BASE_URL%/}${path}" \
    -o "${out}"
}

json_pretty() {
  local file="$1"
  python3 -m json.tool "${file}" > "${file}.tmp"
  mv "${file}.tmp" "${file}"
}

load_env
require curl
require docker
require python3

if [[ -z "${AUTOMATION_ADMIN_API_KEY:-}" ]]; then
  echo "AUTOMATION_ADMIN_API_KEY is required in the local environment or .env" >&2
  exit 2
fi

echo "Creating Yggy backup at ${OUT_DIR}"

api_get "/health" "${OUT_DIR}/api/health.json"
api_get "/tasks" "${OUT_DIR}/api/tasks.json"
api_get "/topics" "${OUT_DIR}/api/topics.json"
api_get "/approvals" "${OUT_DIR}/api/approvals.json"
api_get "/runs?limit=100" "${OUT_DIR}/api/runs-recent.json"
curl -fsS "${BASE_URL%/}/openapi.json" -o "${OUT_DIR}/api/openapi.json"

for file in "${OUT_DIR}"/api/*.json; do
  json_pretty "${file}"
done

docker exec "${MYSQL_CONTAINER}" sh -lc '
  set -eu
  export MYSQL_PWD="${MYSQL_PASSWORD}"
  mysqldump \
    --single-transaction \
    --no-tablespaces \
    --routines \
    --triggers \
    --events \
    -u"${MYSQL_USER}" \
    "${MYSQL_DATABASE}"
' > "${OUT_DIR}/mysql/automation.sql"
chmod 0600 "${OUT_DIR}/mysql/automation.sql"

for compose_file in docker-compose.automation.yml docker-compose.lan.yml docker-compose.https.yml; do
  if [[ -f "${ROOT}/${compose_file}" ]]; then
    cp "${ROOT}/${compose_file}" "${OUT_DIR}/compose/${compose_file}"
  fi
done
printf '%s\n' "${COMPOSE_FILES}" > "${OUT_DIR}/compose/compose-files.txt"

git -C "${ROOT}" rev-parse HEAD > "${OUT_DIR}/git-commit.txt"
git -C "${ROOT}" status --short > "${OUT_DIR}/git-status.txt"

cat > "${OUT_DIR}/manifest.json" <<EOF
{
  "backup_created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "backup_kind": "yggy-local",
  "repository": "${ROOT}",
  "git_commit": "$(git -C "${ROOT}" rev-parse HEAD)",
  "api_base_url": "${BASE_URL%/}",
  "mysql_container": "${MYSQL_CONTAINER}",
  "contains_env_file": false,
  "contains_api_keys": false,
  "contains_discord_tokens": false,
  "contains_dashboard_password": false,
  "files": {
    "mysql_dump": "mysql/automation.sql",
    "tasks": "api/tasks.json",
    "topics": "api/topics.json",
    "approvals": "api/approvals.json",
    "recent_runs": "api/runs-recent.json",
    "openapi": "api/openapi.json",
    "compose_sources": "compose/"
  }
}
EOF

find "${OUT_DIR}" -type f -print | sort > "${OUT_DIR}/files.txt"

echo "Backup complete: ${OUT_DIR}"
echo "Reminder: backup excludes .env, API keys, Discord tokens, dashboard password, and Caddy private keys."
