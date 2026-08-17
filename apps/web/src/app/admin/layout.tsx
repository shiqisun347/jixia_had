'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';

import { AuthLoading } from '@/features/auth/auth-loading';
import { useCurrentUser } from '@/features/auth/use-auth';
import { AdminRefreshButton, AdminShell } from '@/features/admin';
import { ApiClientError } from '@/lib/auth-api';

export default function AdminLayout({ children }: Readonly<{ children: ReactNode }>) {
  const pathname = usePathname();
  const router = useRouter();
  const query = useCurrentUser();

  useEffect(() => {
    if (
      query.data === null ||
      (query.error instanceof ApiClientError && query.error.status === 401)
    ) {
      router.replace(`/login?return_to=${encodeURIComponent(pathname || '/admin')}`);
    } else if (query.data?.user.must_change_password) {
      router.replace('/change-password');
    }
  }, [pathname, query.data, query.error, router]);

  if (query.error && !(query.error instanceof ApiClientError && query.error.status === 401)) {
    return (
      <main className="jx-page-grid jx-page-viewport grid place-items-center px-6">
        <section className="jx-glass max-w-md rounded-3xl p-8 text-center">
          <p className="jx-kicker">CONNECTION ERROR</p>
          <h1 className="mt-3 text-2xl font-black text-slate-950">无法确认管理权限</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            账号服务暂时不可用，请检查网络后重试。系统不会在权限状态未知时进入后台。
          </p>
          <AdminRefreshButton
            className="mt-6"
            label="重新检查"
            onRefresh={() => query.refetch()}
            tone="primary"
          />
        </section>
      </main>
    );
  }

  if (query.isLoading || !query.data || query.data.user.must_change_password) {
    return <AuthLoading label="正在确认管理权限" />;
  }

  if (query.data.user.role !== 'ADMIN') {
    return (
      <main className="jx-page-grid jx-page-viewport grid place-items-center px-6">
        <section className="jx-glass max-w-md rounded-3xl p-8 text-center">
          <p className="jx-kicker">ACCESS DENIED</p>
          <h1 className="mt-3 text-2xl font-black text-slate-950">需要管理员权限</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            当前账号可以参加和观看比赛，但不能进入管理后台。
          </p>
          <Link
            className="mt-6 inline-flex min-h-11 items-center rounded-xl bg-blue-600 px-5 text-sm font-bold text-white"
            href="/"
          >
            返回首页
          </Link>
        </section>
      </main>
    );
  }

  return <AdminShell user={query.data.user}>{children}</AdminShell>;
}
