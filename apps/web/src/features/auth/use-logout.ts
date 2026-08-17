'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import { useOptionalToast } from '@/components/ui/toast-provider';
import { authApi } from '@/lib/auth-api';

interface LogoutDependencies {
  readonly request?: typeof authApi.logout;
  readonly navigate?: () => void;
}

function replaceWithHome() {
  window.location.replace('/');
}

export function useLogout({
  request = authApi.logout,
  navigate = replaceWithHome,
}: LogoutDependencies = {}) {
  const queryClient = useQueryClient();
  const toast = useOptionalToast();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const inFlight = useRef(false);
  const logout = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setIsLoggingOut(true);
    try {
      await request();
      queryClient.clear();
      navigate();
    } catch (error) {
      toast?.showToast({
        message: error instanceof Error ? error.message : '退出失败，请稍后重试。',
        tone: 'error',
      });
    } finally {
      inFlight.current = false;
      setIsLoggingOut(false);
    }
  }, [navigate, queryClient, request, toast]);

  return { logout, isLoggingOut } as const;
}
