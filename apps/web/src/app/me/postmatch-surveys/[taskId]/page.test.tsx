import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Suspense, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { postmatchTask, savePostmatchSpeech, submitPostmatch } = vi.hoisted(() => ({
  postmatchTask: vi.fn(),
  savePostmatchSpeech: vi.fn(),
  submitPostmatch: vi.fn(),
}));

vi.mock('@/features/auth/protected-user-page', () => ({
  ProtectedUserPage: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('@/lib/experiments-api', () => ({
  surveyApi: { postmatchTask, savePostmatchSpeech, submitPostmatch },
}));
vi.mock('@/components/ui/toast-provider', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

import PostmatchTaskPage from './page';

describe('postmatch task route', () => {
  afterEach(() => {
    cleanup();
    postmatchTask.mockReset();
    savePostmatchSpeech.mockReset();
    submitPostmatch.mockReset();
  });

  it('unwraps the Next.js dynamic route params before requesting the task', async () => {
    const taskId = '08b2f812-9c91-4d2f-b117-5d685f00ed41';
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    postmatchTask.mockImplementation(() => new Promise(() => undefined));

    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>
            <PostmatchTaskPage params={Promise.resolve({ taskId })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });

    await waitFor(() => expect(postmatchTask).toHaveBeenCalledWith(taskId));
    expect(postmatchTask).not.toHaveBeenCalledWith(undefined);
  });

  it('auto-saves each answer and advances only after the human multi-choice is confirmed', async () => {
    const taskId = '08b2f812-9c91-4d2f-b117-5d685f00ed41';
    const selfId = '10000000-0000-4000-8000-000000000001';
    const aiId = '10000000-0000-4000-8000-000000000002';
    const q1 = '自己更适合处理这个问题。';
    const q2 = '回应对手';
    const questions = {
      human_self: {
        q1: { type: 'single_choice', text: '为什么选择自己发言？', options: [q1] },
        q2: { type: 'multiple_choice', text: '这次发言想完成什么？', options: [q2] },
      },
      team_ai: {
        q1: { type: 'single_choice', text: 'AI 是否适合发言？', options: ['很适合 AI 发言'] },
        q2: { type: 'single_choice', text: 'AI 发言匹配吗？', options: ['很好'] },
      },
      overall: [],
    };
    const item = (speechId: string, state: string, text: string | null, subject: string) => ({
      speech_id: speechId,
      subject_kind: subject,
      annotatable: true,
      review_state: state,
      side: 'AFFIRMATIVE',
      sequence: speechId === selfId ? 2 : 3,
      stage_position: 2,
      stage_name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      speaker_name: subject === 'HUMAN_SELF' ? '测试辩手' : 'AI 队友',
      text,
    });
    const base = {
      id: taskId,
      match_id: 'match-1',
      questionnaire_version: 'postmatch-v2-strict-2026-08-24',
      status: 'IN_PROGRESS',
      target_total: 2,
      confirmed_total: 0,
      workflow_revision: 0,
      updated_at: '2026-08-31T00:00:00Z',
      submitted_at: null,
      questionnaire: questions,
      room_title: '测试比赛',
    };
    const initial = {
      ...base,
      answers: { speeches: {} },
      items: [item(selfId, 'CURRENT_PRE', null, 'HUMAN_SELF')],
    };
    postmatchTask.mockResolvedValue(initial);
    let revision = 0;
    savePostmatchSpeech.mockImplementation(
      (_taskId: string, _speechId: string, answer: Record<string, unknown>, confirm: boolean) => {
        revision += 1;
        return Promise.resolve({
          ...base,
          workflow_revision: revision,
          confirmed_total: confirm ? 1 : 0,
          answers: { speeches: { [selfId]: answer } },
          items: confirm
            ? [
                item(selfId, 'COMPLETE', '我的自由辩论发言', 'HUMAN_SELF'),
                item(aiId, 'CURRENT_PRE', null, 'TEAM_AI'),
              ]
            : [item(selfId, 'CURRENT_POST', '我的自由辩论发言', 'HUMAN_SELF')],
        });
      },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>
            <PostmatchTaskPage params={Promise.resolve({ taskId })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });

    fireEvent.click(await screen.findByLabelText(`A. ${q1}`));
    await waitFor(() =>
      expect(savePostmatchSpeech).toHaveBeenLastCalledWith(taskId, selfId, { q1 }, false, 0),
    );
    fireEvent.click(await screen.findByLabelText(`A. ${q2}`));
    await waitFor(() =>
      expect(savePostmatchSpeech).toHaveBeenLastCalledWith(
        taskId,
        selfId,
        { q1, q2: [q2] },
        false,
        1,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: '完成并继续' }));
    await waitFor(() =>
      expect(savePostmatchSpeech).toHaveBeenLastCalledWith(
        taskId,
        selfId,
        { q1, q2: [q2] },
        true,
        2,
      ),
    );
    expect(await screen.findByText('AI 是否适合发言？')).toBeVisible();
    expect(screen.queryByText('保存本段标注')).not.toBeInTheDocument();
  });
});
