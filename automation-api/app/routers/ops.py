from __future__ import annotations

import secrets
from datetime import timezone
from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import ApiRole, classify_api_key
from app.config import get_settings
from app.database import get_session
from app.models import ApprovalModel, AuditEventModel, HeartbeatModel, RunModel, TaskModel, utcnow
from app.routers.health import WORKER_HEARTBEAT_MAX_AGE_SECONDS, heartbeat_to_dict

router = APIRouter(tags=["ops"])
basic_security = HTTPBasic(auto_error=False)


def require_ops_access(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_security)] = None,
    x_automation_api_key: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not settings.ops_dashboard_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ops dashboard is disabled")
    if x_automation_api_key:
        try:
            if classify_api_key(x_automation_api_key) == ApiRole.ADMIN:
                return
        except HTTPException:
            pass
    if not settings.ops_dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ops dashboard password is not configured",
        )
    if (
        credentials
        and secrets.compare_digest(credentials.username, settings.ops_dashboard_user)
        and secrets.compare_digest(credentials.password, settings.ops_dashboard_password)
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ops dashboard credentials required",
        headers={"WWW-Authenticate": "Basic"},
    )


@router.get("/ops", response_class=HTMLResponse, include_in_schema=False)
def ops_dashboard(_: None = Depends(require_ops_access)) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


@router.get("/ops/status", include_in_schema=False)
def ops_status(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    now = utcnow()
    database = {"connected": False}
    try:
        session.execute(text("SELECT 1"))
        database["connected"] = True
    except Exception as exc:  # pragma: no cover - exercised only with unavailable DB
        database["error"] = exc.__class__.__name__

    tasks = session.query(TaskModel).order_by(TaskModel.id).all()
    recent_runs = session.query(RunModel).order_by(RunModel.created_at.desc()).limit(20).all()
    latest_by_task: dict[str, RunModel] = {}
    for run in recent_runs:
        latest_by_task.setdefault(run.task_id, run)

    pending_approvals = (
        session.query(ApprovalModel)
        .filter(ApprovalModel.status == "pending")
        .order_by(ApprovalModel.created_at.desc())
        .limit(20)
        .all()
    )
    active_runs = [run for run in recent_runs if run.status in {"queued", "queued_dry_run", "running", "running_dry_run"}]
    latest_retention = (
        session.query(AuditEventModel)
        .filter(AuditEventModel.action.in_(["maintenance.retention.preview", "maintenance.retention.apply"]))
        .order_by(AuditEventModel.created_at.desc())
        .first()
    )
    worker = heartbeat_to_dict(session.get(HeartbeatModel, "automation-worker")) if database["connected"] else {"ok": False}

    return {
        "generated_at": now,
        "service": {
            "status": "ok" if database["connected"] and worker.get("ok") is not False else "degraded",
            "database": database,
            "worker": worker,
        },
        "counts": {
            "tasks": len(tasks),
            "enabled_tasks": sum(1 for task in tasks if task.enabled),
            "pending_approvals": len(pending_approvals),
            "active_runs": len(active_runs),
        },
        "tasks": [_task_summary(task, latest_by_task.get(task.id)) for task in tasks],
        "recent_runs": [_run_summary(run) for run in recent_runs[:10]],
        "pending_approvals": [_approval_summary(approval) for approval in pending_approvals],
        "retention": {
            "policy": {
                "run_retention_days": get_settings().run_retention_days,
                "audit_retention_days": get_settings().audit_retention_days,
                "temp_task_retention_hours": get_settings().temp_task_retention_hours,
            },
            "latest": _audit_summary(latest_retention),
        },
        "safety": {
            "read_only": True,
            "openapi_exposed": False,
            "worker_heartbeat_max_age_seconds": WORKER_HEARTBEAT_MAX_AGE_SECONDS,
        },
    }


def _task_summary(task: TaskModel, latest_run: RunModel | None) -> dict:
    config = task.config or {}
    trigger = config.get("trigger") if isinstance(config.get("trigger"), dict) else {}
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    return {
        "id": task.id,
        "name": task.name,
        "type": task.type,
        "enabled": task.enabled,
        "status": task.status,
        "approval_level": task.approval_level,
        "dry_run": bool(runtime.get("dry_run", True)),
        "trigger": {"kind": trigger.get("kind"), "cron": trigger.get("cron"), "timezone": trigger.get("timezone")},
        "output": {"channel": output.get("channel"), "target": output.get("target")},
        "latest_run": _run_summary(latest_run) if latest_run else None,
        "updated_at": task.updated_at,
    }


def _run_summary(run: RunModel | None) -> dict | None:
    if run is None:
        return None
    log = run.log if isinstance(run.log, dict) else {}
    result = log.get("result") if isinstance(log.get("result"), dict) else {}
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else {}
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "result_status": result.get("status"),
        "failed_count": result.get("failed_count"),
        "notify": result.get("notify"),
        "notification": {
            "sent": notification.get("sent") if notification else None,
            "dry_run": notification.get("dry_run") if notification else None,
            "target": notification.get("target") if notification else None,
            "transport": notification.get("transport") if notification else None,
        },
    }


