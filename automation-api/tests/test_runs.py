from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import RunModel
from conftest import TOOL_HEADERS


def test_run_logs_redact_secret_values(client):
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(
            RunModel(
                id=run_id,
                task_id="redaction_task",
                status="completed",
                log={"message": "ok", "api_token": "super-secret-value", "nested": {"password": "hunter2"}},
            )
        )
        session.commit()

    response = client.get(f"/runs/{run_id}", headers=TOOL_HEADERS)
    assert response.status_code == 200
    log = response.json()["log"]
    assert log["api_token"] == "[REDACTED]"
    assert log["nested"]["password"] == "[REDACTED]"
