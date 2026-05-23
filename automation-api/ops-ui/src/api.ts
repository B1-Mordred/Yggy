import type { ActionHeaderKind } from './types';

const ACTION_HEADER_VALUES: Record<ActionHeaderKind, string> = {
  approval: 'approval-decision',
  run: 'manual-run',
  taskState: 'task-state',
  taskArchive: 'task-archive',
  versionRevert: 'version-revert',
  taskChange: 'task-change-proposal',
  capabilityProposal: 'capability-proposal',
  capabilityImplementation: 'capability-implementation',
  capabilityGap: 'capability-gap',
  sourceProposal: 'source-proposal'
};

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export function queryString(params: Record<string, unknown>): string {
  const entries = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => [key, String(value)]);
  if (!entries.length) return '';
  return `?${new URLSearchParams(entries).toString()}`;
}

export async function fetchJson<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'same-origin'
  });
  if (response.status === 401) {
    window.location.assign(`/ops/login?next=${encodeURIComponent(window.location.pathname)}`);
    throw new ApiError(response.status, 'Authentication required');
  }
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload && 'detail' in payload ? (payload as any).detail : payload;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export function postJson<T = any>(
  path: string,
  body: unknown,
  actionHeader?: ActionHeaderKind,
  method: 'POST' | 'PUT' = 'POST'
): Promise<T> {
  const headers = new Headers();
  headers.set('Content-Type', 'application/json');
  if (actionHeader) {
    headers.set('X-Yggy-Ops-Action', ACTION_HEADER_VALUES[actionHeader]);
  }
  return fetchJson<T>(path, {
    method,
    headers,
    body: JSON.stringify(body ?? {})
  });
}

export function actionHeaderValue(kind: ActionHeaderKind): string {
  return ACTION_HEADER_VALUES[kind];
}
