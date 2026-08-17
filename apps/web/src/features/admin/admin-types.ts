export type UserRow = {
  id: string;
  username: string;
  real_name: string;
  role: string;
  status: string;
  match_count: number;
  finished_count: number;
  wins: number;
  points: number;
  average_personal_score: number;
};

export type MatchRow = {
  id: string;
  room_id: string;
  status: string;
  created_at: string;
  ended_at: string | null;
  context_version: number;
  file_count: number;
  files_permanent: boolean;
  label: string;
  display_topic: string;
  admin_note: string;
};

export type AgentGenerationDiagnostic = {
  id: string;
  action_key: string;
  agent_profile_id: string;
  agent_name: string;
  context_version: number;
  attempt_no: number;
  status: string;
  first_token_latency_ms: number | null;
  completed_latency_ms: number | null;
  completion_tokens: number | null;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
};

export type AgentGenerationDiagnosticDetail = AgentGenerationDiagnostic & {
  input_snapshot: Record<string, unknown>;
  llm_draft_text: string;
};

export type AgentFreeDebateDecisionDiagnostic = {
  id: string;
  action_key: string;
  decision_round_id: string;
  agent_profile_id: string;
  agent_name: string;
  side: string;
  seat_no: number;
  status: string;
  should_speak: boolean | null;
  willingness: number | null;
  attempt_no: number;
  duration_ms: number | null;
  error_code: string | null;
  result_order: number | null;
  final_queue_rank: number | null;
  human_hand_at_result: boolean;
  human_hand_at_lock: boolean;
  selected: boolean;
  fallback: boolean;
  started_at: string;
  completed_at: string | null;
};

export type ExternalCallRow = {
  id: string;
  kind: string;
  kind_label: string;
  provider: string;
  operation: string;
  model: string | null;
  voice: string | null;
  attempt_no: number;
  status: string;
  status_label: string;
  speech_id: string | null;
  generation_id: string | null;
  decision_round_id: string | null;
  context_version: number | null;
  started_at: string;
  first_result_latency_ms: number | null;
  completed_latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  audio_bytes: number | null;
  audio_duration_ms: number | null;
  error_code: string | null;
  has_request: boolean;
  has_response: boolean;
  explanation: { what: string; why: string; impact: string };
};

export type ExternalCallDetail = ExternalCallRow & {
  request: unknown;
  response: unknown;
  content_errors: string[];
  technical: Record<string, unknown>;
};

export type WorkbenchTimelineItem = {
  id: string;
  type: string;
  type_label: string;
  at: string;
  sequence: number | null;
  title: string;
  description: string;
  status: string;
  related_id: string;
};

export type LogRow = {
  id: string;
  action: string;
  target_type: string;
  target_id: string | null;
  result: string;
  details?: Record<string, unknown>;
  created_at: string;
};

export type DiagnosticEventRow = {
  id: string;
  level: string;
  service: string;
  logger_name: string;
  message: string;
  error_code: string | null;
  match_id: string | null;
  happened_at: string;
  details: Record<string, unknown>;
};
export type DiagnosticTaskRow = {
  id: string;
  task_type: string;
  status: string;
  attempts: number;
  error_code: string | null;
  available_at: string;
  updated_at: string;
};
export type IncidentRow = {
  id: string;
  fingerprint: string;
  title: string;
  severity: string;
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  occurrence_count: number;
  affected_match_count: number;
  affected_user_count: number;
  notes: string | null;
};

export type ModelRow = {
  id: string;
  name: string;
  config_ref?: string;
  model_id: string | null;
  base_url?: string;
  max_concurrency?: number;
  api_key_last4?: string | null;
  token_per_char?: number;
  generation_params?: Record<string, unknown>;
  status: string;
};

export type VoiceRow = {
  id: string;
  name: string;
  kind: string;
  provider_voice: string;
  rate: number;
  chars_per_second: number | null;
  playback_gain?: number;
  avatar_key?: string | null;
  status: string;
};

export type AgentRow = {
  id: string;
  name: string;
  model_profile_id: string;
  voice_profile_id: string;
  system_prompt?: string;
  debater_prompt?: string;
  generation_params?: Record<string, unknown>;
  avatar_key?: string;
  status: string;
};

export type TopicRow = {
  id: string;
  topic_key?: string;
  version?: number;
  title: string;
  affirmative_text: string;
  negative_text: string;
  status: string;
};

export type RuleRow = {
  id: string;
  name: string;
  description: string;
  side_size: number;
  estimated_seconds: number;
  status: string;
  audio_reviewed_at: string | null;
};

export type Catalog = {
  models: ModelRow[];
  agents: AgentRow[];
  voices: VoiceRow[];
  topics: TopicRow[];
  rules: RuleRow[];
};

export type JudgeProfile = {
  id: string;
  model_profile_id: string;
  system_prompt: string;
  judge_prompt: string;
  generation_params: Record<string, unknown>;
  status: string;
} | null;

export type StorageStatus = {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_ratio: number;
  estimated_days_remaining: number | null;
  automatic_backup: boolean;
};

export type AdminPage<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type AdminOverview = {
  active_matches: number;
  capacity: number;
  enabled_agents: number;
  enabled_models: number;
  enabled_voices: number;
  storage: StorageStatus;
  recent_failures: LogRow[];
  recent_matches: MatchRow[];
};

export type AdminListQuery = {
  page?: number;
  page_size?: 10 | 25 | 50 | 100;
  q?: string;
  status?: string;
  sort?: string;
  order?: 'asc' | 'desc';
};

export type MatchWorkbenchOverview = {
  match: {
    id: string;
    room_id: string;
    status: string;
    sequence: number;
    context_version: number;
    created_at: string;
    ended_at: string | null;
    label: string;
    topic: Record<string, unknown>;
    admin_note: string;
  };
  counts: Record<string, number>;
};

export type WorkbenchPage<T> = AdminPage<T>;
export type MatchExportStatus = {
  id: string;
  status: string;
  total_items: number;
  processed_items: number;
  byte_count: number;
  sha256: string | null;
  error_code: string | null;
  expires_at: string | null;
  created_at: string;
  completed_at: string | null;
};
