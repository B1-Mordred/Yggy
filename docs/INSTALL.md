# Install

## Prerequisites

- Docker and Docker Compose
- Python 3.12 or newer for local validation
- A private `.env` copied from `.env.example`

## Local Validation

```bash
cd /srv/Yggy
python -m venv .venv
. .venv/bin/activate
python -m pip install -e "automation-api[test]" -e "automation-worker[test]" -e "metrics-exporter[test]" -e "bragi[test]" -e "channel-bridge[test]"
python scripts/validate_configs.py
pytest automation-api/tests automation-worker/tests metrics-exporter/tests bragi/tests yggdrasil/tests channel-bridge/tests
docker compose -f docker-compose.automation.yml config >/dev/null
```

## MySQL

The compose scaffold uses `automation-mysql` on the internal `automation_net` network. Set these values in `.env`:

```env
MYSQL_DATABASE=automation
MYSQL_USER=automation
MYSQL_PASSWORD=<random>
MYSQL_ROOT_PASSWORD=<random>
DATABASE_URL=mysql+pymysql://automation:<random>@automation-mysql:3306/automation
```

Do not commit `.env`.

## Deployment

Deployment is manual:

```bash
docker compose -f docker-compose.automation.yml up -d automation-mysql
docker compose -f docker-compose.automation.yml up -d --build automation-api metrics-exporter automation-worker bragi
```

`bragi` is optional but recommended for natural human-facing interaction. Keep
the existing Yggdrasil provider strict and add Bragi as a separate
OpenAI-compatible provider in Open WebUI. Do not give Bragi the admin API key,
approval nonces, shell tools, Docker access, filesystem write tools, or secrets.

For Discord, prefer the first-class channel bridge over a generic Hermes
Discord profile:

```bash
docker compose -f docker-compose.automation.yml up -d --build channel-bridge
```

Set `DISCORD_BOT_TOKEN`, `DISCORD_HOME_CHANNEL`, and optionally
`DISCORD_ALLOWED_USER_IDS` in `.env`. Stop any legacy Discord responder using
the same bot token before starting `channel-bridge`.

If Open WebUI is in a separate Docker stack and cannot reach `http://bragi:8650`,
set `BRAGI_LAN_PUBLISHED_HOST` to the host LAN address and use the LAN override
to expose only Bragi's OpenAI-compatible endpoint:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.lan.yml up -d bragi
```

The Yggdrasil canonical action endpoint is part of the host-side Yggdrasil
action API. Review `docs/BRAGI_HEIMDAL_INTEGRATION.md` before replacing or
restarting any existing Hermes/Yggdrasil service. Do not run deployment commands
from an automation model session.
