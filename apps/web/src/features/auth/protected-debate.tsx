'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect } from 'react';

import { LiveMatchPage } from '@/features/debate';
import { ApiClientError } from '@/lib/auth-api';

import { AuthLoading } from './auth-loading';
import { useCurrentUser } from './use-auth';

export function ProtectedDebate() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const query = useCurrentUser();
  const queryString = searchParams.toString();
  const returnTo = `${pathname || '/debate'}${queryString ? `?${queryString}` : ''}`;
  useEffect(() => {
    if (
      query.data === null ||
      (query.error instanceof ApiClientError && query.error.status === 401)
    ) {
      router.replace(`/login?return_to=${encodeURIComponent(returnTo)}`);
    } else if (query.data?.user.must_change_password) {
      router.replace(`/change-password?return_to=${encodeURIComponent(returnTo)}`);
    }
  }, [query.data, query.error, returnTo, router]);
  if (query.isLoading || !query.data || query.data.user.must_change_password)
    return <AuthLoading />;
  const matchId = searchParams.get('match_id');
  if (matchId) return <LiveMatchPage matchId={matchId} />;
  return (
    <main className="jx-page-grid jx-page-viewport grid place-items-center px-6">
      <section className="max-w-md rounded-[1.75rem] border border-blue-100 bg-white/90 p-8 text-center shadow-[0_24px_70px_rgba(40,76,142,0.12)]">
        <p className="text-xs font-black tracking-[0.16em] text-blue-600">MATCH REQUIRED</p>
        <h1 className="mt-3 text-2xl font-black text-slate-950">请选择一场比赛</h1>
        <p className="mt-3 text-sm leading-7 text-slate-600">
          当前链接没有比赛编号。请从公开大厅进入准备中的房间或正在进行的比赛。
        </p>
        <Link
          className="mt-6 inline-flex min-h-11 items-center justify-center rounded-xl bg-slate-950 px-5 py-2.5 text-sm font-black !text-white"
          href="/lobby"
        >
          返回公开大厅
        </Link>
      </section>
    </main>
  );
}
