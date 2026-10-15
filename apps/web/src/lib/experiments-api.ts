import type { components } from '@jx/contracts';

import { requestJson, requestJsonWithHeaders } from './auth-api';

export type ExperimentAppointment = components['schemas']['ExperimentAppointmentResponse'];
export type ExperimentEnter = components['schemas']['ExperimentEnterResponse'];
export type ParticipantAnnotationTask = components['schemas']['ParticipantAnnotationTaskResponse'];
export type ParticipantAnnotationItem = components['schemas']['ParticipantAnnotationItemResponse'];
export type ExpertAnnotationTask = components['schemas']['ExpertAnnotationTaskResponse'];
export type ExperimentBatch = components['schemas']['ExperimentBatchResponse'];
export type ExperimentSchedule = components['schemas']['ExperimentScheduleResponse'];
export type ExperimentBatchProgress = components['schemas']['ExperimentBatchProgressResponse'];
export type ExperimentJob = components['schemas']['ExperimentJobResponse'];
export type ExperimentRoster = components['schemas']['ExperimentRosterResponse'];
export type ExperimentRosterDetail = components['schemas']['ExperimentRosterDetailResponse'];
export type ExperimentPublish = components['schemas']['ExperimentPublishResponse'];
export type ExperimentAccountGeneration =
  components['schemas']['ExperimentAccountGenerationResponse'];
export type ExperimentBatchDisable = components['schemas']['ExperimentBatchDisableResponse'];
export type ExperimentBatchDelete = components['schemas']['ExperimentBatchDeleteResponse'];
export type ExperimentScheduleCsvImport =
  components['schemas']['ExperimentScheduleCsvImportResponse'];
export type ExperimentPromptTemplates = components['schemas']['ExperimentPromptTemplatesResponse'];

const ANNOTATION_PAGE_SIZE = 25;
const ANNOTATION_MAX_PAGES = 4;
const ANNOTATION_MAX_ITEMS = ANNOTATION_PAGE_SIZE * ANNOTATION_MAX_PAGES;
const ANNOTATION_MAX_BYTES = 64 * 1024 * 1024;
const annotationTaskTooLargeMessage = '实验标注任务超过单次加载上限，请使用分页查看或联系管理员';
const annotationTaskInvalidResponseMessage = '实验标注任务响应格式异常，请刷新后重试';
const EXPERIMENT_INDEX_PAGE_SIZE = 50;
const EXPERIMENT_INDEX_MAX_PAGES = 200;
const EXPERIMENT_INDEX_MAX_ITEMS = 10_000;
const EXPERIMENT_INDEX_MAX_BYTES = 16 * 1024 * 1024;
const experimentHistoryTooLargeMessage = '实验历史超过单次加载上限，请使用分页查看或联系管理员';
const experimentHistoryInvalidResponseMessage = '实验历史响应格式异常，请刷新后重试';
const experimentHistoryEncoder = new TextEncoder();

async function fetchTaskPages<T>(path: string): Promise<T[]> {
  const rows: T[] = [];
  let cursor: string | undefined;
  const seen = new Set<string>();
  let pageCount = 0;
  let payloadBytes = 0;
  do {
    if (pageCount >= ANNOTATION_MAX_PAGES) {
      throw new Error(annotationTaskTooLargeMessage);
    }
    pageCount += 1;
    const query = new URLSearchParams({ limit: String(ANNOTATION_PAGE_SIZE) });
    if (cursor) query.set('cursor', cursor);
    const result = await requestJsonWithHeaders<T[]>(`${path}?${query.toString()}`);
    if (!Array.isArray(result.data)) {
      throw new Error(annotationTaskInvalidResponseMessage);
    }
    const pageBytes = experimentHistoryEncoder.encode(JSON.stringify(result.data)).byteLength;
    if (
      rows.length + result.data.length > ANNOTATION_MAX_ITEMS ||
      payloadBytes + pageBytes > ANNOTATION_MAX_BYTES
    ) {
      throw new Error(annotationTaskTooLargeMessage);
    }
    payloadBytes += pageBytes;
    rows.push(...result.data);
    const next = result.headers.get('X-Next-Cursor') ?? undefined;
    if (next && seen.has(next)) throw new Error('实验分页游标重复');
    if (next) seen.add(next);
    cursor = next;
  } while (cursor);
  return rows;
}