def _approval_summary(approval: ApprovalModel) -> dict:
    return {
        "id": approval.id,
        "task_id": approval.task_id,
        "approval_level": approval.approval_level,
        "requested_by": approval.requested_by,
        "risk": approval.risk,
        "created_at": approval.created_at,
        "summary": approval.summary[:280],
    }


def _audit_summary(audit: AuditEventModel | None) -> dict | None:
    if audit is None:
        return None
    return {
        "action": audit.action,
        "created_at": audit.created_at,
        "detail": audit.detail,
    }


DASHBOARD_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Yggy Operations</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #18202a;
      --muted: #5f6b7a;
      --line: #d9dee7;
      --ok: #0f7b4b;
      --warn: #9a5b00;
      --bad: #b42318;
      --accent: #2457c5;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101418;
        --panel: #171d24;
        --text: #eef2f6;
        --muted: #a7b0bd;
        --line: #2b3542;
        --ok: #49c783;
        --warn: #e0a33a;
        --bad: #ff6b61;
        --accent: #8fb4ff;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header, main {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; }}
    header {{ padding: 24px 0 12px; display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
    }}
    button:hover {{ border-color: var(--accent); }}
    .meta {{ color: var(--muted); font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }}
    .section {{ margin: 18px 0; }}
    .panel, .metric, table {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ padding: 12px; min-height: 72px; }}
    .metric .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
    .panel {{ padding: 14px; }}
    .status {{ display: inline-flex; gap: 6px; align-items: center; font-weight: 650; }}
    .dot {{ width: 9px; height: 9px; border-radius: 99px; background: var(--muted); display: inline-block; }}
    .ok .dot {{ background: var(--ok); }}
    .warn .dot {{ background: var(--warn); }}
    .bad .dot {{ background: var(--bad); }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .bad {{ color: var(--bad); }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; border: 0; min-width: 760px; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 650; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .pill {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 12px; }}
    .empty {{ color: var(--muted); padding: 12px 0; }}
    @media (max-width: 860px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{escape("Yggy Operations")}</h1>
      <div class="meta" id="generated">Loading status...</div>
    </div>
    <button id="refresh" type="button" title="Refresh status">Refresh</button>
  </header>
  <main>
    <section class="grid" id="metrics"></section>
    <section class="section panel" id="service"></section>
    <section class="section">
      <h2>Tasks</h2>
      <div class="table-wrap"><table id="tasks"></table></div>
    </section>
    <section class="section">
      <h2>Recent Runs</h2>
      <div class="table-wrap"><table id="runs"></table></div>
    </section>
    <section class="section panel" id="approvals"></section>
    <section class="section panel" id="retention"></section>
  </main>
  <script>
    const text = value => value === null || value === undefined || value === '' ? 'n/a' : String(value);
    const shortId = value => value ? String(value).slice(0, 8) : 'n/a';
    const statusClass = value => value === true || value === 'ok' || value === 'completed' ? 'ok'
      : value === false || value === 'failed' || value === 'degraded' ? 'bad' : 'warn';
    function statusLabel(value, label) {{
      const cls = statusClass(value);
      return `<span class="status ${{cls}}"><span class="dot"></span>${{label || text(value)}}</span>`;
    }}
    function metric(label, value, sub) {{
      return `<div class="metric"><div class="meta">${{label}}</div><div class="value">${{value}}</div><div class="meta">${{sub || ''}}</div></div>`;
    }}
    function renderTable(id, headers, rows) {{
      const table = document.getElementById(id);
      table.innerHTML = `<thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join('')}}</tr></thead>`
        + `<tbody>${{rows.map(row => `<tr>${{row.map(cell => `<td>${{cell}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    async function loadStatus() {{
      const response = await fetch('/ops/status', {{credentials: 'same-origin'}});
      if (!response.ok) throw new Error(`status ${{response.status}}`);
      const data = await response.json();
      document.getElementById('generated').textContent = `Generated ${{new Date(data.generated_at).toLocaleString()}}`;
      document.getElementById('metrics').innerHTML = [
        metric('Service', statusLabel(data.service.status), `worker age ${{text(data.service.worker.age_seconds)}}s`),
        metric('Tasks', data.counts.tasks, `${{data.counts.enabled_tasks}} enabled`),
        metric('Active Runs', data.counts.active_runs, 'queued or running'),
        metric('Pending Approvals', data.counts.pending_approvals, 'local approval only'),
      ].join('');
      document.getElementById('service').innerHTML = `
        <h2>Service Health</h2>
        <div>Database: ${{statusLabel(data.service.database.connected, data.service.database.connected ? 'connected' : 'degraded')}}</div>
        <div>Worker: ${{statusLabel(data.service.worker.ok, data.service.worker.status)}} <span class="meta">last seen ${{text(data.service.worker.last_seen_at)}}</span></div>
      `;
      renderTable('tasks', ['Task', 'Type', 'State', 'Trigger', 'Output', 'Latest Run'], data.tasks.map(task => [
        `<code>${{task.id}}</code><br><span class="meta">${{task.name}}</span>`,
        `<span class="pill">${{task.type}}</span><br><span class="meta">${{task.approval_level}}</span>`,
        `${{statusLabel(task.enabled, task.enabled ? 'enabled' : 'disabled')}}<br><span class="meta">dry run ${{task.dry_run}}</span>`,
        `<code>${{text(task.trigger.cron)}}</code><br><span class="meta">${{text(task.trigger.timezone)}}</span>`,
        `${{text(task.output.channel)}}<br><span class="meta">${{text(task.output.target)}}</span>`,
        task.latest_run ? `<code>${{shortId(task.latest_run.id)}}</code> ${{statusLabel(task.latest_run.status)}}<br><span class="meta">${{text(task.latest_run.completed_at)}}</span>` : '<span class="meta">no runs</span>',
      ]));
      renderTable('runs', ['Run', 'Task', 'Status', 'Result', 'Notification', 'Completed'], data.recent_runs.map(run => [
        `<code title="${{run.id}}">${{shortId(run.id)}}</code>`,
        `<code>${{run.task_id}}</code>`,
        statusLabel(run.status),
        `${{text(run.result_status)}}${{run.failed_count !== null && run.failed_count !== undefined ? `<br><span class="meta">failed checks ${{run.failed_count}}</span>` : ''}}`,
        `${{run.notification.sent === true ? 'sent' : run.notification.sent === false ? 'not sent' : 'n/a'}}<br><span class="meta">${{text(run.notification.target || run.notification.transport)}}</span>`,
        text(run.completed_at),
      ]));
      const approvals = data.pending_approvals;
      document.getElementById('approvals').innerHTML = '<h2>Pending Approvals</h2>' + (
        approvals.length ? approvals.map(item => `<div><code>${{item.id}}</code> for <code>${{item.task_id}}</code> <span class="pill">${{item.approval_level}}</span><br><span class="meta">${{item.summary}}</span></div>`).join('<hr>')
        : '<div class="empty">No pending approvals.</div>'
      );
      const latestRetention = data.retention.latest;
      document.getElementById('retention').innerHTML = `
        <h2>Retention</h2>
        <div class="meta">Runs ${{data.retention.policy.run_retention_days}}d, audit ${{data.retention.policy.audit_retention_days}}d, temporary tasks ${{data.retention.policy.temp_task_retention_hours}}h</div>
        ${{latestRetention ? `<div>Latest: <code>${{latestRetention.action}}</code> at ${{text(latestRetention.created_at)}}</div>` : '<div class="empty">No cleanup recorded yet.</div>'}}
      `;
    }}
    async function refresh() {{
      try {{ await loadStatus(); }}
      catch (error) {{ document.getElementById('generated').textContent = `Unable to load status: ${{error.message}}`; }}
    }}
    document.getElementById('refresh').addEventListener('click', refresh);
    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""
