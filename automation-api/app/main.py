from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers import approvals, health, maintenance, notifications, ops, runs, task_change_proposals, task_templates, tasks, topics


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Yggy Automation API",
    version="0.1.0",
    description="Policy-enforced automation control plane for yggdrasil.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(tasks.router)
app.include_router(task_templates.router)
app.include_router(task_change_proposals.router)
app.include_router(topics.router)
app.include_router(approvals.router)
app.include_router(runs.router)
app.include_router(notifications.router)
app.include_router(maintenance.router)
app.include_router(ops.router)