async function fetchAllExperimentPages<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  const seen = new Set<string>();
  let cursor: string | null = null;
  let pageCount = 0;
  let payloadBytes = 0;
  do {
    if (pageCount >= EXPERIMENT_INDEX_MAX_PAGES) {
      throw new Error(experimentHistoryTooLargeMessage);
    }
    const params = new URLSearchParams({ limit: String(EXPERIMENT_INDEX_PAGE_SIZE) });
    if (cursor) params.set('cursor', cursor);
    const result = await requestJsonWithHeaders<T[]>(`${path}?${params.toString()}`);
    if (!Array.isArray(result.data)) {
      throw new Error(experimentHistoryInvalidResponseMessage);
    }
    const pageBytes = experimentHistoryEncoder.encode(JSON.stringify(result.data)).byteLength;
    if (
      items.length + result.data.length > EXPERIMENT_INDEX_MAX_ITEMS ||
      payloadBytes + pageBytes > EXPERIMENT_INDEX_MAX_BYTES
    ) {
      throw new Error(experimentHistoryTooLargeMessage);
    }
    pageCount += 1;
    payloadBytes += pageBytes;
    items.push(...result.data);
    const next = result.headers.get('X-Next-Cursor');
    if (!next) return items;
    if (next === cursor || seen.has(next)) throw new Error('实验分页游标重复');
    seen.add(next);
    cursor = next;
  } while (cursor);
  return items;
}

