# Backup And Restore

Yggy backups are local operational snapshots. They are intended for recovery from accidental data loss, failed upgrades, or host migration.

Backups do not include:

- `.env`
- API keys
- Discord bot tokens
- dashboard passwords
- Technitium admin password
- Caddy private keys
- Open WebUI chat history or Knowledge

## Create A Backup

```bash
scripts/backup_yggy.sh
```

Default output:

```text
backups/yggy-YYYYmmdd-HHMMSSZ/
```

Backup contents:

```text
api/health.json
api/tasks.json
api/topics.json
api/approvals.json
api/runs-recent.json
api/openapi.json
mysql/automation.sql
compose/docker-compose.automation.yml
compose/docker-compose.lan.yml
compose/docker-compose.https.yml
compose/compose-files.txt
git-commit.txt
git-status.txt
manifest.json
files.txt
```

The API exports are redacted by the automation API. Compose source files are copied without resolving `.env`, so secrets are not expanded into the backup. The MySQL dump is a full control-plane database dump, so treat it as sensitive operational data even though task YAML must not contain secrets.

## Restore Dry-Run

```bash
scripts/restore_yggy.sh --backup-dir backups/yggy-YYYYmmdd-HHMMSSZ
```

Dry-run prints the manifest and dump metadata. It does not modify MySQL.

## Apply Restore

Stop API and worker first so no run state changes while MySQL is being restored:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.https.yml stop automation-worker automation-api
```

Apply the restore:

```bash
scripts/restore_yggy.sh --backup-dir backups/yggy-YYYYmmdd-HHMMSSZ --apply
```

Restart:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.https.yml up -d automation-api automation-worker
```

Validate:

```bash
python scripts/validate_configs.py
curl http://127.0.0.1:8088/health
```

## Storage

The `backups/` directory is ignored by Git. Store a copy somewhere durable if the host disk is the main failure risk.

Do not copy `.env` into backups unless you are using a separate encrypted secret backup workflow.
