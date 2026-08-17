import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, userEvent, within } from 'storybook/test';
import { http, HttpResponse } from 'msw';

import AdminUsersPage from '@/app/admin/users/page';
import AdminMatchesPage from '@/app/admin/matches/page';
import AdminLogsPage from '@/app/admin/logs/page';
import AdminModelsPage from '@/app/admin/models/page';
import AdminVoicesPage from '@/app/admin/voices/page';
import AdminTopicsPage from '@/app/admin/topics/page';

const model = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Qwen 3.7 Plus',
  config_ref: 'qwen-main',
  model_id: 'qwen3.7-plus',
  base_url: 'https://example.test/v1',
  api_key_last4: '1234',
  max_concurrency: 50,
  token_per_char: 1,
  generation_params: {},
  status: 'ENABLED',
};
const voice = {
  id: '22222222-2222-4222-8222-222222222222',
  name: '龙安灵希',
  kind: 'AGENT',
  provider_voice: 'voice-a',
  rate: 1,
  chars_per_second: 4.5,
  status: 'ENABLED',
};
const topic = {
  id: '33333333-3333-4333-8333-333333333333',
  topic_key: 'topic-a',
  version: 1,
  title: 'AI 是否提升创作者意义',
  affirmative_text: '提升',
  negative_text: '降低',
  status: 'ENABLED',
};
const catalog = { models: [model], voices: [voice], agents: [], topics: [topic], rules: [] };
const user = {
  id: '44444444-4444-4444-8444-444444444444',
  username: 'debater',
  real_name: '测试辩手',
  role: 'USER',
  status: 'ACTIVE',
  match_count: 2,
  finished_count: 1,
  wins: 1,
  points: 18,
  average_personal_score: 18,
};
const match = {
  id: '55555555-5555-4555-8555-555555555555',
  room_id: '66666666-6666-4666-8666-666666666666',
  status: 'FINISHED',
  created_at: '2026-08-11T04:00:00Z',
  ended_at: '2026-08-11T05:00:00Z',
  archived_at: null,
  context_version: 12,
  file_count: 2,
  files_permanent: false,
  label: '4v4 正式辩论赛',
  display_topic: topic.title,
  admin_note: '',
};
const log = {
  id: '77777777-7777-4777-8777-777777777777',
  action: 'admin.agent.updated',
  target_type: 'agent_profile',
  target_id: model.id,
  result: 'SUCCESS',
  details: {},
  created_at: '2026-08-11T05:00:00Z',
};
const page = <T,>(items: T[]) => ({
  items,
  page: 1,
  page_size: 25,
  total: items.length,
  total_pages: items.length ? 1 : 0,
});

const handlers = [
  http.get('*/api/admin/catalog', () => HttpResponse.json(catalog)),
  http.get('*/api/admin/users', () => HttpResponse.json(page([user]))),
  http.get('*/api/admin/matches', () => HttpResponse.json(page([match]))),
  http.get('*/api/admin/matches/:matchId/agent-generations', () =>
    HttpResponse.json([
      {
        id: '88888888-8888-4888-8888-888888888888',
        action_key: '1:1',
        agent_profile_id: '99999999-9999-4999-8999-999999999999',
        agent_name: '乾元',
        context_version: 4,
        attempt_no: 1,
        status: 'FINALIZED',
        first_token_latency_ms: 420,
        completed_latency_ms: 1300,
        completion_tokens: 128,
        error_code: null,
        created_at: '2026-08-14T05:00:00Z',
        completed_at: '2026-08-14T05:00:02Z',
      },
    ]),
  ),
  http.get('*/api/admin/matches/:matchId/free-debate-decisions', () =>
    HttpResponse.json([
      {
        id: '81818181-8181-4818-8818-818181818181',
        action_key: '7:0',
        decision_round_id: '82828282-8282-4828-8828-828282828282',
        agent_profile_id: '99999999-9999-4999-8999-999999999999',
        agent_name: '乾元',
        side: 'AFFIRMATIVE',
        seat_no: 2,
        status: 'HAND',
        should_speak: true,
        willingness: 0.86,
        attempt_no: 1,
        duration_ms: 640,
        error_code: null,
        result_order: 1,
        final_queue_rank: 2,
        human_hand_at_result: true,
        human_hand_at_lock: true,
        selected: false,
        fallback: false,
        started_at: '2026-08-14T05:10:00Z',
        completed_at: '2026-08-14T05:10:00.640Z',
      },
    ]),
  ),
  http.get('*/api/admin/matches/:matchId/agent-generations/:generationId', () =>
    HttpResponse.json({
      id: '88888888-8888-4888-8888-888888888888',
      action_key: '1:1',
      agent_profile_id: '99999999-9999-4999-8999-999999999999',
      agent_name: '乾元',
      context_version: 4,
      attempt_no: 1,
      status: 'FINALIZED',
      input_snapshot: { current_stage: '正方一辩立论', debate_history: [] },
      llm_draft_text: '这是脱敏的模型正式草稿。',
      first_token_latency_ms: 420,
      completed_latency_ms: 1300,
      completion_tokens: 128,
      error_code: null,
      created_at: '2026-08-14T05:00:00Z',
      completed_at: '2026-08-14T05:00:02Z',
    }),
  ),
  http.get('*/api/admin/logs', () => HttpResponse.json(page([log]))),
  http.get('*/api/admin/storage', () =>
    HttpResponse.json({
      total_bytes: 100,
      used_bytes: 45,
      free_bytes: 55,
      used_ratio: 0.45,
      estimated_days_remaining: 30,
      automatic_backup: false,
    }),
  ),
  http.patch('*/api/admin/catalog/:kind/:id/status', () =>
    HttpResponse.json({ status: 'DISABLED' }),
  ),
  http.post('*/api/admin/voices/:id/preview', () => HttpResponse.json({ status: 'queued' })),
  http.get(
    '*/api/admin/voices/:id/preview',
    () => new HttpResponse(new Uint8Array(), { headers: { 'Content-Type': 'audio/ogg' } }),
  ),
];

