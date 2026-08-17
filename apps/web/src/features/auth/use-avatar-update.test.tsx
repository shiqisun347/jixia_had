import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';
import type { AuthResponse } from '@/lib/auth-api';

import { authQueryKey } from './use-auth';
import { useAvatarUpdate } from './use-avatar-update';

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return (
      <ToastProvider>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </ToastProvider>
    );
  };
}

const updatedAuth = {
  user: { id: 'user-1', real_name: '测试用户', avatar_version: 2 },
} as AuthResponse;

describe('useAvatarUpdate', () => {
  afterEach(() => cleanup());

  it('publishes the server response to the shared auth cache', async () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const request = vi.fn().mockResolvedValue(updatedAuth);
    const { result } = renderHook(() => useAvatarUpdate(), { wrapper: wrapper(queryClient) });

    await act(() => result.current.updateAvatar(request, '头像已更新。', '头像更新失败。'));

    expect(queryClient.getQueryData(authQueryKey)).toBe(updatedAuth);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['rooms'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['matches'] });
    expect(screen.getByRole('status')).toHaveTextContent('头像已更新。');
    expect(result.current.isUpdatingAvatar).toBe(false);
  });

  it('coalesces repeated actions before React can disable the controls', async () => {
    const queryClient = new QueryClient();
    let resolveRequest: ((value: AuthResponse) => void) | undefined;
    const request = vi.fn(
      () =>
        new Promise<AuthResponse>((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const { result } = renderHook(() => useAvatarUpdate(), { wrapper: wrapper(queryClient) });

    let first: Promise<boolean> | undefined;
    let second: Promise<boolean> | undefined;
    act(() => {
      first = result.current.updateAvatar(request, '头像已更新。', '头像更新失败。');
      second = result.current.updateAvatar(request, '头像已更新。', '头像更新失败。');
    });
    expect(request).toHaveBeenCalledTimes(1);
    await expect(second).resolves.toBe(false);

    await act(async () => {
      resolveRequest?.(updatedAuth);
      await first;
    });
  });

  it('keeps the existing cache and exposes a retryable failure', async () => {
    const queryClient = new QueryClient();
    const previous = { user: { id: 'user-1', avatar_version: 1 } };
    queryClient.setQueryData(authQueryKey, previous);
    const request = vi.fn().mockRejectedValue(new Error('头像服务暂时不可用'));
    const { result } = renderHook(() => useAvatarUpdate(), { wrapper: wrapper(queryClient) });

    await act(() => result.current.updateAvatar(request, '头像已更新。', '头像更新失败。'));

    expect(queryClient.getQueryData(authQueryKey)).toBe(previous);
    expect(screen.getByRole('alert')).toHaveTextContent('头像服务暂时不可用');
    expect(result.current.isUpdatingAvatar).toBe(false);
  });
});
