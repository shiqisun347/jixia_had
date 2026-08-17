'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import { useToast } from '@/components/ui/toast-provider';
import type { AuthResponse } from '@/lib/auth-api';

import { authQueryKey } from './use-auth';

type AvatarRequest = () => Promise<AuthResponse>;

export function useAvatarUpdate() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const pendingRef = useRef(false);
  const [isUpdatingAvatar, setIsUpdatingAvatar] = useState(false);

  const updateAvatar = useCallback(
    async (request: AvatarRequest, successMessage: string, fallbackError: string) => {
      if (pendingRef.current) return false;
      pendingRef.current = true;
      setIsUpdatingAvatar(true);
      try {
        const result = await request();
        queryClient.setQueryData(authQueryKey, result);
        // Avatar URLs are embedded in room and match snapshots. Refetch active
        // snapshots after the authoritative user response so other open views
        // do not keep rendering the previous avatar version.
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['rooms'] }),
          queryClient.invalidateQueries({ queryKey: ['matches'] }),
        ]);
        showToast({ message: successMessage, tone: 'success' });
        return true;
      } catch (error: unknown) {
        showToast({
          message: error instanceof Error ? error.message : fallbackError,
          tone: 'error',
        });
        return false;
      } finally {
        pendingRef.current = false;
        setIsUpdatingAvatar(false);
      }
    },
    [queryClient, showToast],
  );

  return { isUpdatingAvatar, updateAvatar };
}
