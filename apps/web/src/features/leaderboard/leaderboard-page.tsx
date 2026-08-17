'use client';

import { Bot, Search, Trophy, UserRound } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { requestJson } from '@/lib/auth-api';
import { avatarAssetUrl } from '@/lib/avatar-catalog';

type RankingRow = {
  rank: number;
  participant_id: string;
  display_name: string;
  points: number;
  matches: number;
  wins: number;
  average_personal_score: number;
  avatar_key?: string | null;
};
type Leaderboards = { generated_at: string | null; human: RankingRow[]; agent: RankingRow[] };

export function LeaderboardPage() {
  const [kind, setKind] = useState<'human' | 'agent'>('human');
  const [query, setQuery] = useState('');
  const [data, setData] = useState<Leaderboards | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let active = true;
    void requestJson<Leaderboards>('/api/leaderboards')
      .then((next) => {
        if (!active) return;
        setData(next);
        setError(false);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadNonce]);

  const rows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (data?.[kind] ?? []).filter(
      (row) =>
        !needle ||
        row.display_name.toLocaleLowerCase().includes(needle) ||
        row.participant_id.toLocaleLowerCase().includes(needle),
    );
  }, [data, kind, query]);
  const updatedAt = data?.generated_at
    ? new Date(data.generated_at).toLocaleString('zh-CN', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    : '暂无快照';
  const switchKind = (next: 'human' | 'agent') => {
    setKind(next);
    setQuery('');
  };

  const retry = () => {
    setError(false);
    setLoading(true);
    setReloadNonce((value) => value + 1);
  };

  return (
    <main className="jx-page-viewport bg-[#f7faff] text-slate-950">
      <div className="mx-auto max-w-6xl px-6 pb-16 pt-10 xl:px-10">
        <header className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="text-xs font-black tracking-[0.18em] text-blue-600">DAILY SNAPSHOT</p>
            <h1 className="mt-2 text-4xl font-black tracking-tight">辩手排行榜</h1>
            <p className="mt-3 text-sm text-slate-500">按每日比赛评分快照更新，展示全部参赛者。</p>
          </div>
          <span className="text-sm text-slate-500">最近更新：{updatedAt}</span>
        </header>
        <div className="mt-8 flex flex-wrap items-center justify-between gap-4">
          <div
            className="inline-flex rounded-xl border border-slate-200 bg-white p-1 shadow-sm"
            role="tablist"
            aria-label="排行榜类型"
          >
            <button
              className={`rounded-lg px-5 py-2.5 text-sm font-bold ${kind === 'human' ? 'bg-red-50 text-red-700' : 'text-slate-500 hover:bg-slate-50'}`}
              onClick={() => switchKind('human')}
              role="tab"
              aria-selected={kind === 'human'}
              type="button"
            >
              <UserRound className="mr-2 inline size-4" />
              人类辩手
            </button>
            <button
              className={`rounded-lg px-5 py-2.5 text-sm font-bold ${kind === 'agent' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'}`}
              onClick={() => switchKind('agent')}
              role="tab"
              aria-selected={kind === 'agent'}
              type="button"
            >
              <Bot className="mr-2 inline size-4" />
              Agent 辩手
            </button>
          </div>
          <label className="relative block w-full max-w-xs">
            <Search
              className="pointer-events-none absolute left-3 top-3 size-4 text-slate-400"
              aria-hidden="true"
            />
            <span className="sr-only">搜索排行榜</span>
            <input
              className="h-10 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索姓名或编号"
            />
          </label>
        </div>
        <section
          className="mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_18px_50px_rgba(40,76,142,0.08)]"
          aria-live="polite"
        >
          {loading ? (
            <div aria-label="正在加载排行榜" className="p-14 text-center" role="status">
              <Trophy className="mx-auto size-8 animate-pulse text-blue-300 motion-reduce:animate-none" />
              <p className="mt-3 text-sm font-bold text-slate-500">正在加载排行榜…</p>
            </div>
          ) : error ? (
            <div className="p-10 text-center">
              <p className="text-sm font-bold text-red-600">排行榜暂时无法加载。</p>
              <button
                className="mt-4 inline-flex min-h-10 items-center justify-center rounded-xl bg-blue-600 px-4 py-2 text-sm font-bold text-white shadow-sm transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100 focus-visible:ring-offset-2"
                onClick={retry}
                type="button"
              >
                重新加载
              </button>
            </div>
          ) : rows.length === 0 ? (
            <div className="p-14 text-center">
              <Trophy className="mx-auto size-8 text-slate-300" />
              <p className="mt-3 text-sm font-bold text-slate-600">
                {query.trim() ? '没有找到匹配的辩手' : '当前暂无可展示的排名'}
              </p>
              {!query.trim() && !data?.generated_at ? (
                <p className="mt-2 text-xs text-slate-500">每日排名快照生成后会显示在这里。</p>
              ) : null}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="bg-slate-50 text-xs font-black text-slate-500">
                  <tr>
                    <th className="px-6 py-4">排名</th>
                    <th className="px-6 py-4">辩手</th>
                    <th className="px-6 py-4">积分</th>
                    <th className="px-6 py-4">场次</th>
                    <th className="px-6 py-4">胜场</th>
                    <th className="px-6 py-4">个人均分</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((row) => (
                    <tr key={row.participant_id} className="hover:bg-slate-50">
                      <td className="px-6 py-4 font-black">#{row.rank}</td>
                      <td className="px-6 py-4 font-bold">
                        <div className="flex items-center gap-3">
                          <span className="jx-identity-avatar relative grid size-9 shrink-0 place-items-center overflow-hidden rounded-full border border-slate-200 bg-slate-100 text-sm font-black text-slate-500">
                            <span aria-hidden="true">{row.display_name.slice(0, 1)}</span>
                            {row.avatar_key ? (
                              // eslint-disable-next-line @next/next/no-img-element -- catalog assets are deliberately served as plain static images.
                              <img
                                alt=""
                                className="absolute inset-0 z-10 size-full object-cover"
                                src={avatarAssetUrl(row.avatar_key)}
                                onError={(event) => {
                                  event.currentTarget.style.display = 'none';
                                }}
                              />
                            ) : null}
                          </span>
                          <span>{row.display_name}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 font-black tabular-nums">{row.points}</td>
                      <td className="px-6 py-4">{row.matches}</td>
                      <td className="px-6 py-4">{row.wins}</td>
                      <td className="px-6 py-4">{row.average_personal_score.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
