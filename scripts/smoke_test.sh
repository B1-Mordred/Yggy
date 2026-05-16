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

"${PYTEST_BIN}" automation-api/tests automation-worker/tests yggdrasil/tests
"${PYTHON_BIN}" scripts/validate_configs.py

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose -f docker-compose.automation.yml config >/dev/null
  echo "Compose config validation passed"
else
  echo "Docker Compose unavailable; skipped compose validation"
fi
