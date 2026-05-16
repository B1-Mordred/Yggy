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
python -m pip install -e "automation-api[test]" -e "automation-worker[test]"
python scripts/validate_configs.py
pytest automation-api/tests automation-worker/tests
docker compose -f docker-compose.automation.yml config
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
docker compose -f docker-compose.automation.yml up -d automation-mysql automation-api automation-worker
```

Do not run deployment commands from an automation model session.
