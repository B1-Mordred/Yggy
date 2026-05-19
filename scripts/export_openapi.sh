#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/openapi/automation-api.openapi.json"
mkdir -p "${ROOT}/openapi"

if command -v curl >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:8088/openapi.json" -o "${OUT}" 2>/dev/null; then
  echo "Wrote ${OUT} from running API"
  exit 0
fi

YGGY_ROOT="${ROOT}" PYTHONPATH="${ROOT}/automation-api" python - <<'PY'
import json
import os
from pathlib import Path
from app.main import app

root = Path(os.environ["YGGY_ROOT"])
out = root / "openapi" / "automation-api.openapi.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
print(f"Wrote {out} from local app import")
PY
