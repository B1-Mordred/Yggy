#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PYTHON_BIN="${ROOT}/.venv/bin/python"
PYTEST_BIN="${ROOT}/.venv/bin/pytest"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python"
fi

if [[ ! -x "${PYTEST_BIN}" ]]; then
  PYTEST_BIN="pytest"
fi

"${PYTEST_BIN}" automation-api/tests automation-worker/tests metrics-exporter/tests yggdrasil/tests bragi/tests channel-bridge/tests
"${PYTHON_BIN}" scripts/validate_configs.py

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose -f docker-compose.automation.yml config >/dev/null
  echo "Compose config validation passed"
  AUTOMATION_API_LAN_PUBLISHED_HOST=127.0.0.1 BRAGI_LAN_PUBLISHED_HOST=127.0.0.1 docker compose -f docker-compose.automation.yml -f docker-compose.lan.yml config >/dev/null
  echo "LAN compose config validation passed"
  YGGY_HTTPS_PUBLISHED_HOST=127.0.0.1 docker compose -f docker-compose.automation.yml -f docker-compose.https.yml config >/dev/null
  echo "HTTPS compose config validation passed"
else
  echo "Docker Compose unavailable; skipped compose validation"
fi
