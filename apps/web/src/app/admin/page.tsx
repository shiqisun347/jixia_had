'use client';

import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Bot,
  BrainCircuit,
  CircleAlert,
  HardDrive,
  Plus,
  Settings2,
  Volume2,
} from 'lucide-react';
import Link from 'next/link';
import type { ComponentType } from 'react';

import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import { AdminNotice, AdminRefreshButton, StatusBadge } from '@/features/admin/admin-controls';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';

export default function AdminPage() {
  const query = useQuery({
    queryKey: ['admin', 'overview'],
    queryFn: adminApi.overview,
  });

  const data = query.data;
  const activeMatches = data?.active_matches ?? 0;
  const enabledAgents = data?.enabled_agents ?? 0;
  const enabledModels = data?.enabled_models ?? 0;
  const enabledVoices = data?.enabled_voices ?? 0;
  const recentFailures = data?.recent_failures ?? [];
  const storagePercent = data ? data.storage.used_ratio * 100 : 0;

  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <div className="flex gap-2">
            <Link
              className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 text-sm font-bold text-slate-700 shadow-sm transition hover:border-blue-300 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
              href="/admin/matches"
            >
              查看比赛 <ArrowUpRight className="size-4" aria-hidden="true" />
            </Link>
            <Link
              className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-blue-600 bg-blue-600 px-3.5 text-sm font-bold text-white shadow-[0_10px_24px_rgba(37,99,235,0.2)] transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
              href="/admin/agents"
            >
              <Plus className="size-4" aria-hidden="true" /> 配置 Agent
            </Link>
          </div>
        }
        description="集中查看比赛容量、智能体目录、后台异常和存储风险，再进入对应模块处理。"
        eyebrow="OPERATIONS OVERVIEW"
        title="运行总览"
      />

      {query.error ? (
        <AdminFeedback message={readableAdminError(query.error)} tone="error" />
      ) : null}
      {query.isLoading ? <OverviewSkeleton /> : null}
      {query.error && !data ? (
        <AdminPanel title="总览暂时无法加载" description="请求失败不会影响正在进行的比赛。">
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl bg-slate-50 p-4">
            <p className="text-sm text-slate-600">请检查服务状态后重新加载管理数据。</p>
            <AdminRefreshButton label="重新加载" onRefresh={() => query.refetch()} />
          </div>
        </AdminPanel>
      ) : null}

      {data ? (
        <>
          <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              detail={`${Math.max(0, 5 - activeMatches)} 个剩余名额`}
              href="/admin/matches"
              icon={Activity}
              label="活动比赛"
              tone={activeMatches >= 5 ? 'danger' : activeMatches >= 4 ? 'warning' : 'blue'}
              value={`${activeMatches} / 5`}
            />
            <MetricCard
              detail={`${enabledAgents} 个启用配置`}
              href="/admin/agents"
              icon={Bot}
              label="启用 Agent"
              tone="blue"
              value={String(enabledAgents)}
            />
            <MetricCard
              detail={`${enabledVoices} 个启用音色`}
              href="/admin/models"
              icon={BrainCircuit}
              label="启用模型"
              tone="blue"
              value={String(enabledModels)}
            />
            <MetricCard
              detail={`${(data.storage.free_bytes / 1024 ** 3).toFixed(1)} GB 可用`}
              href="/admin/settings"
              icon={HardDrive}
              label="磁盘已用"
              tone={storagePercent >= 90 ? 'danger' : storagePercent >= 80 ? 'warning' : 'blue'}
              value={`${storagePercent.toFixed(1)}%`}
            />
          </section>

          <AdminPanel
            action={
              <span className="font-mono text-sm font-black text-slate-700">
                {activeMatches} / 5
              </span>
            }
            description="运行中和暂停中的比赛均占用容量；状态轨道是当前实时资源占用的快速视图。"
            title="实时比赛容量"
          >
            <div className="relative overflow-hidden rounded-xl border border-slate-200 bg-slate-50 px-4 py-5">
              <div
                className="grid grid-cols-5 gap-2"
                aria-label={`当前使用 ${activeMatches} 个比赛名额`}
                role="img"
              >
                {Array.from({ length: 5 }, (_, index) => (
                  <div
                    className={
                      index < activeMatches
                        ? 'h-2.5 rounded-full bg-blue-600 shadow-[0_0_16px_rgba(37,99,235,0.28)]'
                        : 'h-2.5 rounded-full border border-slate-200 bg-white'
                    }
                    key={index}
                  />
                ))}
              </div>
              <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <span>
                  {activeMatches
                    ? '当前有实时比赛，后台重任务应保持低优先级。'
                    : '当前没有活动比赛。'}
                </span>
                <Link
                  className="inline-flex items-center gap-1 font-bold text-blue-700"
                  href="/admin/matches"
                >
                  查看比赛状态 <ArrowRight className="size-3.5" aria-hidden="true" />
                </Link>
              </div>
            </div>
          </AdminPanel>

          <div className="grid gap-5 xl:grid-cols-[1.35fr_0.65fr]">
            <AdminPanel
              description="按创建时间显示最近六场，状态和入口保持可扫描。"
              title="最近比赛"
            >
              {data.recent_matches.length ? (
                <div className="overflow-hidden rounded-xl border border-slate-200">
                  <div className="grid grid-cols-[minmax(0,1fr)_8rem_2rem] gap-4 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[0.65rem] font-black tracking-wide text-slate-600 uppercase">
                    <span>比赛</span>
                    <span>状态</span>
                    <span />
                  </div>
                  <div className="divide-y divide-slate-100">
                    {data.recent_matches.map((match) => (
                      <Link
                        className="grid grid-cols-[minmax(0,1fr)_8rem_2rem] items-center gap-4 px-4 py-3 transition hover:bg-blue-50/40"
                        href={
                          ['FINISHED', 'TERMINATED'].includes(match.status)
                            ? `/matches/${match.id}`
                            : `/debate?match_id=${match.id}`
                        }
                        key={match.id}
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-black text-slate-900">
                            {match.label || '未命名比赛'}
                          </p>
                          <p className="mt-0.5 truncate text-xs text-slate-500">
                            {match.display_topic || match.id.slice(0, 8)}
                          </p>
                        </div>
                        <StatusBadge status={match.status} />
                        <ArrowUpRight className="size-4 text-slate-300" aria-hidden="true" />
                      </Link>
                    ))}
                  </div>
                </div>
              ) : (
                <AdminEmpty>暂无比赛记录，可从用户端创建房间。</AdminEmpty>
              )}
            </AdminPanel>

            <div className="space-y-5">
              <AdminPanel description="需要优先确认的异常和资源风险。" title="待处理事项">
                <div className="space-y-3">
                  {storagePercent >= 80 ? (
                    <AdminNotice tone="warning">
                      磁盘使用率已达到 {storagePercent.toFixed(1)}%，请检查音频保留和清理任务。
                    </AdminNotice>
                  ) : null}
                  {recentFailures.map((log) => (
                    <Link
                      className="flex items-start gap-3 rounded-xl border border-slate-200 p-3 transition hover:border-blue-300"
                      href="/admin/logs"
                      key={log.id}
                    >
                      <CircleAlert
                        className="mt-0.5 size-4 shrink-0 text-red-600"
                        aria-hidden="true"
                      />
                      <span className="min-w-0">
                        <b className="block truncate text-xs text-slate-800">{log.action}</b>
                        <small className="mt-1 block text-[0.68rem] text-slate-600">
                          {new Date(log.created_at).toLocaleString('zh-CN')}
                        </small>
                      </span>
                    </Link>
                  ))}
                  {!recentFailures.length && storagePercent < 80 ? (
                    <AdminNotice tone="success">当前没有需要立即处理的后台异常。</AdminNotice>
                  ) : null}
                </div>
              </AdminPanel>
              <AdminPanel description="进入最常用的配置与诊断页面。" title="快捷入口">
                <div className="grid grid-cols-2 gap-2">
                  <QuickLink href="/admin/agents" icon={Bot} label="Agent" />
                  <QuickLink href="/admin/models" icon={BrainCircuit} label="模型" />
                  <QuickLink href="/admin/voices" icon={Volume2} label="音色" />
                  <QuickLink href="/admin/settings" icon={Settings2} label="系统" />
                </div>
              </AdminPanel>
            </div>
          </div>

          <AdminNotice tone="warning">
            系统不执行自动备份。磁盘使用率达到 90% 后会阻止新比赛开始，但不会中断正在进行的比赛。
          </AdminNotice>
        </>
      ) : null}
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
  href,
  tone,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
  detail: string;
  href: string;
  tone: 'blue' | 'warning' | 'danger';
}) {
  const toneClass =
    tone === 'danger'
      ? 'bg-red-50 text-red-700'
      : tone === 'warning'
        ? 'bg-amber-50 text-amber-700'
        : 'bg-blue-50 text-blue-700';
  return (
    <Link
      className="group relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_12px_34px_rgba(15,23,42,0.045)] transition hover:-translate-y-0.5 hover:border-blue-300"
      href={href}
    >
      <span className="absolute inset-x-0 top-0 h-0.5 bg-blue-600" aria-hidden="true" />
      <div className="flex items-start justify-between gap-4">
        <span className={`grid size-10 place-items-center rounded-xl ${toneClass}`}>
          <Icon className="size-5" aria-hidden="true" />
        </span>
        <ArrowUpRight
          className="size-4 text-slate-300 transition group-hover:text-blue-600"
          aria-hidden="true"
        />
      </div>
      <p className="mt-5 font-mono text-3xl font-black tracking-[-0.05em] text-slate-950 tabular-nums">
        {value}
      </p>
      <p className="mt-1 text-xs font-black text-slate-600">{label}</p>
      <p className="mt-1 text-[0.68rem] text-slate-600">{detail}</p>
    </Link>
  );
}

function QuickLink({
  href,
  icon: Icon,
  label,
}: {
  href: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
}) {
  return (
    <Link
      className="flex min-h-16 items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 transition hover:border-blue-300 hover:bg-blue-50/40 hover:text-blue-700"
      href={href}
    >
      <span className="grid size-8 place-items-center rounded-lg bg-slate-100 text-slate-600">
        <Icon className="size-4" aria-hidden="true" />
      </span>
      {label}
    </Link>
  );
}

function OverviewSkeleton() {
  return (
    <div aria-label="正在加载运行总览" className="space-y-5" role="status">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <div
            className="h-40 animate-pulse rounded-2xl border border-slate-200 bg-white"
            key={index}
          />
        ))}
      </div>
      <div className="h-44 animate-pulse rounded-2xl border border-slate-200 bg-white" />
    </div>
  );
}
