import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, userEvent, within } from 'storybook/test';
import { delay, http, HttpResponse } from 'msw';

import AdminPage from '@/app/admin/page';
import AdminAgentsPage from '@/app/admin/agents/page';
import { AdminShell } from '@/features/admin/admin-shell';

const administrator = {
  avatar_version: 0,
  default_avatar_key: 'human-01',
  has_custom_avatar: false,
  id: '00000000-0000-4000-8000-000000000001',
  must_change_password: false,
  real_name: '系统管理员',
  role: 'ADMIN',
  username: 'admin',
};

const model = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Qwen 3.7 Plus',
  model_id: 'qwen3.7-plus',
  status: 'ENABLED',
};
const voices = ['龙安灵希', '龙弦星岚', '龙昕蕊璇'].map((name, index) => ({
  id: `22222222-2222-4222-8222-22222222222${index}`,
  name,
  kind: 'AGENT',
  provider_voice: `voice-${index}`,
  rate: 1,
  chars_per_second: 4.2,
  avatar_key: `agent-0${index + 1}`,
  status: 'ENABLED',
}));
const agents = ['乾元', '坤元', '明辨', '慎思', '博闻', '笃行'].map((name, index) => ({
  id: `33333333-3333-4333-8333-33333333333${index}`,
  name,
  model_profile_id: model.id,
  voice_profile_id: voices[index % voices.length].id,
  system_prompt: '你是稷下辩手。',
  debater_prompt: `${name} 的辩论风格。`,
  generation_params: { temperature: 0.7 + index / 10 },
  status: index === 5 ? 'DISABLED' : 'ENABLED',
}));

const catalog = { models: [model], voices, agents, topics: [], rules: [] };
const matches = [
  {
    id: '44444444-4444-4444-8444-444444444444',
    room_id: '55555555-5555-4555-8555-555555555555',
    status: 'RUNNING',
    created_at: '2026-08-11T04:00:00Z',
    ended_at: null,
    context_version: 12,
    file_count: 0,
    files_permanent: false,
    label: '4v4 正式辩论赛',
    display_topic: 'AI 是否提升了创作者存在的意义',
    admin_note: '',
  },
  {
    id: '44444444-4444-4444-8444-444444444445',
    room_id: '55555555-5555-4555-8555-555555555556',
    status: 'FINISHED',
    created_at: '2026-08-10T04:00:00Z',
    ended_at: '2026-08-10T05:00:00Z',
    context_version: 20,
    file_count: 2,
    files_permanent: false,
    label: '训练赛 · 春季场',
    display_topic: '开放式 AI 是否应该拥有署名权',
    admin_note: '',
  },
];
const logs = [
  {
    id: 'log-1',
    action: 'admin.agent.updated',
    target_type: 'agent_profile',
    target_id: agents[0].id,
    result: 'SUCCESS',
    created_at: '2026-08-11T05:00:00Z',
  },
  {
    id: 'log-2',
    action: 'admin.tts.preview_failed',
    target_type: 'voice_profile',
    target_id: voices[0].id,
    result: 'FAILED',
    created_at: '2026-08-11T04:50:00Z',
  },
];

function adminHandlers({ error = false, delayMs = 0, duplicate = false, locked = false } = {}) {
  const response = (body: unknown) =>
    delayMs
      ? delay(delayMs).then(() => HttpResponse.json(body as never))
      : HttpResponse.json(body as never);
  return [
    http.get('*/api/admin/overview', () =>
      error
        ? HttpResponse.json({ error: { message: '总览暂时不可用' } }, { status: 503 })
        : response({
            active_matches: matches.filter((item) => ['RUNNING', 'PAUSED'].includes(item.status))
              .length,
            capacity: 5,
            enabled_agents: agents.filter((item) => item.status === 'ENABLED').length,
            enabled_models: model.status === 'ENABLED' ? 1 : 0,
            enabled_voices: voices.filter((item) => item.status === 'ENABLED').length,
            storage: {
              total_bytes: 100,
              used_bytes: 61,
              free_bytes: 39,
              used_ratio: 0.61,
              estimated_days_remaining: 24,
              automatic_backup: false,
            },
            recent_failures: logs.filter((item) => item.result !== 'SUCCESS'),
            recent_matches: matches,
          }),
    ),
    http.get('*/api/admin/catalog', () =>
      error
        ? HttpResponse.json({ error: { message: '目录暂时不可用' } }, { status: 503 })
        : response(catalog),
    ),
    http.get('*/api/admin/users', () => response([])),
    http.get('*/api/admin/matches', () => response(matches)),
    http.get('*/api/admin/logs', () => response(logs)),
    http.get('*/api/admin/storage', () =>
      response({
        total_bytes: 100,
        used_bytes: 61,
        free_bytes: 39,
        used_ratio: 0.61,
        estimated_days_remaining: 24,
        automatic_backup: false,
      }),
    ),
    http.post('*/api/admin/catalog/agents', () =>
      duplicate
        ? HttpResponse.json(
            { error: { message: 'Agent 名称已存在，请换一个名称' } },
            { status: 409 },
          )
        : response(agents[0]),
    ),
    http.patch('*/api/admin/catalog/agents/:agentId', () =>
      locked
        ? HttpResponse.json(
            { error: { message: '该 Agent 正被进行中的比赛使用，暂时不能修改' } },
            { status: 409 },
          )
        : response(agents[0]),
    ),
    http.patch('*/api/admin/catalog/agents/:agentId/status', () =>
      response({ status: 'DISABLED' }),
    ),
  ];
}

