from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import (
    approvals,
    capabilities,
    capability_gaps,
    capability_implementation_runs,
    capability_proposals,
    channels,
    health,
    maintenance,
    notifications,
    ops,
    research,
    runs,
    source_proposals,
    task_change_proposals,
    task_templates,
    tasks,
    topics,
)


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
app.include_router(capabilities.router)
app.include_router(capability_gaps.router)
app.include_router(capability_implementation_runs.router)
app.include_router(capability_proposals.router)
app.include_router(channels.router)
app.include_router(research.sources_router)
app.include_router(research.research_router)
app.include_router(tasks.router)
app.include_router(task_templates.router)
app.include_router(task_change_proposals.router)
app.include_router(topics.router)
app.include_router(approvals.router)
app.include_router(runs.router)
app.include_router(source_proposals.router)
app.include_router(notifications.router)
app.include_router(maintenance.router)
app.mount("/ops/assets", StaticFiles(directory=ops.ops_assets_dir(), check_dir=False), name="ops-assets")
app.include_router(ops.router)
