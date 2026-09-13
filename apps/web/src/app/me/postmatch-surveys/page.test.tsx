import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const postmatch = vi.hoisted(() => vi.fn());
vi.mock('@/features/auth/protected-user-page', () => ({ ProtectedUserPage: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('@/lib/experiments-api', async (importOriginal) => { const original = await importOriginal<typeof import('@/lib/experiments-api')>(); return { ...original, surveyApi: { ...original.surveyApi, postmatch } }; });
import PostmatchSurveysPage from './page';

afterEach(() => { cleanup(); vi.clearAllMocks(); });
const task = { id: 'task-1', match_id: 'match-1', questionnaire_version: 'v2', status: 'PENDING' as const, answers: { speeches: {} }, updated_at: '2026-08-24T00:00:00Z', submitted_at: null, room_title: '测试比赛', topic: '过程还是结果', target_total: 4, confirmed_total: 2, items: [{ speech_id: 's1', subject_kind: 'HUMAN_SELF', annotatable: true, review_state: 'CURRENT_PRE' as const }] };

describe('postmatch survey list', () => {
  it('shows one entry per match and links to the single-task workspace', async () => {
    postmatch.mockResolvedValue([task]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><PostmatchSurveysPage /></QueryClientProvider>);
    expect(await screen.findByText('测试比赛')).toBeVisible();
    expect(screen.getByRole('link', { name: /继续填写本场问卷/ })).toHaveAttribute('href', '/me/postmatch-surveys/task-1');
    expect(screen.getByRole('link', { name: '填写独立 AI 辩论体验问卷' })).toHaveAttribute('href', '/me/ai-experience');
    expect(screen.getByText('2/4 条')).toBeVisible();
  });
});
