import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, userEvent, within } from 'storybook/test';
import { http, HttpResponse } from 'msw';

import { AdminShell } from '@/features/admin/admin-shell';

import { AdminExperimentPage } from './admin-experiment-page';
import { ExpertWorkspace } from './expert-workspace';
import { ParticipantAnnotationPage } from './participant-annotation-page';

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  username: 'P01',
  real_name: '参与者 P01',
  role: 'USER',
  status: 'ACTIVE',
  must_change_password: false,
  default_avatar_key: 'human-01',
  avatar_version: 0,
  has_custom_avatar: false,
};
const admin = { ...user, username: 'admin', real_name: '系统管理员', role: 'ADMIN' };
const taskId = '10000000-0000-4000-8000-000000000001';
const opportunityId = '20000000-0000-4000-8000-000000000001';
const speechId = '30000000-0000-4000-8000-000000000001';
const batchId = '40000000-0000-4000-8000-000000000001';
const ruleId = '41000000-0000-4000-8000-000000000001';

function silentWav() {
  const dataSize = 3_200;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) =>
    [...value].forEach((character, index) =>
      view.setUint8(offset + index, character.charCodeAt(0)),
    );
  text(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16_000, true);
  view.setUint32(28, 32_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, dataSize, true);
  return buffer;
}

const auth = http.get('*/api/auth/me', () => HttpResponse.json({ user }));
const audioResponse = () =>
  new HttpResponse(silentWav(), { headers: { 'Content-Type': 'audio/wav' } });
const audioHandlers = [
  http.get('*/api/experiments/tasks/:taskId/audio/:speechId', audioResponse),
  http.get('*/api/experiments/expert-tasks/:taskId/audio/:speechId', audioResponse),
];
const history = [
  {
    speech_id: speechId,
    speaker_kind: 'HUMAN',
    side: 'NEGATIVE',
    seat_no: 1,
    text: '对方刚才强调，奋斗的价值应由最终结果衡量。',
  },
];
const participantTask = {
  id: taskId,
  experiment_attempt_id: '50000000-0000-4000-8000-000000000001',
  status: 'IN_PROGRESS',
  due_at: '2026-08-22T12:00:00Z',
  late: false,
  submitted_at: null,
  scheduled_match_kind: 'FORMAL',
  items: [
    {
      id: '60000000-0000-4000-8000-000000000001',
      opportunity_id: opportunityId,
      subject_kind: 'TEAM_AI',
      speech_id: speechId,
      position: 1,
      stage1_locked: true,
      revealed: true,
      frozen_context: {
        side: 'AFFIRMATIVE',
        affirmative_remaining_ms: 278000,
        negative_remaining_ms: 291000,
        history,
      },
      speech_text: '结果只有在回应真实阻力时，才能反过来证明奋斗过程的价值。',
      answers: { 'stage1.appropriateness': '合适' },
      answer_versions: { 'stage1.appropriateness': 1 },
    },
  ],
  questionnaire: null,
};
const humanParticipantTask = {
  ...participantTask,
  items: [
    {
      ...participantTask.items[0],
      subject_kind: 'HUMAN_SELF',
      stage1_locked: false,
      revealed: true,
      speech_text: '我方需要先回应对手刚才的质疑，再继续推进核心论证。',
      answers: {},
      answer_versions: {},
    },
  ],
};
const expertTask = {
  id: taskId,
  batch_id: batchId,
  status: 'IN_PROGRESS',
  submitted_at: null,
  items: [
    {
      opportunity_id: opportunityId,
      frozen_payload: {
        opportunity_id: opportunityId,
        sequence_no: 4,
        side: 'AFFIRMATIVE',
        trigger_kind: 'HUMAN_SPEECH',
        context_version: 12,
        context: { affirmative_remaining_ms: 278000, negative_remaining_ms: 291000, history },
      },
      q1: null,
      q2: null,
      q3: null,
      client_version: 0,
      saved_at: '2026-08-21T12:00:00Z',
      submitted_at: null,
    },
  ],
};