const meta = {
  title: 'Admin/016a Pilot',
  decorators: [
    (Story) => (
      <AdminShell user={administrator}>
        <Story />
      </AdminShell>
    ),
  ],
  parameters: {
    layout: 'fullscreen',
    nextjs: { appDirectory: true, navigation: { pathname: '/admin' } },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const Overview: Story = {
  render: () => <AdminPage />,
  parameters: { msw: { handlers: adminHandlers() } },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: '运行总览' })).toBeVisible();
    await expect(await canvas.findByText('实时比赛容量')).toBeVisible();
    await expect(await canvas.findByText('待处理事项')).toBeVisible();
  },
};

export const OverviewStorageWarning: Story = {
  render: () => <AdminPage />,
  parameters: {
    msw: {
      handlers: [
        http.get('*/api/admin/overview', () =>
          HttpResponse.json({
            active_matches: 0,
            capacity: 5,
            enabled_agents: 5,
            enabled_models: 1,
            enabled_voices: 3,
            storage: {
              total_bytes: 100,
              used_bytes: 91,
              free_bytes: 9,
              used_ratio: 0.91,
              estimated_days_remaining: 2,
              automatic_backup: false,
            },
            recent_failures: [],
            recent_matches: [],
          }),
        ),
        ...adminHandlers(),
      ],
    },
  },
  async play({ canvasElement }) {
    await expect(await within(canvasElement).findByText(/磁盘使用率已达到 91/)).toBeVisible();
  },
};

export const OverviewError: Story = {
  render: () => <AdminPage />,
  parameters: { msw: { handlers: adminHandlers({ error: true }) } },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('总览暂时无法加载')).toBeVisible();
    await expect(await canvas.findByRole('button', { name: /重新加载/ })).toBeVisible();
  },
};

export const AgentDirectory: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: { handlers: adminHandlers() },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: 'Agent 管理' })).toBeVisible();
    await expect(await canvas.findByText('乾元')).toBeVisible();
    await expect(await canvas.findByText(/6 个结果/)).toBeVisible();
  },
};

export const AgentCreateDrawer: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: { handlers: adminHandlers() },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    const page = within(document.body);
    await canvas.findByText('乾元');
    await userEvent.click(canvas.getByRole('button', { name: /创建 Agent/ }));
    await expect(await page.findByRole('dialog')).toHaveTextContent('创建 Agent');
    await expect(page.getByLabelText('Agent 名称')).toBeVisible();
  },
};

export const AgentEditDrawer: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: { handlers: adminHandlers() },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    const page = within(document.body);
    await canvas.findByText('乾元');
    await userEvent.click(canvas.getAllByRole('button', { name: '更多操作' })[0]);
    await userEvent.click(page.getByText('编辑配置'));
    await expect(await page.findByRole('dialog')).toHaveTextContent('编辑 Agent');
    await expect(page.getByLabelText('Agent 名称')).toHaveValue('乾元');
  },
};

export const AgentDisableConfirmation: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: { handlers: adminHandlers() },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    const page = within(document.body);
    await canvas.findByText('乾元');
    await userEvent.click(canvas.getAllByRole('button', { name: '更多操作' })[0]);
    await userEvent.click(page.getByText('停用 Agent'));
    await expect(await page.findByRole('alertdialog')).toHaveTextContent('停用 Agent？');
    await expect(page.getByRole('button', { name: '确认停用' })).toBeVisible();
  },
};

export const AgentDuplicateName: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: { handlers: adminHandlers({ duplicate: true }) },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    const page = within(document.body);
    await canvas.findByText('乾元');
    await userEvent.click(canvas.getByRole('button', { name: /创建 Agent/ }));
    await userEvent.clear(page.getByLabelText('Agent 名称'));
    await userEvent.type(page.getByLabelText('Agent 名称'), '乾元');
    await userEvent.selectOptions(page.getByLabelText('LLM 模型'), model.id);
    await userEvent.selectOptions(page.getByLabelText('TTS 音色'), voices[0].id);
    await userEvent.click(page.getByRole('button', { name: '保存配置' }));
    await expect(await page.findByRole('alert')).toHaveTextContent('Agent 名称已存在');
  },
};

export const AgentLoading: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: { handlers: adminHandlers({ delayMs: 2_000 }) },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    await expect(
      await within(canvasElement).findByRole('status', { name: '正在加载数据' }),
    ).toBeVisible();
  },
};

export const AgentEmpty: Story = {
  render: () => <AdminAgentsPage />,
  parameters: {
    msw: {
      handlers: [
        http.get('*/api/admin/catalog', () => HttpResponse.json({ ...catalog, agents: [] })),
        ...adminHandlers(),
      ],
    },
    nextjs: { navigation: { pathname: '/admin/agents' } },
  },
  async play({ canvasElement }) {
    await expect(await within(canvasElement).findByText('还没有 Agent 配置')).toBeVisible();
  },
};
