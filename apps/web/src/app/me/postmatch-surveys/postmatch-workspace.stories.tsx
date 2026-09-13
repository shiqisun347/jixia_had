import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/nextjs-vite';

import type { PostmatchSurveyTask } from '@/lib/experiments-api';

import { Workspace } from './[taskId]/page';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const questionnaire = {
  human_self: {
    q1: {
      type: 'single_choice' as const,
      text: '当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？请选择最主要的原因。',
      options: [
        '自己更适合处理这个问题。',
        '担心AI 不适合或者处理不好这个问题。',
        '没人回答或者其他担忧被迫回答',
        '其他。',
      ],
    },
    q2: {
      type: 'multiple_choice' as const,
      text: '你这次发言最主要想完成什么？',
      options: ['回应对手', '接续队友', '补足缺口', '主动推进', '调整方向', '其他'],
      descriptions: {
        回应对手: '对手刚提出了需要处理的攻击、质疑或追问',
        接续队友: '队友的内容需要补充、澄清或继续推进',
        补足缺口: '我注意到己方有一个重要问题还没人处理',
        主动推进: '我认为应该推进新的或已有的己方论证',
        调整方向: '我认为当前讨论需要转向更重要的问题',
      },
    },
  },
  team_ai: {
    q1: {
      type: 'single_choice' as const,
      text: '在发言之前，当时你觉得 AI 这个时候发言合适吗',
      options: [
        '不合适，比如有此时其他人更合适发言：',
        '发言不发言都合适：',
        '很适合 AI 发言',
        '无法判断：',
      ],
    },
    q2: {
      type: 'single_choice' as const,
      text: '看完 AI 的实际发言后，你认为这次发言与当时团队需要的匹配程度如何？（不仅看内容）',
      options: [
        '很好，处理了当时团队真正需要处理的问题',
        '一般，内容有点冗余，重复表达，新增价值很小',
        '一般，有点跑偏或者钻牛角尖，内容可能有价值，但不是当时最需要处理的问题',
        '不好，为后续带来了额外的修复负担',
        '其他不好或者一般的原因',
        '无法判断',
      ],
    },
  },
  overall: [],
};

const task: PostmatchSurveyTask = {
  id: '10000000-0000-4000-8000-000000000001',
  match_id: '20000000-0000-4000-8000-000000000001',
  questionnaire_version: 'postmatch-v2-strict-2026-08-24',
  status: 'IN_PROGRESS',
  answers: { speeches: {} },
  updated_at: '2026-08-31T00:00:00Z',
  submitted_at: null,
  room_title: '人机协同公开辩论赛',
  topic: '过程还是结果更能体现奋斗的价值',
  target_total: 4,
  confirmed_total: 0,
  workflow_revision: 0,
  questionnaire,
  items: [
    {
      speech_id: '30000000-0000-4000-8000-000000000001',
      subject_kind: 'TEAM_HUMAN',
      annotatable: false,
      review_state: 'CONTEXT',
      side: 'AFFIRMATIVE',
      seat_no: 1,
      sequence: 1,
      stage_position: 1,
      stage_name: '正方一辩立论',
      stage_kind: 'FIXED_SPEECH',
      speaker_name: '林知夏',
      text: '奋斗的价值首先体现在持续投入与能力成长的过程中。结果可能受到环境和偶然因素影响，但过程中的选择与坚持始终属于行动者。',
      started_at: '2026-08-30T13:00:00Z',
    },
    {
      speech_id: '30000000-0000-4000-8000-000000000002',
      subject_kind: 'OPPONENT_AI',
      annotatable: false,
      review_state: 'CONTEXT',
      side: 'NEGATIVE',
      seat_no: 1,
      sequence: 2,
      stage_position: 2,
      stage_name: '反方一辩立论',
      stage_kind: 'FIXED_SPEECH',
      speaker_name: '墨衡',
      text: '奋斗不是自我感动，结果把个人投入转化为能够被社会识别和验证的真实贡献。',
      started_at: '2026-08-30T13:02:00Z',
    },
    {
      speech_id: '30000000-0000-4000-8000-000000000003',
      subject_kind: 'OPPONENT_HUMAN',
      annotatable: false,
      review_state: 'CONTEXT',
      side: 'NEGATIVE',
      seat_no: 2,
      sequence: 3,
      stage_position: 3,
      stage_name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      speaker_name: '周行远',
      text: '如果过程本身就足以体现价值，为什么方向错误、资源浪费的努力仍然需要被纠正？',
      started_at: '2026-08-30T13:05:00Z',
    },
    {
      speech_id: '30000000-0000-4000-8000-000000000004',
      subject_kind: 'TEAM_AI',
      annotatable: false,
      review_state: 'CONTEXT',
      side: 'AFFIRMATIVE',
      seat_no: 3,
      sequence: 4,
      stage_position: 3,
      stage_name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      speaker_name: '砚青',
      text: '纠正方向恰恰发生在过程中。一次没有达到预期的尝试，也可能积累判断方法并推动下一次行动。',
      started_at: '2026-08-30T13:05:30Z',
    },
    {
      speech_id: '30000000-0000-4000-8000-000000000005',
      subject_kind: 'HUMAN_SELF',
      annotatable: true,
      review_state: 'CURRENT_PRE',
      side: 'AFFIRMATIVE',
      seat_no: 2,
      sequence: 5,
      stage_position: 3,
      stage_name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      speaker_name: '我',
      text: null,
      started_at: '2026-08-30T13:06:00Z',
    },
  ],
};

const meta = {
  title: 'Postmatch/Free Debate Annotation Chat',
  component: Workspace,
  decorators: [
    (Story) => (
      <QueryClientProvider client={client}>
        <Story />
      </QueryClientProvider>
    ),
  ],
  parameters: { layout: 'fullscreen' },
  args: { initial: task },
} satisfies Meta<typeof Workspace>;

export default meta;
type Story = StoryObj<typeof meta>;

export const CurrentHumanSpeech: Story = {};
