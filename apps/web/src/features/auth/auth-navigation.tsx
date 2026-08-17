'use client';

import { QueryClientContext } from '@tanstack/react-query';
import { ChevronDown, LogOut, ShieldCheck, UserRound } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { Suspense, useContext, useState } from 'react';

import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { avatarUrl } from '@/lib/auth-api';
import { authHrefWithReturnTo, buildReturnTo, sanitizeAuthReturnTo } from '@/lib/return-to';

import { useCurrentUser } from './use-auth';
import { useLogout } from './use-logout';

export function AuthNavigation() {
  return (
    <Suspense fallback={<AuthNavigationLoading />}>
      <AuthNavigationContent />
    </Suspense>
  );
}

function AuthNavigationLoading() {
  return <div className="h-10 w-36 animate-pulse rounded-xl bg-slate-100" aria-hidden="true" />;
}

function AuthNavigationContent() {
  const pathname = usePathname() || '/';
  const searchParams = useSearchParams();
  const returnTo = sanitizeAuthReturnTo(buildReturnTo(pathname, searchParams?.toString() ?? ''));
  const loginHref = authHrefWithReturnTo('/login', returnTo);
  const registerHref = authHrefWithReturnTo('/register', returnTo);
  // The prototype route is also rendered in isolation by Storybook/unit tests.
  // Keep the navigation useful without making those consumers recreate the app provider.
  const queryClientContext = useContext(QueryClientContext);
  if (!queryClientContext) {
    return (
      <div className="flex items-center gap-2">
        <Link
          className="rounded-xl border border-blue-100 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 shadow-sm hover:border-blue-200"
          href={loginHref}
          prefetch={false}
        >
          登录
        </Link>
        <Link
          className="inline-flex items-center rounded-xl bg-lime-300 px-4 py-2.5 text-sm font-black text-slate-950 shadow-[0_8px_26px_rgba(183,237,0,0.28)]"
          href={registerHref}
          prefetch={false}
        >
          注册
        </Link>
      </div>
    );
  }
  return <ConnectedAuthNavigation loginHref={loginHref} registerHref={registerHref} />;
}

function ConnectedAuthNavigation({
  loginHref,
  registerHref,
}: Readonly<{ loginHref: string; registerHref: string }>) {
  const query = useCurrentUser();
  const { logout, isLoggingOut } = useLogout();
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);

  if (query.isLoading) {
    return (
      <div
        className="h-10 w-36 animate-pulse rounded-xl bg-slate-100"
        aria-label="正在检查登录状态"
        role="status"
      />
    );
  }

  if (!query.data) {
    return (
      <div
        className="flex items-center gap-2"
        title={query.isError ? '暂时无法确认登录状态，可稍后重试。' : undefined}
      >
        {query.isError ? (
          <button
            className="inline-flex min-h-10 rounded-xl border border-amber-200 bg-amber-50 px-3 text-xs font-bold text-amber-800 transition hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-amber-100 disabled:cursor-wait disabled:text-amber-500"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
            type="button"
          >
            {query.isFetching ? '重试中…' : '重试登录状态'}
          </button>
        ) : null}
        <Link
          className="rounded-xl border border-blue-100 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 shadow-sm hover:border-blue-200"
          href={loginHref}
          prefetch={false}
        >
          登录
        </Link>
        <Link
          className="inline-flex items-center rounded-xl bg-lime-300 px-4 py-2.5 text-sm font-black text-slate-950 shadow-[0_8px_26px_rgba(183,237,0,0.28)]"
          href={registerHref}
          prefetch={false}
        >
          注册
        </Link>
      </div>
    );
  }

  const user = query.data.user;
  const accountContent = (
    <>
      <Image
        className="size-8 rounded-full border border-blue-100 object-cover"
        src={avatarUrl(user)}
        alt=""
        width={32}
        height={32}
        priority
        unoptimized
      />
      <span className="max-w-28 truncate">{user.real_name}</span>
    </>
  );
  return (
    <>
      <div className="flex items-center gap-2">
        {user.role === 'ADMIN' ? (
          <details className="group relative">
            <summary
              aria-label={`打开${user.real_name}的账号菜单`}
              className="account-control cursor-pointer list-none"
              role="button"
            >
              {accountContent}
              <ChevronDown
                className="size-3.5 transition-transform group-open:rotate-180"
                aria-hidden="true"
              />
            </summary>
            <div className="absolute right-0 top-[calc(100%+.5rem)] z-50 grid min-w-48 gap-1 rounded-2xl border border-blue-100 bg-white p-2 shadow-xl">
              <Link className="account-menu-link" href="/me" prefetch={false}>
                <UserRound className="size-4" aria-hidden="true" />
                我的页面
              </Link>
              <Link className="account-menu-link" href="/admin" prefetch={false}>
                <ShieldCheck className="size-4" aria-hidden="true" />
                管理后台
              </Link>
            </div>
          </details>
        ) : (
          <Link
            aria-label={`进入${user.real_name}的个人页面`}
            className="account-control"
            href="/me"
            prefetch={false}
          >
            {accountContent}
          </Link>
        )}
        <button
          className="grid size-10 place-items-center rounded-xl border border-slate-200 bg-white text-slate-500 shadow-sm outline-none transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-950 focus-visible:ring-2 focus-visible:ring-blue-600 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
          disabled={isLoggingOut}
          onClick={() => setLogoutConfirmOpen(true)}
          type="button"
          aria-label="退出登录"
        >
          <LogOut className="size-4" aria-hidden="true" />
        </button>
      </div>
      <ConfirmDialog
        confirmLabel="退出登录"
        description="退出后需要重新输入用户名和密码才能继续使用需要登录的功能。"
        loading={isLoggingOut}
        onConfirm={() => void logout()}
        onOpenChange={setLogoutConfirmOpen}
        open={logoutConfirmOpen}
        title="确认退出登录？"
      />
    </>
  );
}
