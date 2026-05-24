import {
  Activity,
  AlertTriangle,
  Archive,
  Bell,
  Boxes,
  Check,
  ChevronRight,
  ClipboardList,
  Database,
  Eye,
  FileText,
  Gauge,
  Hammer,
  History,
  Loader2,
  LogOut,
  Moon,
  Pause,
  Play,
  Radio,
  RefreshCcw,
  Search,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  Trash2,
  X
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, fetchJson, postJson, queryString } from './api';
import type { ActionHeaderKind, JsonRecord, OpsBootstrap, ViewId } from './types';

type OpsAction = {
  path: string;
  body?: unknown;
  header?: ActionHeaderKind;
  method?: 'POST' | 'PUT';
  label: string;
};

type Toast = {
  kind: 'ok' | 'bad';
  title: string;
  detail?: string;
};

type ThemeMode = 'light' | 'dark';

const THEME_STORAGE_KEY = 'yggy.ops.theme';

const VIEWS: Array<{ id: ViewId; label: string; icon: any; description: string }> = [
  { id: 'builder', label: 'Builder', icon: Hammer, description: 'Capability work queue' },
  { id: 'tasks', label: 'Tasks', icon: ClipboardList, description: 'Existing automations' },
  { id: 'runs', label: 'Runs', icon: Activity, description: 'Execution history' },
  { id: 'reviews', label: 'Reviews', icon: ShieldCheck, description: 'Approvals and changes' },
  { id: 'sources', label: 'Sources', icon: Boxes, description: 'Source proposals' },
  { id: 'audit', label: 'Audit', icon: History, description: 'Operator activity' },
  { id: 'system', label: 'System', icon: Server, description: 'Runtime and security' }
];

const PAGE_SIZES = [5, 10, 20, 25, 50, 100];

export default function App() {
  return <OpsApp />;
}

