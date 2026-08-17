import { describe, expect, it, vi } from 'vitest';

import { ApiClientError, authApi } from '@/lib/auth-api';

import { authStatusQueryFn, currentUserRefetchInterval } from './use-auth';

describe('auth status query', () => {
  it('passes a combined cancellation signal to the auth endpoint', async () => {
    const spy = vi.spyOn(authApi, 'currentUser').mockResolvedValue({ user: {} } as never);
    const controller = new AbortController();

    await authStatusQueryFn({ queryKey: ['auth', 'me'], signal: controller.signal } as never);

    expect(spy).toHaveBeenCalledWith(expect.any(AbortSignal));
    spy.mockRestore();
  });

  it('polls authenticated state but not anonymous state', () => {
    expect(currentUserRefetchInterval({ user: {} })).toBe(60_000);
    expect(currentUserRefetchInterval(undefined)).toBe(false);
  });

  it('treats the expected unauthenticated response as anonymous state', async () => {
    const spy = vi.spyOn(authApi, 'currentUser').mockRejectedValue(
      new ApiClientError(401, {
        error: { code: 'not_authenticated', message: '请先登录' },
      }),
    );

    await expect(
      authStatusQueryFn({
        queryKey: ['auth', 'me'],
        signal: new AbortController().signal,
      } as never),
    ).resolves.toBeNull();
    spy.mockRestore();
  });

  it('keeps unexpected authentication failures visible to the query', async () => {
    const spy = vi.spyOn(authApi, 'currentUser').mockRejectedValue(
      new ApiClientError(401, {
        error: { code: 'session_expired', message: '登录状态已失效' },
      }),
    );

    await expect(
      authStatusQueryFn({
        queryKey: ['auth', 'me'],
        signal: new AbortController().signal,
      } as never),
    ).rejects.toMatchObject({ code: 'session_expired' });
    spy.mockRestore();
  });

  it('fails fast when the auth request exceeds its timeout', async () => {
    const spy = vi.spyOn(authApi, 'currentUser').mockReturnValue(new Promise(() => {}) as never);
    vi.useFakeTimers();
    const pending = authStatusQueryFn({
      queryKey: ['auth', 'me'],
      signal: new AbortController().signal,
    } as never);
    const assertion = expect(pending).rejects.toThrow('auth_status_timeout');
    await vi.advanceTimersByTimeAsync(5_000);
    await assertion;
    vi.useRealTimers();
    spy.mockRestore();
  });
});
