#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

pytest automation-api/tests automation-worker/tests yggdrasil/tests
python scripts/validate_configs.py

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose -f docker-compose.automation.yml config >/dev/null
  echo "Compose config validation passed"
else
  echo "Docker Compose unavailable; skipped compose validation"
fi
