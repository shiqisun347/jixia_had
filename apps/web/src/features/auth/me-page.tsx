'use client';

import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Camera, Clock3, Gauge, Medal, Settings, Swords, Trophy } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useState } from 'react';

import { AuthLoading } from './auth-loading';
import { useCurrentUser } from './use-auth';
import { ApiClientError, avatarUrl } from '@/lib/auth-api';
import { requestJson } from '@/lib/auth-api';
import { ProtectedUserPage } from './protected-user-page';
import { ProfileDialog } from './profile-dialog';

type Summary = {
  current_match: {
    match_id: string | null;
    room_id: string;
    title: string;
    status: string;
    code: string;
  } | null;
  matches: number;
  finished_matches: number;
  wins: number;
  average_score: number;
  leaderboard_rank: number | null;
  recent_matches: {
    id: string;
    title: string;
    status: string;
    created_at: string;
    side: string | null;
  }[];
  latest_device_check: string | null;
};

function MeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const userQuery = useCurrentUser();
  const summaryQuery = useQuery({
    queryKey: ['user-summary'],
    queryFn: () => requestJson<Summary>('/api/users/me/summary'),
  });
  const [profileOpen, setProfileOpen] = useState(() => searchParams.get('edit') === 'profile');
  if (userQuery.isLoading || !userQuery.data || summaryQuery.isLoading || !summaryQuery.data)
    return <AuthLoading label="正在加载你的页面" />;
  if (userQuery.error instanceof ApiClientError && userQuery.error.status === 401) return null;
  const user = userQuery.data.user;
  const summary = summaryQuery.data;
  const metrics = [
    { icon: Swords, label: '参赛次数', value: summary.matches },
    { icon: Trophy, label: '完赛次数', value: summary.finished_matches },
    { icon: Medal, label: '胜场', value: summary.wins },
    { icon: Gauge, label: '平均评分', value: summary.average_score.toFixed(1) },
  ];
  return (
    <main className="jx-page-viewport bg-[#f7faff] px-6 py-10 text-slate-950 xl:px-10">
      <div className="mx-auto max-w-[1200px]">
        <section className="flex flex-wrap items-center justify-between gap-6 rounded-[2rem] border border-blue-100 bg-white p-8 shadow-[0_24px_70px_rgba(31,71,128,0.10)] md:p-10">
          <div className="flex items-center gap-5">
            <button
              aria-label="修改头像"
              className="group relative rounded-full focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-200"
              onClick={() => setProfileOpen(true)}
              type="button"
            >
              <Image
                className="jx-identity-avatar size-24 rounded-full border-4 border-white object-cover shadow-lg"
                src={avatarUrl(user)}
                alt={`${user.real_name}的头像`}
                width={96}
                height={96}
                unoptimized
              />
              <span className="absolute -bottom-1 -right-1 grid size-8 place-items-center rounded-full border-4 border-white bg-lime-300 text-slate-950 transition-transform group-hover:scale-105">
                <Camera className="size-3.5" />
              </span>
            </button>
            <div>
              <p className="jx-kicker">MY SPACE</p>
              <h1 className="mt-2 text-3xl font-black tracking-[-0.04em]">{user.real_name}</h1>
              <p className="mt-2 text-sm text-slate-500">@{user.username} · 辩手</p>
            </div>
          </div>
          <div className="flex gap-3">
            <button
              className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-blue-200 bg-white px-4 py-3 text-sm font-black text-blue-700 hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
              onClick={() => setProfileOpen(true)}
              type="button"
            >
              <Settings className="size-4" aria-hidden="true" /> 编辑资料
            </button>
            {summary.current_match ? (
              <button
                className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-4 py-3 text-sm font-black text-white shadow-lg shadow-blue-200 hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-200"
                onClick={() => router.push(`/rooms/${summary.current_match?.room_id}`)}
                type="button"
              >
                返回比赛 <ArrowRight className="size-4" />
              </button>
            ) : null}
          </div>
        </section>
        {summary.current_match ? (
          <section className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-lime-200 bg-lime-50 px-6 py-5">
            <div>
              <p className="text-xs font-black tracking-[0.15em] text-lime-800">CURRENT MATCH</p>
              <p className="mt-1 font-black text-slate-950">{summary.current_match.title}</p>
              <p className="mt-1 text-sm text-slate-600">
                房间号 {summary.current_match.code} ·{' '}
                {summary.current_match.status === 'PAUSED' ? '暂时暂停' : '正在进行'}
              </p>
            </div>
            <span className="rounded-full bg-lime-200 px-3 py-1.5 text-xs font-black text-lime-900">
              请先完成当前比赛
            </span>
          </section>
        ) : null}
        <section className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          {metrics.map(({ icon: Icon, label, value }) => (
            <div key={label} className="rounded-2xl border border-blue-100 bg-white p-5 shadow-sm">
              <Icon className="size-5 text-blue-600" />
              <p className="mt-5 text-2xl font-black tabular-nums">{value}</p>
              <p className="mt-1 text-xs font-bold text-slate-500">{label}</p>
            </div>
          ))}
        </section>
        <section className="mt-6 grid gap-6 lg:grid-cols-[1.25fr_.75fr]">
          <div className="rounded-[2rem] border border-blue-100 bg-white p-8 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <p className="jx-kicker">RECENT MATCHES</p>
                <h2 className="mt-2 text-2xl font-black">最近比赛</h2>
              </div>
              <Link className="text-sm font-black text-blue-700 hover:underline" href="/lobby">
                参加新比赛
              </Link>
            </div>
            {summary.recent_matches.length ? (
              <div className="mt-6 divide-y divide-slate-100">
                {summary.recent_matches.map((match) => (
                  <Link
                    className="flex items-center justify-between gap-3 py-4 first:pt-0 last:pb-0 hover:bg-blue-50/40"
                    key={match.id}
                    href={`/matches/${match.id}`}
                  >
                    <span className="min-w-0">
                      <strong className="block truncate text-sm font-black">{match.title}</strong>
                      <small className="mt-1 block text-xs text-slate-500">
                        {new Date(match.created_at).toLocaleDateString('zh-CN')} ·{' '}
                        {match.side === 'AFFIRMATIVE'
                          ? '正方'
                          : match.side === 'NEGATIVE'
                            ? '反方'
                            : '席位待定'}
                      </small>
                    </span>
                    <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-600">
                      {match.status === 'FINISHED'
                        ? '已结束'
                        : match.status === 'TERMINATED'
                          ? '已终止'
                          : match.status}
                    </span>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="mt-8 rounded-2xl bg-slate-50 px-5 py-8 text-center text-sm text-slate-500">
                还没有比赛记录，去大厅开始第一场辩论。
              </div>
            )}
          </div>
          <div className="grid gap-6">
            <div className="rounded-[2rem] border border-blue-100 bg-white p-8 shadow-sm">
              <p className="jx-kicker">RANKING</p>
              <h2 className="mt-2 text-2xl font-black">排行榜</h2>
              <div className="mt-6 flex items-end gap-2">
                <span className="text-4xl font-black">
                  {summary.leaderboard_rank ? `#${summary.leaderboard_rank}` : '—'}
                </span>
                <span className="pb-1 text-sm text-slate-500">每日更新</span>
              </div>
            </div>
            <div className="rounded-[2rem] border border-blue-100 bg-white p-8 shadow-sm">
              <p className="jx-kicker">DEVICE</p>
              <h2 className="mt-2 text-2xl font-black">设备状态</h2>
              <p className="mt-4 flex items-center gap-2 text-sm text-slate-600">
                <Clock3 className="size-4 text-blue-600" />
                {summary.latest_device_check
                  ? `最近检测：${new Date(summary.latest_device_check).toLocaleString('zh-CN')}`
                  : '还没有有效检测'}
              </p>
              <Link
                className="mt-5 inline-flex text-sm font-black text-blue-700 hover:underline"
                href="/lobby"
              >
                进入房间检测设备
              </Link>
            </div>
          </div>
        </section>
      </div>
      <ProfileDialog onOpenChange={setProfileOpen} open={profileOpen} user={user} />
    </main>
  );
}

export function MePageView() {
  return (
    <ProtectedUserPage returnTo="/me">
      <MeContent />
    </ProtectedUserPage>
  );
}
