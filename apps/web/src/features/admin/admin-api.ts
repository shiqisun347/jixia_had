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
  JudgeProfile,
  LogRow,
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
  diagnosticEvents: () =>
    requestJson<{ items: DiagnosticEventRow[] }>('/api/admin/diagnostics/events?page_size=50'),
  diagnosticTasks: () =>
    requestJson<{ items: DiagnosticTaskRow[] }>('/api/admin/diagnostics/tasks?page_size=50'),
  incidents: (status = '') =>
    requestJson<{ items: IncidentRow[] }>(listPath('/api/admin/incidents', { status })),
  catalog: () => requestJson<Catalog>('/api/admin/catalog'),
  judge: () => requestJson<JudgeProfile>('/api/admin/judge-profile'),
  storage: () => requestJson<StorageStatus>('/api/admin/storage'),
  matchWorkbenchOverview: (matchId: string) =>
    requestJson<MatchWorkbenchOverview>(`/api/admin/matches/${matchId}/workbench/overview`),
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
