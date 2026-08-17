import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { http, HttpResponse } from 'msw';
import { expect, userEvent, within } from 'storybook/test';

import { PostmatchContent } from './page';

const matchId = '90000000-0000-4000-8000-000000000001';
const viewerId = '10000000-0000-4000-8000-000000000001';

const user = {
  id: viewerId,
  username: 'reviewer',
  real_name: '林知行',
  role: 'USER',
  status: 'ACTIVE',
  must_change_password: false,
  avatar_version: 0,
};

const participants = [
  { id: 'p-a1', kind: 'HUMAN', display_name: '林知行', side: 'AFFIRMATIVE', seat_no: 1 },
  { id: 'p-a2', kind: 'AGENT', display_name: '坤元', side: 'AFFIRMATIVE', seat_no: 2 },
  { id: 'p-n1', kind: 'AGENT', display_name: '乾元', side: 'NEGATIVE', seat_no: 1 },
  { id: 'p-n2', kind: 'HUMAN', display_name: '沈观', side: 'NEGATIVE', seat_no: 2 },
];

const speeches = [
  {
    id: 'speech-a1',
    speaker_kind: 'HUMAN',
    side: 'AFFIRMATIVE',
    seat_no: 1,
    display_text: '效率与公平需要同时被看见。',
    asr_raw_final_text: '效率与公平需要同时被看见。',
    user_id: viewerId,
  },
  {
    id: 'speech-n1',
    speaker_kind: 'AGENT',
    side: 'NEGATIVE',
    seat_no: 1,
    display_text: '技术进步不能替代规则与责任。',
    asr_raw_final_text: null,
    user_id: null,
  },
];

const replayFile = {
  id: 'file-replay',
  file_kind: 'MATCH_REPLAY',
  status: 'READY',
  owner_user_id: null,
  duration_ms: 182_000,
  byte_count: 1_024,
  download_url: '/fixtures/match-replay.opus',
};

function postmatch(overrides: Record<string, unknown> = {}) {
  return {
    match_id: matchId,
    status: 'FINISHED',
    title: '4v4 正式辩论赛',
    label: '正式赛',
    display_topic: '在人工智能快速发展的今天，我们更应重视效率还是公平？',
    admin_note: null,
    context_version: 4,
    speeches,
    participants,
    submissions: [],
    files: [replayFile],
    judge: null,
    can_retry_judge: false,
    ...overrides,
  };
}

const auth = http.get('*/api/auth/me', () => HttpResponse.json({ user }));
const avatar = http.get(
  '*/api/users/:userId/avatar',
  () => new HttpResponse(null, { status: 204 }),
);
const replayAudio = http.get(
  '*/fixtures/match-replay.opus',
  () => new HttpResponse(new Uint8Array(), { headers: { 'Content-Type': 'audio/ogg' } }),
);
const handlers = (...storyHandlers: ReturnType<typeof http.get>[]) => [
  auth,
  avatar,
  replayAudio,
  ...storyHandlers,
];
const baseParameters = {
  layout: 'fullscreen',
  nextjs: { appDirectory: true, navigation: { pathname: `/matches/${matchId}` } },
};

const meta = { title: 'Postmatch/States', parameters: baseParameters } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

export const JudgeProcessing: Story = {
  render: () => <PostmatchContent matchId={matchId} />,
  parameters: {
    msw: {
      handlers: handlers(
        http.get('*/api/matches/:matchId/postmatch', () =>
          HttpResponse.json(
            postmatch({ judge: { status: 'RUNNING', result: null }, can_retry_judge: false }),
          ),
        ),
      ),
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: 'AI 裁判' })).toBeVisible();
    await expect(await canvas.findByText('AI 裁判正在评分…')).toBeVisible();
  },
};

export const JudgeSucceeded: Story = {
  render: () => <PostmatchContent matchId={matchId} />,
  parameters: {
    msw: {
      handlers: handlers(
        http.get('*/api/matches/:matchId/postmatch', () =>
          HttpResponse.json(
            postmatch({
              judge: {
                status: 'SUCCEEDED',
                result: {
                  winner: 'AFFIRMATIVE',
                  team_scores: {
                    AFFIRMATIVE: {
                      argument: 30,
                      rebuttal: 24,
                      evidence: 18,
                      teamwork: 14,
                      expression: 9,
                    },
                    NEGATIVE: {
                      argument: 27,
                      rebuttal: 23,
                      evidence: 17,
                      teamwork: 13,
                      expression: 8,
                    },
                  },
                  participants: [{ participant_id: 'p-a1', score: 18, comment: '表达清晰' }],
                  team_comments: { AFFIRMATIVE: '论证完整。', NEGATIVE: '回应及时。' },
                },
              },
            }),
          ),
        ),
      ),
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('正方获胜')).toBeVisible();
    await expect(await canvas.findByText('完整文字记录')).toBeVisible();
    await expect(await canvas.findByText('下载整场 Opus 回放')).toBeVisible();
  },
};

export const JudgeFailedRetry: Story = {
  render: () => <PostmatchContent matchId={matchId} />,
  parameters: {
    msw: {
      handlers: handlers(
        http.get('*/api/matches/:matchId/postmatch', () =>
          HttpResponse.json(
            postmatch({
              judge: { status: 'FAILED', error_code: 'llm_timeout', result: null },
              can_retry_judge: true,
            }),
          ),
        ),
        http.post('*/api/matches/:matchId/judge/retry', () =>
          HttpResponse.json(
            postmatch({ judge: { status: 'RUNNING', result: null }, can_retry_judge: false }),
          ),
        ),
      ),
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/AI 评分未完成/)).toBeVisible();
    const retry = await canvas.findByRole('button', { name: '重新评分' });
    await userEvent.click(retry);
    await expect(await canvas.findByText('AI 裁判正在评分…')).toBeVisible();
  },
};

export const TerminatedWithoutJudge: Story = {
  render: () => <PostmatchContent matchId={matchId} />,
  parameters: {
    msw: {
      handlers: handlers(
        http.get('*/api/matches/:matchId/postmatch', () =>
          HttpResponse.json(
            postmatch({
              status: 'TERMINATED',
              judge: null,
              files: [],
            }),
          ),
        ),
      ),
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('比赛已终止，不进行 AI 评分。')).toBeVisible();
    await expect(canvas.queryByText('正方获胜')).not.toBeInTheDocument();
    await expect(canvas.queryByText('下载整场 Opus 回放')).not.toBeInTheDocument();
  },
};

export const ReviewOwnTranscript: Story = {
  render: () => <PostmatchContent matchId={matchId} />,
  parameters: {
    msw: {
      handlers: handlers(
        http.get('*/api/matches/:matchId/postmatch', () => HttpResponse.json(postmatch())),
        http.patch('*/api/matches/:matchId/speeches/:speechId/display-text', () =>
          HttpResponse.json({ context_version: 5 }),
        ),
      ),
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: '修改我的文字' }));
    const editor = await canvas.findByRole('textbox', { name: '修改本人发言文字' });
    await userEvent.clear(editor);
    await userEvent.type(editor, '修改后的正式文字。');
    await userEvent.click(await canvas.findByRole('button', { name: '保存修改' }));
    await expect(await canvas.findByText('修改后的正式文字。')).toBeVisible();
  },
};
