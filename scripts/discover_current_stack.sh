#!/usr/bin/env bash
set -euo pipefail

echo "== OS =="
cat /etc/os-release || true

echo "== Docker version =="
docker version || true

echo "== Docker Compose version =="
docker compose version || true

echo "== Running containers =="
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' || true

echo "== Docker networks =="
docker network ls || true
