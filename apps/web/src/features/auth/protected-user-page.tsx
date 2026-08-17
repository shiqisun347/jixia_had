'use client';

import { usePathname, useRouter } from 'next/navigation';
import { type ReactNode, useEffect } from 'react';

import { ApiClientError } from '@/lib/auth-api';
import { buildReturnTo } from '@/lib/return-to';

import { AuthLoading } from './auth-loading';
import { useCurrentUser } from './use-auth';

export function ProtectedUserPage({
  children,
  returnTo,
}: Readonly<{ children: ReactNode; returnTo: string }>) {
  const router = useRouter();
  const pathname = usePathname();
  const query = useCurrentUser();

  useEffect(() => {
    const currentReturnTo = buildReturnTo(
      pathname || returnTo,
      typeof window === 'undefined' ? '' : window.location.search,
      typeof window === 'undefined' ? '' : window.location.hash,
    );
    if (
      query.data === null ||
      (query.error instanceof ApiClientError && query.error.status === 401)
    ) {
      router.replace(`/login?return_to=${encodeURIComponent(currentReturnTo)}`);
    } else if (query.data?.user.must_change_password) {
      router.replace(`/change-password?return_to=${encodeURIComponent(currentReturnTo)}`);
    }
  }, [pathname, query.data, query.error, returnTo, router]);

  if (query.error && (!(query.error instanceof ApiClientError) || query.error.status !== 401)) {
    return (
      <main className="jx-page-viewport grid place-items-center bg-[#f7faff] px-6 text-slate-700">
        <div className="max-w-md rounded-2xl border border-amber-200 bg-white px-6 py-5 text-center shadow-sm">
          <p className="font-bold">暂时无法确认登录状态</p>
          <p className="mt-2 text-sm text-slate-500">请检查网络后重试。</p>
          <button
            className="mt-4 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-bold text-white"
            onClick={() => void query.refetch()}
            type="button"
          >
            重新尝试
          </button>
        </div>
      </main>
    );
  }

  if (query.isLoading || !query.data || query.data.user.must_change_password) {
    return <AuthLoading />;
  }
  return children;
}