export const experimentsApi = {
  appointments: () =>
    fetchAllExperimentPages<ExperimentAppointment>('/api/experiments/appointments'),
  enter: (scheduledMatchId: string, termsVersion: string) =>
    requestJson<ExperimentEnter>(`/api/experiments/appointments/${scheduledMatchId}/enter`, {
      method: 'POST',
      body: JSON.stringify({ human_participation_terms_version: termsVersion }),
    }),
  participantTasks: () =>
    fetchTaskPages<ParticipantAnnotationTask>('/api/experiments/annotation-tasks'),
  saveParticipantItem: (
    taskId: string,
    itemId: string,
    payload: components['schemas']['ParticipantAnnotationAnswerSaveRequest'],
  ) =>
    requestJson<ParticipantAnnotationTask>(
      `/api/experiments/annotation-tasks/${taskId}/items/${itemId}`,
      { method: 'PUT', body: JSON.stringify(payload) },
    ),
  saveQuestionnaire: (
    taskId: string,
    payload: components['schemas']['ParticipantQuestionnaireSubmitRequest'],
  ) =>
    requestJson<ParticipantAnnotationTask>(
      `/api/experiments/annotation-tasks/${taskId}/questionnaire`,
      { method: 'PUT', body: JSON.stringify(payload) },
    ),
  submitParticipantTask: (taskId: string) =>
    requestJson<ParticipantAnnotationTask>(`/api/experiments/annotation-tasks/${taskId}/submit`, {
      method: 'POST',
    }),
  expertTasks: () => fetchTaskPages<ExpertAnnotationTask>('/api/experiments/expert-tasks'),
  saveExpertItem: (
    taskId: string,
    opportunityId: string,
    payload: components['schemas']['ExpertAnnotationSaveRequest'],
  ) =>
    requestJson<ExpertAnnotationTask>(
      `/api/experiments/expert-tasks/${taskId}/opportunities/${opportunityId}`,
      { method: 'PUT', body: JSON.stringify(payload) },
    ),
  submitExpertTask: (taskId: string) =>
    requestJson<ExpertAnnotationTask>(`/api/experiments/expert-tasks/${taskId}/submit`, {
      method: 'POST',
    }),
  adminBatches: () => fetchAllExperimentPages<ExperimentBatch>('/api/admin/experiments/batches'),
  generateAccounts: () =>
    requestJson<ExperimentAccountGeneration>('/api/admin/experiments/accounts/generate', {
      method: 'POST',
    }),
  createBatch: (payload: components['schemas']['ExperimentBatchCreateRequest']) =>
    requestJson<ExperimentBatch>('/api/admin/experiments/batches', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateBatch: (batchId: string, payload: components['schemas']['ExperimentBatchUpdateRequest']) =>
    requestJson<ExperimentBatch>(`/api/admin/experiments/batches/${batchId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteBatch: (batchId: string) =>
    requestJson<ExperimentBatchDelete>(`/api/admin/experiments/batches/${batchId}`, {
      method: 'DELETE',
    }),
  replaceRoster: (batchId: string, payload: components['schemas']['ExperimentRosterPutRequest']) =>
    requestJson<ExperimentRoster>(`/api/admin/experiments/batches/${batchId}/roster`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  adminRoster: (batchId: string) =>
    requestJson<ExperimentRosterDetail>(`/api/admin/experiments/batches/${batchId}/roster`),
  generateSchedule: (batchId: string, topicIds: string[], trainingTopicId: string) =>
    requestJson<ExperimentSchedule>(`/api/admin/experiments/batches/${batchId}/schedule/generate`, {
      method: 'POST',
      body: JSON.stringify({ topic_ids: topicIds, training_topic_id: trainingTopicId }),
    }),
  publishBatch: (batchId: string) =>
    requestJson<ExperimentPublish>(`/api/admin/experiments/batches/${batchId}/publish`, {
      method: 'POST',
    }),
  adminSchedule: (batchId: string) =>
    requestJson<ExperimentSchedule>(`/api/admin/experiments/batches/${batchId}/schedule`),
  promptTemplates: () =>
    requestJson<ExperimentPromptTemplates>('/api/admin/experiments/prompt-templates'),
  disableBatch: (batchId: string) =>
    requestJson<ExperimentBatchDisable>(`/api/admin/experiments/batches/${batchId}/disable`, {
      method: 'POST',
    }),
  importSchedule: (batchId: string, csvText: string) =>
    requestJson<ExperimentScheduleCsvImport>(
      `/api/admin/experiments/batches/${batchId}/schedule/import`,
      { method: 'POST', body: JSON.stringify({ csv_text: csvText }) },
    ),
  adminProgress: (batchId: string) =>
    requestJson<ExperimentBatchProgress>(`/api/admin/experiments/batches/${batchId}/progress`),
  publishResult: (attemptId: string, reason: string) =>
    requestJson<components['schemas']['ExperimentResultVisibilityResponse']>(
      `/api/admin/experiments/attempts/${attemptId}/publish-result`,
      { method: 'POST', body: JSON.stringify({ reason }) },
    ),
  createExport: (batchId: string) =>
    requestJson<ExperimentJob>(`/api/admin/experiments/batches/${batchId}/exports`, {
      method: 'POST',
    }),
  retentionDryRun: (batchId: string) =>
    requestJson<ExperimentJob>(`/api/admin/experiments/batches/${batchId}/retention`, {
      method: 'POST',
      body: JSON.stringify({ dry_run: true }),
    }),
  job: (taskId: string) => requestJson<ExperimentJob>(`/api/admin/experiments/jobs/${taskId}`),
};

export type AiExperienceSurvey = {
  id: string;
  version: number;
  title: string;
  description: string;
  questions: Array<{
    key: string;
    type: 'scale' | 'textarea';
    required: boolean;
    text: string;
    scale_max?: number;
    options?: Array<{ value: number; text: string }>;
  }>;
  status: 'DRAFT' | 'SUBMITTED';
  answers: Record<string, number | string>;
  updated_at: string;
  submitted_at: string | null;
};

export type PostmatchSurveyItem = {
  speech_id: string;
  subject_kind: string;
  annotatable?: boolean;
  review_state?: 'CONTEXT' | 'CURRENT_PRE' | 'CURRENT_POST' | 'FUTURE' | 'COMPLETE';
  text: string | null;
  side?: string;
  seat_no?: number;
  sequence?: number;
  action_key?: string;
  stage_position?: number | null;
  stage_name?: string;
  stage_kind?: string;
  speaker_name?: string;
  started_at?: string | null;
  ended_at?: string | null;
  finalized_at?: string | null;
  duration_ms?: number | null;
};

export type PostmatchSurveyTask = {
  id: string;
  match_id: string;
  questionnaire_version: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'SUBMITTED';
  answers: Record<string, unknown>;
  updated_at: string;
  submitted_at: string | null;
  match_status?: string | null;
  match_ended_at?: string | null;
  room_title?: string | null;
  room_label?: string | null;
  topic?: string | null;
  target_total?: number;
  confirmed_total?: number;
  workflow_revision?: number;
  items?: PostmatchSurveyItem[];
  questionnaire: {
    human_self: Record<'q1' | 'q2', SurveyChoiceQuestion>;
    team_ai: Record<'q1' | 'q2', SurveyChoiceQuestion>;
    overall: SurveyScaleQuestion[];
  } | null;
};

export type PostmatchSurveyPage = {
  items: PostmatchSurveyTask[];
  nextCursor: string | null;
};

export type SurveyChoiceQuestion = {
  type: 'single_choice' | 'multiple_choice';
  text: string;
  options: string[];
  max_selections?: number;
  descriptions?: Record<string, string>;
};

export type SurveyScaleQuestion = {
  key: string;
  type: 'scale';
  text: string;
  options: Array<{ value: number; text: string }>;
};

export const surveyApi = {
  aiExperience: () => requestJson<AiExperienceSurvey>('/api/me/ai-experience'),
  saveAiExperience: (answers: Record<string, number | string>, submit = false) =>
    requestJson<AiExperienceSurvey>('/api/me/ai-experience', {
      method: 'PUT',
      body: JSON.stringify({ answers, submit }),
    }),
  postmatch: () => requestJson<PostmatchSurveyTask[]>('/api/me/postmatch-surveys'),
  postmatchPage: async (cursor: string | null = null): Promise<PostmatchSurveyPage> => {
    const params = new URLSearchParams({ limit: '25' });
    if (cursor) params.set('cursor', cursor);
    const result = await requestJsonWithHeaders<PostmatchSurveyTask[]>(
      `/api/me/postmatch-surveys?${params.toString()}`,
    );
    return {
      items: result.data,
      nextCursor: result.headers.get('X-Next-Cursor'),
    };
  },
  postmatchTask: (taskId: string) =>
    requestJson<PostmatchSurveyTask>(`/api/me/postmatch-surveys/${taskId}`),
  postmatchStatus: (matchId: string) =>
    requestJson<{
      match_id: string;
      exists: boolean;
      task_id: string | null;
      status: PostmatchSurveyTask['status'] | null;
      pending: boolean;
      href: string;
    }>(`/api/me/postmatch-surveys/matches/${matchId}/status`),
  savePostmatch: (
    taskId: string,
    answers: Record<string, unknown>,
    submit = false,
    stage?: 'overall' | 'speeches',
  ) =>
    requestJson<PostmatchSurveyTask>(`/api/me/postmatch-surveys/${taskId}`, {
      method: 'PUT',
      body: JSON.stringify({ answers, submit, ...(stage ? { stage } : {}) }),
    }),
  savePostmatchSpeech: (
    taskId: string,
    speechId: string,
    answer: Record<string, unknown>,
    confirm = false,
    expectedRevision = 0,
  ) =>
    requestJson<PostmatchSurveyTask>(`/api/me/postmatch-surveys/${taskId}`, {
      method: 'PUT',
      body: JSON.stringify({
        speech_id: speechId,
        answer,
        confirm,
        expected_revision: expectedRevision,
      }),
    }),
  submitPostmatch: (taskId: string) =>
    requestJson<PostmatchSurveyTask>(`/api/me/postmatch-surveys/${taskId}`, {
      method: 'PUT',
      body: JSON.stringify({ submit: true }),
    }),
};