const meta = {
  title: 'Experiments/V2 Surfaces',
  parameters: { layout: 'fullscreen', nextjs: { appDirectory: true } },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const AdminSetup: Story = {
  render: () => (
    <AdminShell user={admin}>
      <AdminExperimentPage />
    </AdminShell>
  ),
  parameters: {
    msw: {
      handlers: [
        http.get('*/api/admin/experiments/batches', () =>
          HttpResponse.json([
            {
              id: batchId,
              code: 'PAPER_2026',
              title: '正式实验批次',
              status: 'DRAFT',
              schedule_version: 0,
              rule_id: ruleId,
              format_version_id: null,
              training_room_quota: 3,
              created_by: admin.id,
              created_at: '2026-08-22T00:00:00Z',
              updated_at: '2026-08-22T00:00:00Z',
              published_at: null,
              disabled_at: null,
            },
          ]),
        ),
        http.get('*/api/admin/catalog', () =>
          HttpResponse.json({
            models: [],
            voices: [],
            agents: [],
            topics: [],
            rules: [
              {
                id: ruleId,
                name: '论文实验三阶段赛制',
                status: 'ENABLED',
                side_size: 4,
                audio_reviewed_at: '2026-08-21T00:00:00Z',
              },
            ],
          }),
        ),
        http.get(`*/api/admin/rules/${ruleId}/agents`, () => HttpResponse.json([])),
        http.get('*/api/admin/users', () =>
          HttpResponse.json({ items: [], page: 1, page_size: 100, total: 0, total_pages: 0 }),
        ),
        http.get(`*/api/admin/experiments/batches/${batchId}/roster`, () =>
          HttpResponse.json({ teams: [], experts: [] }),
        ),
        http.get(`*/api/admin/experiments/batches/${batchId}/schedule`, () =>
          HttpResponse.json({
            batch_id: batchId,
            batch_status: 'DRAFT',
            schedule_version: 0,
            matches: [],
          }),
        ),
        http.get('*/api/admin/experiments/prompt-templates', () =>
          HttpResponse.json({
            version: 'paper-v2.0-2026-08-21',
            decision_prompt: '# 决策 Prompt\n{"should_speak": true|false}',
            speech_prompt: '# 发言 Prompt\n{{DEBATE_HISTORY}}',
          }),
        ),
        http.get('*/api/admin/incidents', () => HttpResponse.json({ items: [] })),
        http.get('*/api/admin/diagnostics/events', () => HttpResponse.json({ items: [] })),
        http.get('*/api/admin/diagnostics/tasks', () => HttpResponse.json({ items: [] })),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: '论文实验管理' })).toBeVisible();
    await userEvent.click(canvas.getByRole('button', { name: '新建批次' }));
    await expect(within(canvasElement.ownerDocument.body).getByLabelText('批次编号')).toBeVisible();
  },
};

export const AdminPublishedProgress: Story = {
  render: () => (
    <AdminShell user={admin}>
      <AdminExperimentPage />
    </AdminShell>
  ),
  parameters: {
    msw: {
      handlers: [
        http.get('*/api/admin/experiments/batches', () =>
          HttpResponse.json([
            {
              id: batchId,
              code: 'PAPER_2026',
              title: '正式实验批次',
              status: 'PUBLISHED',
              schedule_version: 1,
              rule_id: ruleId,
              created_by: admin.id,
              created_at: '2026-08-22T00:00:00Z',
              updated_at: '2026-08-22T00:00:00Z',
              published_at: '2026-08-22T01:00:00Z',
              disabled_at: null,
            },
          ]),
        ),
        http.get(`*/api/admin/experiments/batches/${batchId}/progress`, () =>
          HttpResponse.json({
            batch_id: batchId,
            formal_total: 18,
            formal_completed: 5,
            matches: [
              {
                scheduled_match_id: '45000000-0000-4000-8000-000000000001',
                round_no: 1,
                match_no: 1,
                kind: 'FORMAL',
                topic_title: '过程还是结果更能体现奋斗的价值',
                affirmative_team_code: 'T01',
                negative_team_code: 'T02',
                match_status: 'RUNNING',
                attempt_id: '46000000-0000-4000-8000-000000000001',
                attempt_no: 1,
                attempt_status: 'RUNNING',
                match_id: '47000000-0000-4000-8000-000000000001',
                completed_annotations: 2,
                total_annotations: 6,
                public_at: null,
              },
            ],
          }),
        ),
        http.get('*/api/admin/catalog', () =>
          HttpResponse.json({ models: [], voices: [], agents: [], topics: [], rules: [] }),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('过程还是结果更能体现奋斗的价值')).toBeVisible();
    await expect(canvas.getByText('第 1 次尝试 · 进行中')).toBeVisible();
    await expect(canvas.getByRole('link', { name: '数据与请求日志' })).toBeVisible();
  },
};

export const ParticipantStageTwo: Story = {
  render: () => <ParticipantAnnotationPage taskId={taskId} />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: `/experiments/annotations/${taskId}` } },
    msw: {
      handlers: [
        auth,
        ...audioHandlers,
        http.get('*/api/experiments/annotation-tasks', () => HttpResponse.json([participantTask])),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('事前判断已锁定')).toBeVisible();
    await expect(canvas.getByText('实际发言')).toBeVisible();
  },
};

export const ParticipantHumanQuestions: Story = {
  render: () => <ParticipantAnnotationPage taskId={taskId} />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: `/experiments/annotations/${taskId}` } },
    msw: {
      handlers: [
        auth,
        ...audioHandlers,
        http.get('*/api/experiments/annotation-tasks', () =>
          HttpResponse.json([humanParticipantTask]),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(
      await canvas.findByText(
        'Q1 当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？请选择最主要的原因。',
      ),
    ).toBeVisible();
    await expect(canvas.getByText('Q2 你这次发言最主要想完成什么？')).toBeVisible();
    await expect(canvas.getByText('：对手刚提出了需要处理的攻击、质疑或追问')).toBeVisible();
  },
};

export const ExpertFrozenContext: Story = {
  render: () => <ExpertWorkspace taskId={taskId} />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: `/experiments/expert/${taskId}` } },
    msw: {
      handlers: [
        auth,
        ...audioHandlers,
        http.get('*/api/experiments/expert-tasks', () => HttpResponse.json([expertTask])),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('冻结场上状态')).toBeVisible();
    await expect(canvas.getByText('此前辩论记录')).toBeVisible();
    await expect(canvas.getByText(/对方刚才强调/)).toBeVisible();
  },
};
