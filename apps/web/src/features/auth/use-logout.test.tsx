import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';

import { useLogout } from './use-logout';

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return (
      <ToastProvider>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </ToastProvider>
    );
  };
}

describe('useLogout', () => {
  afterEach(() => cleanup());

  it('clears all cached user state before replacing the document', async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(['auth', 'me'], { user: { real_name: '旧身份' } });
    queryClient.setQueryData(['rooms', 'detail'], { name: '旧房间' });
    const request = vi.fn().mockResolvedValue({ status: 'ok' });
    const navigate = vi.fn();
    const { result } = renderHook(() => useLogout({ request, navigate }), {
      wrapper: wrapper(queryClient),
    });

    await act(() => result.current.logout());

    expect(request).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it('keeps the session cache and shows an error when logout fails', async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(['auth', 'me'], { user: { real_name: '仍登录' } });
    const request = vi.fn().mockRejectedValue(new Error('网络暂时不可用'));
    const navigate = vi.fn();
    const { result } = renderHook(() => useLogout({ request, navigate }), {
      wrapper: wrapper(queryClient),
    });

    await act(() => result.current.logout());

    expect(queryClient.getQueryData(['auth', 'me'])).toEqual({ user: { real_name: '仍登录' } });
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('网络暂时不可用');
    expect(result.current.isLoggingOut).toBe(false);
  });

  it('coalesces repeated clicks while the request is in flight', async () => {
    const queryClient = new QueryClient();
    let resolveRequest: ((value: { status: string }) => void) | undefined;
    const request = vi.fn(
      () =>
        new Promise<{ status: string }>((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const navigate = vi.fn();
    const { result } = renderHook(() => useLogout({ request, navigate }), {
      wrapper: wrapper(queryClient),
    });

    let first: Promise<void> | undefined;
    act(() => {
      first = result.current.logout();
      void result.current.logout();
    });
    expect(request).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRequest?.({ status: 'ok' });
      await first;
    });
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});
