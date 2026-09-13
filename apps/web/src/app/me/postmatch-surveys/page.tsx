'use client';

import { LoaderCircle } from 'lucide-react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { surveyApi } from '@/lib/experiments-api';
import { LocalizedTextBoundary } from '@/i18n/localized-text';

function PostmatchList() {
  const query = useQuery({ queryKey: ['survey', 'postmatch'], queryFn: surveyApi.postmatch });
  const tasks = query.data ?? [];
  return (
    <LocalizedTextBoundary><main className="jx-page-viewport bg-[#f7faff] px-4 py-8 text-slate-950 md:px-8">
      <section className="mx-auto max-w-4xl">
        <Link className="text-sm font-bold text-blue-700" href="/me">
          返回我的页面
        </Link>
        <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-black uppercase tracking-[0.18em] text-blue-700">
              POST-MATCH REVIEW
            </p>
            <h1 className="mt-2 text-3xl font-black tracking-tight">赛后发言问卷</h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              每场比赛独立审阅。完成本场问卷后，下一场比赛资格自动解除。
            </p>
          </div>
          <Link className="text-sm font-bold text-blue-700 underline" href="/me/ai-experience">
            填写独立 AI 辩论体验问卷
          </Link>
        </div>
        {query.isLoading ? (
          <div className="mt-10 flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="size-4 animate-spin" />
            正在加载问卷…
          </div>
        ) : null}
        {query.isError ? (
          <div className="mt-8 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            问卷加载失败，请刷新页面重试。
          </div>
        ) : null}
        {!query.isLoading && !tasks.length ? (
          <div className="mt-8 rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
            暂无单场赛后问卷。
          </div>
        ) : null}
        <div className="mt-8 grid gap-4">
          {tasks.map((task) => {
            const items = task.items ?? [];
            const targets = items.filter((item) => item.annotatable);
            const answers = task.answers?.speeches as
              Record<string, { q1?: unknown; q2?: unknown }> | undefined;
            const completed = targets.filter(
              (item) =>
                answers?.[item.speech_id]?.q1 != null && answers?.[item.speech_id]?.q2 != null,
            ).length;
            const confirmedTotal = task.confirmed_total ?? completed;
            const targetTotal = task.target_total ?? targets.length;
            const done = task.status === 'SUBMITTED';
            return (
              <Link
                key={task.id}
                href={`/me/postmatch-surveys/${task.id}`}
                className="group rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-blue-300 hover:shadow-md"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-bold text-slate-500">
                      {task.room_label || '正式比赛'}
                    </p>
                    <h2 className="mt-1 text-xl font-black">
                      {task.room_title || `比赛 ${task.match_id.slice(0, 8)}`}
                    </h2>
                    {task.topic ? (
                      <p className="mt-2 text-sm text-slate-600">辩题：{task.topic}</p>
                    ) : null}
                  </div>
                  <span
                    className={`rounded-md px-3 py-1.5 text-xs font-black ${done ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}
                  >
                    {done ? '已完成' : '待完成'}
                  </span>
                </div>
                <div className="mt-5 flex items-center gap-3">
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-blue-600"
                      style={{
                        width: `${targetTotal ? (confirmedTotal / targetTotal) * 100 : 100}%`,
                      }}
                    />
                  </div>
                  <span className="text-xs font-bold text-slate-500">
                    {confirmedTotal}/{targetTotal} 条
                  </span>
                </div>
                <p className="mt-4 text-sm font-bold text-blue-700 group-hover:underline">
                  {done ? '查看并修改标注 →' : '继续填写本场问卷 →'}
                </p>
              </Link>
            );
          })}
        </div>
      </section>
    </main></LocalizedTextBoundary>
  );
}

export default function PostmatchSurveysPage() {
  return (
    <ProtectedUserPage returnTo="/me/postmatch-surveys">
      <PostmatchList />
    </ProtectedUserPage>
  );
}