function OpsApp() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<ViewId>('builder');
  const [density, setDensity] = useState<'comfortable' | 'compact'>('compact');
  const [theme, setTheme] = useState<ThemeMode>(() => loadThemeMode());
  const [toast, setToast] = useState<Toast | null>(null);
  const statusQuery = useQuery<JsonRecord>({
    queryKey: ['ops-status'],
    queryFn: () => fetchJson('/ops/status'),
    refetchInterval: 30_000
  });
  const bootstrapQuery = useQuery<OpsBootstrap>({
    queryKey: ['ops-bootstrap'],
    queryFn: () => fetchJson('/ops/bootstrap')
  });
  const eventState = useOpsEvents();

  const actionMutation = useMutation({
    mutationFn: (action: OpsAction) =>
      postJson(action.path, action.body ?? {}, action.header, action.method ?? 'POST'),
    onSuccess: (data, action) => {
      queryClient.invalidateQueries();
      setToast({
        kind: 'ok',
        title: `${action.label} succeeded`,
        detail: summarizePayload(data)
      });
    },
    onError: (error, action) => {
      setToast({
        kind: 'bad',
        title: `${action.label} failed`,
        detail: error instanceof ApiError ? stringifyDetail(error.detail) : String(error)
      });
    }
  });

  const runAction = (action: OpsAction) => actionMutation.mutate(action);
  const status = statusQuery.data || {};
  const counts = (status.counts || {}) as JsonRecord;

  useEffect(() => {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return (
    <div className={`ops-shell density-${density} theme-${theme}`}>
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">Y</div>
          <div>
            <div className="brand-title">Yggy Operations</div>
            <div className="brand-subtitle">Local automation control</div>
          </div>
        </div>
        <nav className="nav-list" aria-label="Operations views">
          {VIEWS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={`nav-item ${view === item.id ? 'active' : ''}`}
                onClick={() => setView(item.id)}
                type="button"
              >
                <Icon size={17} />
                <span>{item.label}</span>
                {navBadge(item.id, counts) ? <b>{navBadge(item.id, counts)}</b> : null}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <a href="/ops/legacy">Legacy UI</a>
          <form method="post" action="/ops/logout">
            <button className="link-button" type="submit">
              <LogOut size={15} />
              Sign out
            </button>
          </form>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <div className="eyebrow">Secure operator surface</div>
            <h1>{VIEWS.find((item) => item.id === view)?.label}</h1>
            <p>{VIEWS.find((item) => item.id === view)?.description}</p>
          </div>
          <div className="topbar-actions">
            <ConnectionBadge eventState={eventState} status={status} />
            <label className="density-toggle">
              <SlidersHorizontal size={15} />
              <select value={density} onChange={(event) => setDensity(event.target.value as any)}>
                <option value="compact">Compact</option>
                <option value="comfortable">Comfortable</option>
              </select>
            </label>
            <button
              className={`theme-toggle ${theme === 'dark' ? 'active' : ''}`}
              onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              aria-pressed={theme === 'dark'}
              type="button"
            >
              {theme === 'dark' ? <Moon size={15} /> : <Sun size={15} />}
              <span>{theme === 'dark' ? 'Dark' : 'Light'}</span>
            </button>
            <button className="icon-button" onClick={() => queryClient.invalidateQueries()} title="Refresh" type="button">
              <RefreshCcw size={17} />
            </button>
          </div>
        </header>

        {toast ? <ToastBar toast={toast} onClose={() => setToast(null)} /> : null}
        {statusQuery.error ? <ErrorPanel title="Status load failed" error={statusQuery.error} /> : null}

        <section className="summary-grid">
          <MetricCard label="Tasks" value={counts.tasks ?? 0} detail={`${counts.enabled_tasks ?? 0} enabled`} icon={ClipboardList} />
          <MetricCard label="Reviews" value={counts.pending_reviews ?? 0} detail="approval queue" icon={ShieldCheck} tone={(counts.pending_reviews ?? 0) > 0 ? 'warn' : 'ok'} />
          <MetricCard label="Active runs" value={counts.active_runs ?? 0} detail="queued or running" icon={Activity} />
          <MetricCard label="Worker" value={status.service?.worker?.ok === false ? 'degraded' : 'ok'} detail={status.service?.status || 'unknown'} icon={Gauge} tone={status.service?.status === 'ok' ? 'ok' : 'warn'} />
        </section>

        <AttentionQueue status={status} setView={setView} />

        {view === 'builder' ? <BuilderView runAction={runAction} /> : null}
        {view === 'tasks' ? <TasksView status={status} runAction={runAction} /> : null}
        {view === 'runs' ? <RunsView /> : null}
        {view === 'reviews' ? <ReviewsView runAction={runAction} /> : null}
        {view === 'sources' ? <SourcesView runAction={runAction} /> : null}
        {view === 'audit' ? <AuditView /> : null}
        {view === 'system' ? <SystemView bootstrap={bootstrapQuery.data} status={status} /> : null}
      </main>
    </div>
  );
}

function loadThemeMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function useOpsEvents() {
  const queryClient = useQueryClient();
  const [state, setState] = useState<{ mode: string; lastEvent?: string; lastAt?: string }>({ mode: 'connecting' });

  useEffect(() => {
    if (!('EventSource' in window)) {
      setState({ mode: 'polling' });
      return;
    }
    const source = new EventSource('/ops/events');
    const onReady = (event: MessageEvent) => {
      setState({ mode: 'live', lastEvent: 'ready', lastAt: new Date().toISOString() });
      try {
        JSON.parse(event.data);
      } catch {
        /* keep the connection even if a frame is malformed */
      }
    };
    const onStatus = (event: MessageEvent) => {
      setState({ mode: 'live', lastEvent: 'status.updated', lastAt: new Date().toISOString() });
      try {
        const payload = JSON.parse(event.data);
        queryClient.setQueryData(['ops-realtime'], payload);
      } catch {
        /* polling remains the fallback */
      }
      queryClient.invalidateQueries({ queryKey: ['ops-status'] });
      queryClient.invalidateQueries({ queryKey: ['capability-implementation-runs'] });
    };
    source.addEventListener('ops.ready', onReady as EventListener);
    source.addEventListener('status.updated', onStatus as EventListener);
    source.onerror = () => setState((current) => ({ ...current, mode: 'polling' }));
    return () => source.close();
  }, [queryClient]);

  return state;
}

function BuilderView({ runAction }: { runAction: (action: OpsAction) => void }) {
  const [proposalStatus, setProposalStatus] = useState('pending');
  const [implementationStatus, setImplementationStatus] = useState('');
  const [implementationLimit, setImplementationLimit] = useState(25);
  const proposalsQuery = useQuery<JsonRecord>({
    queryKey: ['capability-proposals', proposalStatus],
    queryFn: () => fetchJson(`/ops/capability-proposals${queryString({ status: proposalStatus, page_size: 20 })}`)
  });
  const gapsQuery = useQuery<JsonRecord>({
    queryKey: ['capability-gaps'],
    queryFn: () => fetchJson('/ops/capability-gaps')
  });
  const implementationQuery = useQuery<JsonRecord>({
    queryKey: ['capability-implementation-runs', implementationStatus, implementationLimit],
    queryFn: () =>
      fetchJson(
        `/ops/capability-implementation-runs${queryString({
          status: implementationStatus,
          limit: implementationLimit
        })}`
      ),
    refetchInterval: 30_000
  });

  const proposals = proposalsQuery.data?.proposals || [];
  const gaps = gapsQuery.data?.gaps || [];
  const runs = implementationQuery.data?.runs || [];

  return (
    <div className="view-grid builder-grid">
      <section className="panel wide">
        <PanelTitle icon={Hammer} title="Capability building cockpit" subtitle="Turn backlog proposals into reviewed implementation runs." />
        <div className="toolbar-row">
          <label>
            Status
            <select value={proposalStatus} onChange={(event) => setProposalStatus(event.target.value)}>
              <option value="">All</option>
              <option value="pending">Pending</option>
              <option value="accepted">Accepted</option>
              <option value="implementation_planned">Implementation planned</option>
              <option value="implemented">Implemented</option>
              <option value="rejected">Rejected</option>
              <option value="superseded">Superseded</option>
            </select>
          </label>
        </div>
        <div className="card-list">
          {proposals.length ? (
            proposals.map((proposal: JsonRecord) => (
              <CapabilityProposalCard key={proposal.id} proposal={proposal} runAction={runAction} />
            ))
          ) : (
            <EmptyState text="No capability proposals match the current filter." />
          )}
        </div>
      </section>

      <section className="panel">
        <PanelTitle icon={AlertTriangle} title="Capability gaps" subtitle="Natural requests that should become non-executable proposals." />
        <CapabilityGapEditor gaps={gaps} runAction={runAction} />
      </section>

      <section className="panel">
        <PanelTitle icon={Radio} title="Implementation runs" subtitle="Heavy jobs stay serialized and visible." />
        <div className="implementation-toolbar">
          <label>
            Status
            <select value={implementationStatus} onChange={(event) => setImplementationStatus(event.target.value)}>
              <option value="">All</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </label>
          <PageSizeSelect value={implementationLimit} onChange={setImplementationLimit} label="Runs" />
        </div>
        <div className="implementation-summary">
          Showing {runs.length} {implementationStatus ? implementationStatus : 'recent'} run{runs.length === 1 ? '' : 's'}
        </div>
        <div className="timeline-list implementation-scroll">
          {runs.length ? (
            runs.map((run: JsonRecord) => <ImplementationRun key={run.id} run={run} />)
          ) : (
            <EmptyState text="No implementation runs are queued or recorded." />
          )}
        </div>
      </section>
    </div>
  );
}

function CapabilityProposalCard({ proposal, runAction }: { proposal: JsonRecord; runAction: (action: OpsAction) => void }) {
  const planStatus = proposal.implementation_plan?.status || 'no plan';
  const reason = (label: string) => window.prompt(`${label} reason`, '') ?? null;
  const decision = (value: string, label: string) => {
    const note = reason(label);
    if (note === null) return;
    runAction({
      path: `/ops/capability-proposals/${proposal.id}/${value}`,
      header: 'capabilityProposal',
      body: { reason: note },
      label
    });
  };
  const implement = () => {
    const note = reason('Queue implementation');
    if (note === null) return;
    runAction({
      path: `/ops/capability-proposals/${proposal.id}/implement`,
      header: 'capabilityImplementation',
      body: { reason: note },
      label: 'Queue implementation'
    });
  };

  return (
    <article className="work-card">
      <div className="work-card-head">
        <div>
          <div className="card-title">{proposal.title || proposal.suggested_capability_id}</div>
          <code>{proposal.suggested_capability_id}</code>
        </div>
        <div className="pill-row">
          <StatusPill value={proposal.status} />
          <StatusPill value={planStatus} />
        </div>
      </div>
      <p>{proposal.purpose || proposal.original_request_preview || 'No purpose recorded.'}</p>
      <div className="mini-grid">
        <KeyValue label="Task type" value={proposal.suggested_task_type} />
        <KeyValue label="Approval" value={proposal.likely_approval_level} />
        <KeyValue label="Requested by" value={proposal.requested_by} />
      </div>
      <TagList title="Required inputs" items={proposal.required_inputs} />
      <TagList title="Safety rules" items={proposal.safety_rules} />
      <div className="button-row">
        {proposal.status === 'pending' ? (
          <>
            <button onClick={() => decision('accept', 'Accept proposal')} type="button">
              <Check size={15} />
              Accept
            </button>
            <button onClick={() => decision('reject', 'Reject proposal')} className="secondary danger" type="button">
              <X size={15} />
              Reject
            </button>
          </>
        ) : null}
        <button onClick={() => decision('plan', 'Plan implementation')} className="secondary" type="button">
          <FileText size={15} />
          Plan
        </button>
        <button onClick={implement} className="primary" type="button">
          <Play size={15} />
          Queue implementation
        </button>
        <button onClick={() => decision('implemented', 'Mark implemented')} className="secondary" type="button">
          <ShieldCheck size={15} />
          Mark implemented
        </button>
        <button onClick={() => decision('supersede', 'Supersede proposal')} className="secondary" type="button">
          <Archive size={15} />
          Supersede
        </button>
      </div>
    </article>
  );
}

function CapabilityGapEditor({ gaps, runAction }: { gaps: JsonRecord[]; runAction: (action: OpsAction) => void }) {
  const emptyGap = {
    id: '',
    enabled: true,
    status: 'active',
    route: 'propose_new_capability',
    title: '',
    purpose: '',
    suggested_capability_id: '',
    suggested_task_type: '',
    likely_approval_level: 'L1_NOTIFY_ONLY',
    trigger_terms: [],
    context_terms: [],
    exclude_terms: [],
    required_inputs: [],
    safety_rules: ['must not execute shell commands', 'must not expose secrets'],
    non_goals: ['no direct execution from Bragi'],
    review_notes: ''
  };
  const [form, setForm] = useState<JsonRecord>(emptyGap);
  const set = (key: string, value: any) => setForm((current) => ({ ...current, [key]: value }));
  const listField = (key: string, value: string) =>
    set(
      key,
      value
        .split(/[,\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
    );
  const save = () => {
    if (!form.id) {
      window.alert('Gap ID is required.');
      return;
    }
    runAction({
      path: `/ops/capability-gaps/${encodeURIComponent(form.id)}`,
      method: 'PUT',
      header: 'capabilityGap',
      body: form,
      label: 'Save capability gap'
    });
  };

  return (
    <div className="gap-editor">
      <div className="gap-list">
        {gaps.length ? (
          gaps.map((gap) => (
            <button key={gap.id} className="gap-chip" onClick={() => setForm({ ...gap })} type="button">
              <span>{gap.title || gap.id}</span>
              <StatusPill value={gap.status} />
            </button>
          ))
        ) : (
          <EmptyState text="No configured gaps." />
        )}
      </div>
      <div className="form-grid">
        <label>
          Gap ID
          <input value={form.id || ''} onChange={(event) => set('id', event.target.value)} placeholder="disk_usage.v1" />
        </label>
        <label>
          Title
          <input value={form.title || ''} onChange={(event) => set('title', event.target.value)} placeholder="Storage monitoring" />
        </label>
        <label>
          Capability ID
          <input value={form.suggested_capability_id || ''} onChange={(event) => set('suggested_capability_id', event.target.value)} placeholder="storage_monitoring.v1" />
        </label>
        <label>
          Task type
          <input value={form.suggested_task_type || ''} onChange={(event) => set('suggested_task_type', event.target.value)} placeholder="storage_monitoring" />
        </label>
        <label>
          Approval level
          <select value={form.likely_approval_level || 'L1_NOTIFY_ONLY'} onChange={(event) => set('likely_approval_level', event.target.value)}>
            <option value="L0_READ_ONLY">L0 read only</option>
            <option value="L1_NOTIFY_ONLY">L1 notify only</option>
            <option value="L2_LOCAL_WRITE">L2 local write</option>
            <option value="L3_EXTERNAL_SIDE_EFFECT">L3 external side effect</option>
            <option value="L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE">L4 manual only</option>
          </select>
        </label>
        <label>
          Status
          <select value={form.status || 'active'} onChange={(event) => set('status', event.target.value)}>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
            <option value="implemented">Implemented</option>
            <option value="superseded">Superseded</option>
          </select>
        </label>
        <label className="span-2">
          Purpose
          <textarea value={form.purpose || ''} onChange={(event) => set('purpose', event.target.value)} rows={3} />
        </label>
        <label>
          Trigger terms
          <textarea value={(form.trigger_terms || []).join(', ')} onChange={(event) => listField('trigger_terms', event.target.value)} rows={3} />
        </label>
        <label>
          Context terms
          <textarea value={(form.context_terms || []).join(', ')} onChange={(event) => listField('context_terms', event.target.value)} rows={3} />
        </label>
        <label>
          Required inputs
          <textarea value={(form.required_inputs || []).join('\n')} onChange={(event) => listField('required_inputs', event.target.value)} rows={3} />
        </label>
        <label>
          Safety rules
          <textarea value={(form.safety_rules || []).join('\n')} onChange={(event) => listField('safety_rules', event.target.value)} rows={3} />
        </label>
      </div>
      <div className="button-row">
        <button onClick={save} type="button">
          <Check size={15} />
          Save gap
        </button>
        <button onClick={() => setForm(emptyGap)} className="secondary" type="button">
          Clear
        </button>
      </div>
    </div>
  );
}

function TasksView({ status, runAction }: { status: JsonRecord; runAction: (action: OpsAction) => void }) {
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [filter, setFilter] = useState('');
  const [pageSize, setPageSize] = useState(20);
  const detailQuery = useQuery<JsonRecord>({
    queryKey: ['task-detail', selectedTaskId],
    queryFn: () => fetchJson(`/ops/tasks/${encodeURIComponent(selectedTaskId)}`),
    enabled: Boolean(selectedTaskId)
  });
  const tasks = (status.tasks || []) as JsonRecord[];
  const visibleTasks = tasks.filter((task) =>
    `${task.id} ${task.name} ${task.type} ${task.status} ${task.approval_level}`.toLowerCase().includes(filter.toLowerCase())
  );
  const pageTasks = visibleTasks.slice(0, pageSize);

  return (
    <div className="view-grid two-column">
      <section className="panel wide">
        <PanelTitle icon={ClipboardList} title="Tasks" subtitle="Run, pause, resume, archive, and inspect bounded automations." />
        <div className="toolbar-row">
          <SearchBox value={filter} onChange={setFilter} placeholder="Filter tasks" />
          <PageSizeSelect value={pageSize} onChange={setPageSize} />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Status</th>
                <th>Schedule</th>
                <th>Output</th>
                <th>Latest run</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageTasks.map((task) => (
                <tr key={task.id} className={selectedTaskId === task.id ? 'selected-row' : ''}>
                  <td>
                    <button className="inline-link" onClick={() => setSelectedTaskId(task.id)} type="button">
                      {task.name || task.id}
                    </button>
                    <div className="muted">{task.id}</div>
                    <div className="muted">{task.type}</div>
                  </td>
                  <td>
                    <StatusPill value={task.status} />
                    <div className="muted">{task.approval_level}</div>
                  </td>
                  <td>
                    <code>{task.trigger?.cron || 'manual'}</code>
                    <div className="muted">{task.trigger?.timezone}</div>
                  </td>
                  <td>
                    {task.output?.target || 'none'}
                    <div className="muted">{task.output?.channel}</div>
                  </td>
                  <td>{task.latest_run ? <StatusPill value={task.latest_run.status} /> : <span className="muted">never</span>}</td>
                  <td>
                    <TaskActionButtons task={task} runAction={runAction} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <PanelTitle icon={Eye} title="Task detail" subtitle="Redacted config, approvals, history, and allowed actions." />
        {selectedTaskId ? (
          detailQuery.isLoading ? (
            <Loading />
          ) : detailQuery.error ? (
            <ErrorPanel title="Task detail failed" error={detailQuery.error} />
          ) : (
            <TaskDetail detail={detailQuery.data} runAction={runAction} />
          )
        ) : (
          <EmptyState text="Select a task to inspect it." />
        )}
      </section>
    </div>
  );
}

function TaskActionButtons({ task, runAction }: { task: JsonRecord; runAction: (action: OpsAction) => void }) {
  const dryRun = () =>
    runAction({
      path: `/ops/tasks/${task.id}/run`,
      header: 'run',
      body: { mode: 'dry_run' },
      label: 'Queue dry run'
    });
  const liveRun = () => {
    if (!window.confirm(`Queue a live run for ${task.id}?`)) return;
    runAction({
      path: `/ops/tasks/${task.id}/run`,
      header: 'run',
      body: { mode: 'live' },
      label: 'Queue live run'
    });
  };
  const state = (action: 'pause' | 'resume') =>
    runAction({
      path: `/ops/tasks/${task.id}/${action}`,
      header: 'taskState',
      body: {},
      label: action === 'pause' ? 'Pause task' : 'Resume task'
    });
  const archive = () => {
    if (!window.confirm(`Archive disabled task ${task.id}? Audit and run history are retained.`)) return;
    runAction({
      path: `/ops/tasks/${task.id}/archive`,
      header: 'taskArchive',
      body: {},
      label: 'Archive task'
    });
  };
  return (
    <div className="button-row tight">
      <button onClick={dryRun} className="secondary" title="Dry run" type="button">
        <Play size={14} />
      </button>
      <button onClick={liveRun} className="secondary" title="Live run" type="button">
        <Bell size={14} />
      </button>
      {task.enabled ? (
        <button onClick={() => state('pause')} className="secondary" title="Pause" type="button">
          <Pause size={14} />
        </button>
      ) : (
        <button onClick={() => state('resume')} className="secondary" title="Resume" type="button">
          <RefreshCcw size={14} />
        </button>
      )}
      <button onClick={archive} className="secondary danger" title="Archive" type="button">
        <Trash2 size={14} />
      </button>
    </div>
  );
}

function TaskDetail({ detail, runAction }: { detail?: JsonRecord; runAction: (action: OpsAction) => void }) {
  if (!detail) return null;
  const task = detail.task || {};
  return (
    <div className="detail-stack">
      <div className="detail-heading">
        <div>
          <h3>{task.name || task.id}</h3>
          <code>{task.id}</code>
        </div>
        <StatusPill value={task.status} />
      </div>
      <TaskActionButtons task={task} runAction={runAction} />
      <div className="mini-grid">
        <KeyValue label="Type" value={task.type} />
        <KeyValue label="Approval" value={task.approval_level} />
        <KeyValue label="Cron" value={task.trigger?.cron || 'manual'} />
        <KeyValue label="Target" value={task.output?.target || 'none'} />
      </div>
      <Subsection title="Allowed actions">
        <RawJson data={detail.allowed_actions} compact />
      </Subsection>
      <Subsection title="Approvals">
        {(detail.approvals || []).length ? (
          <div className="timeline-list">
            {detail.approvals.map((approval: JsonRecord) => (
              <div className="timeline-item" key={approval.id}>
                <StatusPill value={approval.status} />
                <span>{approval.id}</span>
                <span className="muted">{approval.created_at}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState text="No approval history." />
        )}
      </Subsection>
      <Subsection title="Recent runs">
        {(detail.recent_runs || []).length ? (
          <div className="timeline-list">
            {detail.recent_runs.map((run: JsonRecord) => (
              <div className="timeline-item" key={run.id}>
                <StatusPill value={run.status} />
                <span>{run.id}</span>
                <span className="muted">{run.created_at}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState text="No recent runs." />
        )}
      </Subsection>
      <Subsection title="Redacted config">
        <RawJson data={detail.config} />
      </Subsection>
    </div>
  );
}

function RunsView() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [taskId, setTaskId] = useState('');
  const [runStatus, setRunStatus] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const runsQuery = useQuery<JsonRecord>({
    queryKey: ['runs', page, pageSize, taskId, runStatus],
    queryFn: () => fetchJson(`/ops/runs${queryString({ page, page_size: pageSize, task_id: taskId, status: runStatus })}`)
  });
  const detailQuery = useQuery<JsonRecord>({
    queryKey: ['run-detail', selectedRunId],
    queryFn: () => fetchJson(`/ops/runs/${encodeURIComponent(selectedRunId)}`),
    enabled: Boolean(selectedRunId)
  });
  const runs = runsQuery.data?.runs || [];

  return (
    <div className="view-grid two-column">
      <section className="panel wide">
        <PanelTitle icon={Activity} title="Runs" subtitle="Filter execution history and inspect redacted results." />
        <div className="toolbar-row">
          <SearchBox value={taskId} onChange={setTaskId} placeholder="Task ID" />
          <select value={runStatus} onChange={(event) => setRunStatus(event.target.value)}>
            <option value="">Any status</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="dry_run">Dry run</option>
            <option value="failed">Failed</option>
          </select>
          <PageSizeSelect value={pageSize} onChange={setPageSize} />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Task</th>
                <th>Status</th>
                <th>Created</th>
                <th>Notification</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run: JsonRecord) => (
                <tr key={run.id} className={selectedRunId === run.id ? 'selected-row' : ''}>
                  <td>
                    <button className="inline-link" onClick={() => setSelectedRunId(run.id)} type="button">
                      {run.id}
                    </button>
                  </td>
                  <td>{run.task_id}</td>
                  <td><StatusPill value={run.status} /></td>
                  <td>{formatDate(run.created_at)}</td>
                  <td>{run.notification?.sent === true ? 'sent' : run.notification?.sent === false ? 'not sent' : 'unknown'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination page={page} setPage={setPage} pagination={runsQuery.data?.pagination} />
      </section>
      <section className="panel">
        <PanelTitle icon={FileText} title="Run detail" subtitle="Result, digest health, and notification decision." />
        {selectedRunId ? (
          detailQuery.isLoading ? <Loading /> : detailQuery.error ? <ErrorPanel title="Run detail failed" error={detailQuery.error} /> : <RawJson data={detailQuery.data} />
        ) : (
          <EmptyState text="Select a run to inspect it." />
        )}
      </section>
    </div>
  );
}

function ReviewsView({ runAction }: { runAction: (action: OpsAction) => void }) {
  const [approvalPageSize, setApprovalPageSize] = useState(20);
  const approvalsQuery = useQuery<JsonRecord>({
    queryKey: ['reviews', approvalPageSize],
    queryFn: () => fetchJson(`/ops/reviews${queryString({ kind: 'all', page_size: approvalPageSize })}`)
  });
  const changesQuery = useQuery<JsonRecord>({
    queryKey: ['task-change-proposals'],
    queryFn: () => fetchJson('/ops/task-change-proposals?page_size=20')
  });
  const approvals = approvalsQuery.data?.reviews || [];
  const changes = changesQuery.data?.proposals || [];

  return (
    <div className="view-grid two-column">
      <section className="panel wide">
        <PanelTitle icon={ShieldCheck} title="Approvals" subtitle="L0/L1 can be approved without a nonce; L2+ requires the delivered nonce." />
        <div className="toolbar-row">
          <PageSizeSelect value={approvalPageSize} onChange={setApprovalPageSize} />
        </div>
        <div className="card-list">
          {approvals.length ? (
            approvals.map((approval: JsonRecord) => <ApprovalCard key={approval.id} approval={approval} runAction={runAction} />)
          ) : (
            <EmptyState text="No pending approvals." />
          )}
        </div>
      </section>
      <section className="panel">
        <PanelTitle icon={FileText} title="Task change proposals" subtitle="Changes are approved, rejected, then applied through guarded endpoints." />
        <div className="card-list">
          {changes.length ? (
            changes.map((proposal: JsonRecord) => <TaskChangeCard key={proposal.id} proposal={proposal} runAction={runAction} />)
          ) : (
            <EmptyState text="No task change proposals." />
          )}
        </div>
      </section>
    </div>
  );
}

function ApprovalCard({ approval, runAction }: { approval: JsonRecord; runAction: (action: OpsAction) => void }) {
  const approve = () => {
    const nonce = window.prompt('Approval nonce. Leave blank for L0/L1 approvals.', '') ?? null;
    if (nonce === null) return;
    runAction({
      path: `/ops/approvals/${approval.id}/approve`,
      header: 'approval',
      body: { nonce: nonce || null },
      label: 'Approve task'
    });
  };
  const reject = () => {
    const reason = window.prompt('Reject reason', '') ?? null;
    if (reason === null) return;
    runAction({
      path: `/ops/approvals/${approval.id}/reject`,
      header: 'approval',
      body: { reason },
      label: 'Reject task'
    });
  };
  return (
    <article className="work-card">
      <div className="work-card-head">
        <div>
          <div className="card-title">{approval.task?.name || approval.task_id}</div>
          <code>{approval.id}</code>
        </div>
        <StatusPill value={approval.approval_level} />
      </div>
      <p>{approval.summary}</p>
      <div className="mini-grid">
        <KeyValue label="Task" value={approval.task_id} />
        <KeyValue label="Requested by" value={approval.requested_by} />
        <KeyValue label="Risk" value={approval.risk} />
      </div>
      <TagList title="Actions" items={approval.review?.actions || []} />
      <div className="button-row">
        <button onClick={approve} type="button">
          <Check size={15} />
          Approve
        </button>
        <button onClick={reject} className="secondary danger" type="button">
          <X size={15} />
          Reject
        </button>
      </div>
    </article>
  );
}

function TaskChangeCard({ proposal, runAction }: { proposal: JsonRecord; runAction: (action: OpsAction) => void }) {
  const approve = () => {
    const nonce = window.prompt('Task-change nonce is required.', '') ?? null;
    if (!nonce) return;
    runAction({
      path: `/ops/task-change-proposals/${proposal.id}/approve`,
      header: 'taskChange',
      body: { nonce },
      label: 'Approve task change'
    });
  };
  const reject = () => {
    const reason = window.prompt('Reject reason', '') ?? null;
    if (reason === null) return;
    runAction({
      path: `/ops/task-change-proposals/${proposal.id}/reject`,
      header: 'taskChange',
      body: { reason },
      label: 'Reject task change'
    });
  };
  const apply = () =>
    runAction({
      path: `/ops/task-change-proposals/${proposal.id}/apply`,
      header: 'taskChange',
      body: {},
      label: 'Apply task change'
    });
  return (
    <article className="work-card">
      <div className="work-card-head">
        <div>
          <div className="card-title">{proposal.summary || proposal.task_id}</div>
          <code>{proposal.id}</code>
        </div>
        <StatusPill value={proposal.status} />
      </div>
      <div className="mini-grid">
        <KeyValue label="Task" value={proposal.task_id} />
        <KeyValue label="Approval" value={proposal.approval_level} />
        <KeyValue label="Requested by" value={proposal.requested_by} />
      </div>
      <div className="button-row">
        <button onClick={approve} type="button">
          <Check size={15} />
          Approve
        </button>
        <button onClick={apply} className="primary" type="button">
          <Play size={15} />
          Apply
        </button>
        <button onClick={reject} className="secondary danger" type="button">
          <X size={15} />
          Reject
        </button>
      </div>
    </article>
  );
}

function SourcesView({ runAction }: { runAction: (action: OpsAction) => void }) {
  const [proposalStatus, setProposalStatus] = useState('pending');
  const [sourceId, setSourceId] = useState('');
  const query = useQuery<JsonRecord>({
    queryKey: ['source-proposals', proposalStatus, sourceId],
    queryFn: () => fetchJson(`/ops/source-proposals${queryString({ status: proposalStatus, source_id: sourceId, page_size: 50 })}`)
  });
  const proposals = query.data?.proposals || [];
  return (
    <section className="panel">
      <PanelTitle icon={Boxes} title="Source proposals" subtitle="Approve and apply operator-reviewed public information sources." />
      <div className="toolbar-row">
        <SearchBox value={sourceId} onChange={setSourceId} placeholder="Source ID" />
        <select value={proposalStatus} onChange={(event) => setProposalStatus(event.target.value)}>
          <option value="">All</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="applied">Applied</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>
      <div className="card-list">
        {proposals.length ? (
          proposals.map((proposal: JsonRecord) => <SourceProposalCard key={proposal.id} proposal={proposal} runAction={runAction} />)
        ) : (
          <EmptyState text="No source proposals match the filter." />
        )}
      </div>
    </section>
  );
}

function SourceProposalCard({ proposal, runAction }: { proposal: JsonRecord; runAction: (action: OpsAction) => void }) {
  const decision = (value: 'approve' | 'reject' | 'apply', label: string) => {
    const reason = window.prompt(`${label} reason`, '') ?? null;
    if (reason === null) return;
    runAction({
      path: `/ops/source-proposals/${proposal.id}/${value}`,
      header: 'sourceProposal',
      body: { reason },
      label
    });
  };
  return (
    <article className="work-card">
      <div className="work-card-head">
        <div>
          <div className="card-title">{proposal.source_config?.name || proposal.source_id}</div>
          <code>{proposal.source_id}</code>
        </div>
        <StatusPill value={proposal.status} />
      </div>
      <p>{proposal.summary || proposal.source_config?.description}</p>
      <div className="mini-grid">
        <KeyValue label="Mode" value={proposal.source_config?.ingestion_mode} />
        <KeyValue label="Fit" value={proposal.source_config?.ai_safe_fit} />
        <KeyValue label="Requested by" value={proposal.requested_by} />
      </div>
      <TagList title="Categories" items={proposal.source_config?.categories || []} />
      <div className="button-row">
        <button onClick={() => decision('approve', 'Approve source')} type="button">
          <Check size={15} />
          Approve
        </button>
        <button onClick={() => decision('apply', 'Apply source')} className="primary" type="button">
          <Play size={15} />
          Apply
        </button>
        <button onClick={() => decision('reject', 'Reject source')} className="secondary danger" type="button">
          <X size={15} />
          Reject
        </button>
      </div>
    </article>
  );
}

function AuditView() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [q, setQ] = useState('');
  const [action, setAction] = useState('');
  const query = useQuery<JsonRecord>({
    queryKey: ['audit', page, pageSize, q, action],
    queryFn: () => fetchJson(`/ops/audit${queryString({ page, page_size: pageSize, q, action })}`)
  });
  const events = query.data?.events || [];
  return (
    <section className="panel">
      <PanelTitle icon={History} title="Audit" subtitle="Redacted operator and automation API audit trail." />
      <div className="toolbar-row">
        <SearchBox value={q} onChange={setQ} placeholder="Search audit" />
        <SearchBox value={action} onChange={setAction} placeholder="Action filter" />
        <PageSizeSelect value={pageSize} onChange={setPageSize} />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Resource</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event: JsonRecord) => (
              <tr key={event.id}>
                <td>{formatDate(event.created_at)}</td>
                <td>{event.actor_role}</td>
                <td><code>{event.action}</code></td>
                <td>{event.resource_type}:{event.resource_id}</td>
                <td><RawJson data={event.detail} compact /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} setPage={setPage} pagination={query.data?.pagination} />
    </section>
  );
}

function SystemView({ bootstrap, status }: { bootstrap?: OpsBootstrap; status: JsonRecord }) {
  return (
    <div className="view-grid two-column">
      <section className="panel">
        <PanelTitle icon={Server} title="Runtime" subtitle="Current service, worker, retention, and session metadata." />
        <div className="mini-grid">
          <KeyValue label="Service" value={status.service?.status} />
          <KeyValue label="Database" value={status.service?.database?.connected ? 'connected' : 'degraded'} />
          <KeyValue label="Worker" value={status.service?.worker?.ok === false ? 'degraded' : 'ok'} />
          <KeyValue label="Legacy UI" value={bootstrap?.app?.legacy_url || '/ops/legacy'} />
        </div>
        <Subsection title="Retention">
          <RawJson data={status.retention} />
        </Subsection>
      </section>
      <section className="panel">
        <PanelTitle icon={Database} title="Boundary checks" subtitle="The frontend receives no admin key and stores no approval nonce." />
        <RawJson data={bootstrap?.security || {}} />
        <Subsection title="Bootstrap">
          <RawJson data={bootstrap} />
        </Subsection>
      </section>
    </div>
  );
}

function AttentionQueue({ status, setView }: { status: JsonRecord; setView: (view: ViewId) => void }) {
  const counts = (status.counts || {}) as JsonRecord;
  const recentRuns = (status.recent_runs || []) as JsonRecord[];
  const failedRuns = recentRuns.filter((run) => String(run.status || '').includes('failed'));
  const items = [
    { label: 'Pending approvals', count: counts.pending_approvals || 0, view: 'reviews' as ViewId },
    { label: 'Task changes', count: counts.open_task_change_proposals || 0, view: 'reviews' as ViewId },
    { label: 'Capability proposals', count: counts.pending_capability_proposals || 0, view: 'builder' as ViewId },
    { label: 'Source proposals', count: counts.open_source_proposals || 0, view: 'sources' as ViewId },
    { label: 'Active runs', count: counts.active_runs || 0, view: 'runs' as ViewId },
    { label: 'Failed recent runs', count: failedRuns.length, view: 'runs' as ViewId }
  ].filter((item) => item.count > 0);

  if (!items.length) {
    return (
      <section className="attention ok">
        <Check size={17} />
        <span>No operator queue items need attention.</span>
      </section>
    );
  }
  return (
    <section className="attention">
      <AlertTriangle size={17} />
      <strong>Attention queue</strong>
      <div className="attention-items">
        {items.map((item) => (
          <button key={item.label} onClick={() => setView(item.view)} type="button">
            {item.label}
            <b>{item.count}</b>
            <ChevronRight size={14} />
          </button>
        ))}
      </div>
    </section>
  );
}

function ImplementationRun({ run }: { run: JsonRecord }) {
  return (
    <div className="timeline-item vertical">
      <div className="timeline-head">
        <StatusPill value={run.status} />
        <code>{run.capability_id}</code>
      </div>
      <div>{run.summary || run.error || 'No summary recorded yet.'}</div>
      <div className="muted">{run.id}</div>
      <div className="muted">{formatDate(run.updated_at || run.created_at)}</div>
    </div>
  );
}

function ConnectionBadge({ eventState, status }: { eventState: JsonRecord; status: JsonRecord }) {
  const degraded = status.service?.status && status.service.status !== 'ok';
  return (
    <div className={`connection-badge ${degraded ? 'warn' : eventState.mode === 'live' ? 'ok' : ''}`}>
      <Radio size={15} />
      <span>{eventState.mode === 'live' ? 'Live' : 'Polling'}</span>
    </div>
  );
}

function MetricCard({ label, value, detail, icon: Icon, tone = 'neutral' }: { label: string; value: any; detail: string; icon: any; tone?: string }) {
  return (
    <article className={`metric-card ${tone}`}>
      <Icon size={19} />
      <div>
        <div className="metric-value">{value}</div>
        <div className="metric-label">{label}</div>
        <div className="muted">{detail}</div>
      </div>
    </article>
  );
}

function PanelTitle({ icon: Icon, title, subtitle }: { icon: any; title: string; subtitle: string }) {
  return (
    <div className="panel-title">
      <Icon size={18} />
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

function StatusPill({ value }: { value?: any }) {
  const text = String(value || 'unknown');
  const className = text.includes('failed') || text.includes('rejected') || text.includes('degraded') ? 'bad' : text.includes('pending') || text.includes('queued') || text.includes('planned') ? 'warn' : text.includes('enabled') || text.includes('approved') || text.includes('ok') || text.includes('implemented') ? 'ok' : '';
  return <span className={`status-pill ${className}`}>{text}</span>;
}

function KeyValue({ label, value }: { label: string; value: any }) {
  return (
    <div className="key-value">
      <span>{label}</span>
      <strong>{value === undefined || value === null || value === '' ? 'none' : String(value)}</strong>
    </div>
  );
}

function TagList({ title, items }: { title: string; items?: any[] }) {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) return null;
  return (
    <div className="tag-list-block">
      <div className="muted">{title}</div>
      <div className="tag-list">
        {values.slice(0, 12).map((item, index) => (
          <span key={`${item}-${index}`}>{String(item)}</span>
        ))}
      </div>
    </div>
  );
}

function RawJson({ data, compact = false }: { data: any; compact?: boolean }) {
  return <pre className={compact ? 'raw-json compact' : 'raw-json'}>{JSON.stringify(data ?? {}, null, compact ? 0 : 2)}</pre>;
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return (
    <label className="search-box">
      <Search size={15} />
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

function PageSizeSelect({ value, onChange, label = 'Rows' }: { value: number; onChange: (value: number) => void; label?: string }) {
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(Number(event.target.value))}>
        {PAGE_SIZES.map((size) => (
          <option key={size} value={size}>
            {size}
          </option>
        ))}
      </select>
    </label>
  );
}

function Pagination({ page, setPage, pagination }: { page: number; setPage: (page: number) => void; pagination?: JsonRecord }) {
  return (
    <div className="pagination">
      <button disabled={page <= 1} onClick={() => setPage(Math.max(1, page - 1))} type="button">
        Previous
      </button>
      <span>
        Page {pagination?.page || page} of {pagination?.total_pages || 1}
      </span>
      <button disabled={pagination && page >= pagination.total_pages} onClick={() => setPage(page + 1)} type="button">
        Next
      </button>
    </div>
  );
}

function Subsection({ title, children }: { title: string; children: any }) {
  return (
    <section className="subsection">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  return (
    <div className={`toast ${toast.kind}`}>
      <div>
        <strong>{toast.title}</strong>
        {toast.detail ? <p>{toast.detail}</p> : null}
      </div>
      <button className="icon-button" onClick={onClose} type="button">
        <X size={16} />
      </button>
    </div>
  );
}

function ErrorPanel({ title, error }: { title: string; error: any }) {
  return (
    <div className="error-panel">
      <AlertTriangle size={17} />
      <div>
        <strong>{title}</strong>
        <p>{error instanceof ApiError ? stringifyDetail(error.detail) : String(error)}</p>
      </div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

function Loading() {
  return (
    <div className="loading">
      <Loader2 size={17} className="spin" />
      Loading
    </div>
  );
}

function summarizePayload(data: any): string {
  if (!data) return '';
  if (data.message) return String(data.message);
  if (data.id) return `ID: ${data.id}`;
  if (data.task?.id) return `Task: ${data.task.id}`;
  if (data.proposal?.id) return `Proposal: ${data.proposal.id}`;
  return stringifyDetail(data).slice(0, 500);
}

function stringifyDetail(detail: any): string {
  if (typeof detail === 'string') return detail;
  try {
    return JSON.stringify(detail, null, 2);
  } catch {
    return String(detail);
  }
}

function navBadge(view: ViewId, counts: JsonRecord) {
  if (view === 'builder') return counts.pending_capability_proposals || counts.active_capability_gaps || '';
  if (view === 'reviews') return counts.pending_reviews || '';
  if (view === 'sources') return counts.open_source_proposals || '';
  if (view === 'runs') return counts.active_runs || '';
  return '';
}

function formatDate(value: any) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}
