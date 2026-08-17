import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));

vi.mock('@/lib/auth-api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/auth-api')>();
  return {
    ...original,
    requestJson,
    authApi: { ...original.authApi, currentUser: vi.fn() },
  };
});

import { authApi } from '@/lib/auth-api';

import HomePage from './page';

describe('home prototype route', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderPage() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <HomePage />
      </QueryClientProvider>,
    );
  }

  it('does not request the protected lobby for a signed-out visitor', async () => {
    vi.mocked(authApi.currentUser).mockRejectedValue(new Error('not authenticated'));
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/leaderboards')
        return Promise.resolve({ generated_at: null, human: [], agent: [] });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
    renderPage();

    expect(
      screen.getByRole('heading', {
        name: '让人类与 Agent，在声音中交锋与共创',
      }),
    ).toBeVisible();
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '正在进行' })).toBeVisible();
    expect(screen.queryByTestId('home-prototype-note')).not.toBeInTheDocument();
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith('/api/leaderboards'));
    expect(requestJson).not.toHaveBeenCalledWith('/api/lobby/rooms');
  });

  it('loads lobby rooms after authentication succeeds', async () => {
    vi.mocked(authApi.currentUser).mockResolvedValue({ user: { id: 'user-1' } } as never);
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/lobby/rooms') return Promise.resolve([]);
      if (path === '/api/leaderboards')
        return Promise.resolve({ generated_at: null, human: [], agent: [] });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
    renderPage();

    await waitFor(() => expect(requestJson).toHaveBeenCalledWith('/api/lobby/rooms'));
    expect(requestJson).toHaveBeenCalledWith('/api/leaderboards');
  });

  it('never renders Storybook rooms while runtime requests are unresolved', async () => {
    let resolveRooms: (value: unknown[]) => void = () => undefined;
    const rooms = new Promise<unknown[]>((resolve) => {
      resolveRooms = resolve;
    });
    vi.mocked(authApi.currentUser).mockResolvedValue({ user: { id: 'user-1' } } as never);
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/lobby/rooms') return rooms;
      if (path === '/api/leaderboards')
        return Promise.resolve({ generated_at: null, human: [], agent: [] });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    renderPage();

    await waitFor(() => expect(requestJson).toHaveBeenCalledWith('/api/lobby/rooms'));
    expect(screen.getByRole('status')).toHaveTextContent('正在同步比赛信息');
    expect(screen.queryByRole('link', { name: /查看比赛：/ })).not.toBeInTheDocument();
    expect(screen.queryByText('公平与效率之辩')).not.toBeInTheDocument();

    resolveRooms([]);
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '目前没有进行中的比赛' })).toBeVisible(),
    );
  });

  it('shows an actionable sync state when the lobby request fails', async () => {
    vi.mocked(authApi.currentUser).mockResolvedValue({ user: { id: 'user-1' } } as never);
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/lobby/rooms') {
        return Promise.reject(new Error('temporary network error'));
      }
      if (path === '/api/leaderboards') {
        return Promise.resolve({ generated_at: null, human: [], agent: [] });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByText('比赛信息暂时无法同步，当前显示最近一次成功结果。')).toBeVisible(),
    );
    expect(screen.getAllByRole('button', { name: '重新同步' }).length).toBeGreaterThan(0);
  });
});
