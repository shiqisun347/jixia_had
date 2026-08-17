import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SESSION_EXPIRED_EVENT } from '@/lib/session-events';

import { ExpiredSessionBoundary, expiredSessionLoginHref } from './expired-session-boundary';

describe('ExpiredSessionBoundary', () => {
  afterEach(() => {
    cleanup();
    window.history.replaceState(null, '', '/');
  });

  it('builds a safe login return target and avoids authentication loops', () => {
    expect(
      expiredSessionLoginHref({
        pathname: '/lobby',
        search: '?join=1',
        hash: '#invite',
      }),
    ).toBe('/login?reason=session_expired&return_to=%2Flobby%3Fjoin%3D1%23invite');
    expect(expiredSessionLoginHref({ pathname: '/change-password', search: '', hash: '' })).toBe(
      '/login?reason=session_expired',
    );
  });

  it('clears cached user state and navigates only once', () => {
    window.history.replaceState(null, '', '/rooms/room-1?mode=debater#seat');
    const queryClient = new QueryClient();
    queryClient.setQueryData(['auth', 'me'], { user: { id: 'user-1' } });
    queryClient.setQueryData(['rooms', 'room-1'], { title: '旧房间' });
    const navigate = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <ExpiredSessionBoundary navigate={navigate}>
          <div>受保护内容</div>
        </ExpiredSessionBoundary>
      </QueryClientProvider>,
    );

    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(navigate).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith(
      '/login?reason=session_expired&return_to=%2Frooms%2Froom-1%3Fmode%3Ddebater%23seat',
    );
  });

  it('does not reload an already open login page when the stale cookie is checked again', () => {
    window.history.replaceState(null, '', '/login?reason=session_expired&return_to=%2Flobby');
    const queryClient = new QueryClient();
    queryClient.setQueryData(['auth', 'me'], { user: { id: 'stale-user' } });
    const navigate = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <ExpiredSessionBoundary navigate={navigate}>
          <div>登录</div>
        </ExpiredSessionBoundary>
      </QueryClientProvider>,
    );

    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(navigate).not.toHaveBeenCalled();
  });
});