const meta = {
  title: 'Admin/016b Migration',
  parameters: { layout: 'fullscreen', msw: { handlers } },
} satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

export const Users: Story = {
  render: () => <AdminUsersPage />,
  async play({ canvasElement }) {
    await expect(
      within(canvasElement).findByRole('heading', { name: '用户管理' }),
    ).resolves.toBeVisible();
    await expect(within(canvasElement).findByText('测试辩手')).resolves.toBeVisible();
  },
};
export const Matches: Story = {
  render: () => <AdminMatchesPage />,
  async play({ canvasElement }) {
    await expect(
      within(canvasElement).findByRole('heading', { name: '比赛与数据' }),
    ).resolves.toBeVisible();
    await expect(within(canvasElement).findByText('4v4 正式辩论赛')).resolves.toBeVisible();
    await userEvent.click(within(canvasElement).getByRole('button', { name: '更多操作' }));
    await userEvent.click(await within(document.body).findByText('模型诊断'));
    await expect(await within(document.body).findByText('自由辩论快速决策')).toBeVisible();
    await userEvent.click(await within(document.body).findByText('查看输入与草稿'));
    await userEvent.click(await within(document.body).findByText('LLM 正式草稿'));
    await expect(await within(document.body).findByText('这是脱敏的模型正式草稿。')).toBeVisible();
  },
};
export const Logs: Story = {
  render: () => <AdminLogsPage />,
  async play({ canvasElement }) {
    await expect(
      within(canvasElement).findByRole('heading', { name: '日志管理' }),
    ).resolves.toBeVisible();
    await expect(within(canvasElement).findByText('admin.agent.updated')).resolves.toBeVisible();
  },
};
export const Models: Story = {
  render: () => <AdminModelsPage />,
  async play({ canvasElement }) {
    await expect(
      within(canvasElement).findByRole('heading', { name: '模型设置' }),
    ).resolves.toBeVisible();
    await expect(within(canvasElement).findByText('Qwen 3.7 Plus')).resolves.toBeVisible();
  },
};
export const Voices: Story = {
  render: () => <AdminVoicesPage />,
  async play({ canvasElement }) {
    await expect(
      within(canvasElement).findByRole('heading', { name: '语音方案' }),
    ).resolves.toBeVisible();
    await expect(within(canvasElement).findByText('龙安灵希')).resolves.toBeVisible();
  },
};
export const Topics: Story = {
  render: () => <AdminTopicsPage />,
  async play({ canvasElement }) {
    await expect(
      within(canvasElement).findByRole('heading', { name: '辩题管理' }),
    ).resolves.toBeVisible();
    await expect(within(canvasElement).findByText(topic.title)).resolves.toBeVisible();
  },
};
