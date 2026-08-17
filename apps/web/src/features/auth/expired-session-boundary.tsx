'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, type ReactNode } from 'react';

import { buildReturnTo } from '@/lib/return-to';
import { SESSION_EXPIRED_EVENT } from '@/lib/session-events';

const AUTH_PATHS = new Set(['/login', '/register', '/change-password']);

export function expiredSessionLoginHref(location: Pick<Location, 'pathname' | 'search' | 'hash'>) {
  const returnTo = buildReturnTo(location.pathname, location.search, location.hash);
  const params = new URLSearchParams({ reason: 'session_expired' });
  if (!AUTH_PATHS.has(location.pathname) && returnTo !== '/') {
    params.set('return_to', returnTo);
  }
  return `/login?${params.toString()}`;
}

export function ExpiredSessionBoundary({
  children,
  navigate = (href) => window.location.replace(href),
}: Readonly<{ children: ReactNode; navigate?: (href: string) => void }>) {
  const queryClient = useQueryClient();
  const navigating = useRef(false);

  useEffect(() => {
    const handleExpiredSession = () => {
      if (navigating.current) return;
      if (window.location.pathname === '/login') {
        queryClient.clear();
        return;
      }
      navigating.current = true;
      queryClient.clear();
      navigate(expiredSessionLoginHref(window.location));
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, handleExpiredSession);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handleExpiredSession);
  }, [navigate, queryClient]);

  return children;
}
