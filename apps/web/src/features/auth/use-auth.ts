'use client';

import { useQuery, type QueryFunctionContext } from '@tanstack/react-query';

import { ApiClientError, authApi } from '@/lib/auth-api';

export const authQueryKey = ['auth', 'me'] as const;

export function currentUserRefetchInterval(data: unknown) {
  return data ? 60_000 : false;
}

const AUTH_STATUS_TIMEOUT_MS = 5_000;

export async function authStatusQueryFn({ signal }: QueryFunctionContext) {
  const timeoutController = new AbortController();
  let rejectTimeout: ((reason: Error) => void) | undefined;
  const timeoutFailure = new Promise<never>((_, reject) => {
    rejectTimeout = reject;
  });
  const timeout = setTimeout(() => {
    timeoutController.abort();
    rejectTimeout?.(new Error('auth_status_timeout'));
  }, AUTH_STATUS_TIMEOUT_MS);
  try {
    try {
      return await Promise.race([
        authApi.currentUser(AbortSignal.any([signal, timeoutController.signal])),
        timeoutFailure,
      ]);
    } catch (error) {
      if (
        error instanceof ApiClientError &&
        error.status === 401 &&
        error.code === 'not_authenticated'
      ) {
        return null;
      }
      throw error;
    }
  } finally {
    clearTimeout(timeout);
  }
}

export function useCurrentUser() {
  return useQuery({
    queryKey: authQueryKey,
    queryFn: authStatusQueryFn,
    refetchInterval: (query) => currentUserRefetchInterval(query.state.data),
    refetchOnWindowFocus: true,
  });
}
