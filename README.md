# Yggy Personal Automation Control Plane

Yggy is a local, self-hosted automation control plane for Open WebUI, Hermes/yggdrasil, Ollama, n8n, and Discord. It is designed as a narrow policy-enforced API, not as an unrestricted autonomous agent.

The core boundary is:

```text
Open WebUI -> Hermes/yggdrasil -> Automation API -> approved worker actions
```

The model-facing side can draft and inspect automations. It cannot approve higher-risk actions, run arbitrary shell commands, access the Docker socket, write arbitrary host files, or receive secrets.

## Architecture

```text
Open WebUI
  -> yggdrasil / Hermes
  -> automation-api
      -> MySQL
      -> versioned YAML configs
      -> audit log
      -> automation-worker
          -> n8n webhooks
          -> Discord dry-run or whitelisted webhooks
          -> RSS / HTTP sources
          -> optional Ollama summarizer
```

## Quickstart

This repository is a scaffold. Review it before deploying it.

```bash
cd /srv/Yggy
cp .env.example .env
# edit .env manually; do not commit it
python -m venv .venv
. .venv/bin/activate
python -m pip install -e "automation-api[test]" -e "automation-worker[test]"
python scripts/validate_configs.py
docker compose -f docker-compose.automation.yml config
```

Run tests locally:

```bash
pytest automation-api/tests automation-worker/tests
python scripts/validate_configs.py
```

When ready, bring up only the new automation scaffold:

```bash
docker compose -f docker-compose.automation.yml up -d automation-mysql automation-api automation-worker
curl http://127.0.0.1:8088/health
```

The API port is published on localhost by default. For trusted LAN access to the operations dashboard, set `AUTOMATION_API_LAN_PUBLISHED_HOST` in `.env` to the host's LAN address and include the LAN override:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.lan.yml up -d automation-api
```

Then open `http://<lan-ip>:8088/ops`.

For encrypted LAN access without touching Technitium's `80/443` listeners, use the HTTPS override:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.https.yml up -d yggy-https-proxy
```

The default HTTPS dashboard URL is `https://yggy.b1.germering:8443/ops`.

Do not connect Open WebUI/Hermes until you have reviewed [docs/OPENWEBUI_HERMES_INTEGRATION.md](docs/OPENWEBUI_HERMES_INTEGRATION.md).

## Defaults

- API framework: FastAPI
- Database: MySQL
- Validation: Pydantic v2
- Testing: pytest
- Worker scheduling: croniter-based polling scaffold
- Discord: dry-run by default
- n8n: optional execution backend, not approval authority
- Ollama summarizer: disabled by default

## Safety Model

- No arbitrary shell execution by the LLM.
- No Docker socket exposed to the API, worker, Hermes, or Open WebUI tools.
- Separate tool, worker, and admin API keys.
- L2+ approvals require an admin-controlled process.
- Secrets stay in `.env`, Docker secrets, n8n credentials, or a local secret manager.
- Task YAML, Open WebUI Knowledge, prompts, chat history, and logs must not contain secrets.

See [SECURITY.md](SECURITY.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Backup

Create a local backup:

```bash
scripts/backup_yggy.sh
```

Restore is dry-run by default and requires `--apply` before importing MySQL. See [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).
