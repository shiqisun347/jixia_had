import { requestJson } from '@/lib/auth-api';

import type {
  AdminListQuery,
  AgentGenerationDiagnostic,
  AgentGenerationDiagnosticDetail,
  AgentFreeDebateDecisionDiagnostic,
  ExternalCallDetail,
  ExternalCallRow,
  AdminOverview,
  AdminPage,
  Catalog,
  AgentRow,
  JudgeProfile,
  LogRow,
  RuntimeLogRow,
  MatchRow,
  StorageStatus,
  UserRow,
  MatchWorkbenchOverview,
  MatchExportStatus,
  WorkbenchPage,
  DiagnosticEventRow,
  DiagnosticTaskRow,
  IncidentRow,
  WorkbenchTimelineItem,
} from './admin-types';

function listPath(path: string, query: AdminListQuery = {}) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value));
  });
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export const adminApi = {
  overview: () => requestJson<AdminOverview>('/api/admin/overview'),
  users: (query?: AdminListQuery) =>
    requestJson<AdminPage<UserRow>>(listPath('/api/admin/users', query)),
  matches: (query?: AdminListQuery) =>
    requestJson<AdminPage<MatchRow>>(listPath('/api/admin/matches', query)),
  matchIds: (query?: AdminListQuery) =>
    requestJson<{ ids: string[]; total: number; truncated: boolean }>(
      listPath('/api/admin/matches/ids', query),
    ),
  matchGenerations: (matchId: string) =>
    requestJson<AgentGenerationDiagnostic[]>(`/api/admin/matches/${matchId}/agent-generations`),
  matchGeneration: (matchId: string, generationId: string) =>
    requestJson<AgentGenerationDiagnosticDetail>(
      `/api/admin/matches/${matchId}/agent-generations/${generationId}`,
    ),
  matchFreeDebateDecisions: (matchId: string) =>
    requestJson<AgentFreeDebateDecisionDiagnostic[]>(
      `/api/admin/matches/${matchId}/free-debate-decisions`,
    ),
  logs: (query?: AdminListQuery) =>
    requestJson<AdminPage<LogRow>>(listPath('/api/admin/logs', query)),
  auditLog: (logId: string) => requestJson<LogRow>(`/api/admin/audit-logs/${logId}`),
  runtimeLogs: (query?: AdminListQuery) =>
    requestJson<AdminPage<RuntimeLogRow>>(listPath('/api/admin/runtime-logs', query)),
  runtimeLogStats: () =>
    requestJson<{ queue_size: number; queue_capacity: number; dropped_count: number }>(
      '/api/admin/runtime-logs/stats',
    ),
  diagnosticEvents: () =>
    requestJson<{ items: DiagnosticEventRow[] }>('/api/admin/diagnostics/events?page_size=50'),
  diagnosticTasks: (query?: AdminListQuery) =>
    requestJson<{ items: DiagnosticTaskRow[] }>(
      listPath('/api/admin/diagnostics/tasks', { page_size: 50, ...query }),
    ),
  retryDiagnosticTask: (taskId: string) =>
    requestJson<{ id: string; status: string }>(`/api/admin/diagnostics/tasks/${taskId}/retry`, {
      method: 'POST',
      body: '{}',
    }),
  incidents: (status = '') =>
    requestJson<{ items: IncidentRow[] }>(listPath('/api/admin/incidents', { status })),
  incident: (incidentId: string) =>
    requestJson<{ incident: IncidentRow; events: DiagnosticEventRow[] }>(
      `/api/admin/incidents/${incidentId}`,
    ),
  updateIncident: (incidentId: string, status: string, notes: string) =>
    requestJson<{ id: string; status: string; notes: string | null }>(
      `/api/admin/incidents/${incidentId}`,
      { method: 'PATCH', body: JSON.stringify({ status, notes }) },
    ),
  catalog: () => requestJson<Catalog>('/api/admin/catalog'),
  ruleAgents: (ruleId: string) => requestJson<AgentRow[]>(`/api/admin/rules/${ruleId}/agents`),
  judge: () => requestJson<JudgeProfile>('/api/admin/judge-profile'),
  storage: () => requestJson<StorageStatus>('/api/admin/storage'),
  matchWorkbenchOverview: (matchId: string) =>
    requestJson<MatchWorkbenchOverview>(`/api/admin/matches/${matchId}/workbench/overview`),
  controlMatch: (matchId: string, action: 'terminate' | 'resume' | 'recover' | 'reset_speech') =>
    requestJson<{ status: string; action_state: string; sequence: number }>(
      `/api/admin/matches/${matchId}/control`,
      { method: 'POST', body: JSON.stringify({ action }) },
    ),
  matchWorkbenchParticipants: (matchId: string) =>
    requestJson<Record<string, unknown>[]>(`/api/admin/matches/${matchId}/workbench/participants`),
  matchWorkbenchTranscript: (matchId: string, page = 1) =>
    requestJson<WorkbenchPage<Record<string, unknown>>>(
      `/api/admin/matches/${matchId}/workbench/transcript?page=${page}&page_size=25`,
    ),
  matchWorkbenchEvents: (matchId: string, page = 1) =>
    requestJson<WorkbenchPage<Record<string, unknown>>>(
      `/api/admin/matches/${matchId}/workbench/events?page=${page}&page_size=50`,
    ),
  matchWorkbenchCalls: (matchId: string, page = 1) =>
    requestJson<WorkbenchPage<ExternalCallRow>>(
      `/api/admin/matches/${matchId}/workbench/calls?page=${page}&page_size=50`,
    ),
  externalCall: (callId: string) =>
    requestJson<ExternalCallDetail>(`/api/admin/external-calls/${callId}`),
  externalCalls: (query?: AdminListQuery) =>
    requestJson<AdminPage<ExternalCallRow>>(listPath('/api/admin/external-calls', query)),
  matchWorkbenchTimeline: (matchId: string, page = 1) =>
    requestJson<WorkbenchPage<WorkbenchTimelineItem>>(
      `/api/admin/matches/${matchId}/workbench/timeline?page=${page}&page_size=50`,
    ),
  preflightExport: (matchIds: string[], includeAudio: boolean) =>
    requestJson<Record<string, unknown>>('/api/admin/exports/preflight', {
      method: 'POST',
      body: JSON.stringify({ match_ids: matchIds, include_audio: includeAudio }),
    }),
  createExport: (matchIds: string[], includeAudio: boolean) =>
    requestJson<MatchExportStatus>('/api/admin/exports', {
      method: 'POST',
      body: JSON.stringify({ match_ids: matchIds, include_audio: includeAudio }),
    }),
  exportStatus: (exportId: string) =>
    requestJson<MatchExportStatus>(`/api/admin/exports/${exportId}`),
  bulkPreflight: (resource: string, operation: string, targetIds: string[]) =>
    requestJson<Record<string, unknown>>('/api/admin/bulk/preflight', {
      method: 'POST',
      body: JSON.stringify({ resource, operation, target_ids: targetIds }),
    }),
  bulk: (resource: string, operation: string, targetIds: string[]) =>
    requestJson<Record<string, unknown>>('/api/admin/bulk', {
      method: 'POST',
      body: JSON.stringify({ resource, operation, target_ids: targetIds }),
    }),
};

export function readableAdminError(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}
