'use client';

import { useQuery } from '@tanstack/react-query';
import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Activity, Archive, Gavel, Radio, Trash2, Volume2 } from 'lucide-react';
import Link from 'next/link';
import { useDeferredValue, useEffect, useMemo, useState } from 'react';

import { useOptionalToast } from '@/components/ui/toast-provider';
import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import {
  AdminActionItem,
  AdminActionMenu,
  AdminConfirmDialog,
  AdminDrawer,
  AdminPagination,
  AdminRefreshButton,
  AdminSearch,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminDataTable } from '@/features/admin/admin-data-table';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type {
  AgentFreeDebateDecisionDiagnostic,
  AgentGenerationDiagnostic,
  MatchRow,
} from '@/features/admin/admin-types';
import { requestJson } from '@/lib/auth-api';
import { commitAdminAction } from '@/features/admin/commit-admin-action';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';
import { AdminBulkActions } from '@/features/admin/admin-bulk-actions';

type MatchAction = 'terminate' | 'judge' | 'audio' | 'retention' | 'delete';
type ActionTarget = { match: MatchRow; action: MatchAction } | null;

export default function AdminMatchesPage() {
  const toast = useOptionalToast();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('ALL');
  const [sort, setSort] = useState('created_at');
  const [page, setPage] = useState(1);
  const [target, setTarget] = useState<ActionTarget>(null);
  const [diagnosticMatch, setDiagnosticMatch] = useState<MatchRow | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const { isSubmitting, submit } = useAdminSubmit();
  const deferredQuery = useDeferredValue(query);
  const params = useMemo(
    () => ({
      page,
      page_size: 25 as const,
      q: deferredQuery,
      status: status === 'ALL' ? '' : status,
      sort,
    }),
    [deferredQuery, page, sort, status],
  );
  const matchesQuery = useQuery({
    queryKey: ['admin', 'matches', params],
    queryFn: () => adminApi.matches(params),
  });
  const matches = matchesQuery.data?.items ?? [];
  const diagnosticsQuery = useQuery({
    queryKey: ['admin', 'matches', diagnosticMatch?.id, 'agent-generations'],
    queryFn: () => adminApi.matchGenerations(diagnosticMatch?.id ?? ''),
    enabled: Boolean(diagnosticMatch),
  });
  const decisionDiagnosticsQuery = useQuery({
    queryKey: ['admin', 'matches', diagnosticMatch?.id, 'free-debate-decisions'],
    queryFn: () => adminApi.matchFreeDebateDecisions(diagnosticMatch?.id ?? ''),
    enabled: Boolean(diagnosticMatch),
  });

  useEffect(() => {
    const url = new URL(window.location.href);
    setQuery(url.searchParams.get('q') ?? '');
    setStatus(url.searchParams.get('status') ?? 'ALL');
    setSort(url.searchParams.get('sort') ?? 'created_at');
    setPage(Number(url.searchParams.get('page') ?? 1) || 1);
  }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (query.trim()) url.searchParams.set('q', query.trim());
    else url.searchParams.delete('q');
    if (status === 'ALL') url.searchParams.delete('status');
    else url.searchParams.set('status', status);
    if (sort === 'created_at') url.searchParams.delete('sort');
    else url.searchParams.set('sort', sort);
    if (page === 1) url.searchParams.delete('page');
    else url.searchParams.set('page', String(page));
    window.history.replaceState(null, '', `${url.pathname}${url.search}`);
  }, [page, query, sort, status]);

  async function executeAction(selected = target) {
    if (!selected) return;
    const { match, action } = selected;
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(async () => {
          if (action === 'terminate')
            await requestJson(`/api/admin/matches/${match.id}/terminate`, {
              method: 'POST',
              body: '{}',
            });
          if (action === 'judge')
            await requestJson(`/api/admin/matches/${match.id}/judge-retry`, {
              method: 'POST',
              body: '{}',
            });
          if (action === 'audio')
            await requestJson(`/api/admin/matches/${match.id}/audio-retry`, {
              method: 'POST',
              body: '{}',
            });
          if (action === 'retention')
            await requestJson(`/api/admin/matches/${match.id}/files/permanent`, {
              method: 'PATCH',
              body: JSON.stringify({ permanent: !match.files_permanent }),
            });
          if (action === 'delete')
            await requestJson(`/api/admin/matches/${match.id}`, { method: 'DELETE' });
        }, matchesQuery.refetch);
      });
      if (!submitted) return;
      setTarget(null);
      const message =
        action === 'terminate'
          ? '比赛已终止。'
          : action === 'judge'
            ? '重新评分任务已开始。'
            : action === 'audio'
              ? '回放任务已排队。'
              : action === 'retention'
                ? match.files_permanent
                  ? '已恢复默认保留期。'
                  : '比赛音频已永久保留。'
                : '比赛数据已删除。';
      toast?.showToast({ message, tone: 'success' });
      if (refreshResult.value === 'refresh_failed') {
        toast?.showToast({
          message: '操作已完成，但比赛列表未刷新；请稍后手动刷新。',
          tone: 'info',
        });
      }
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }

  const columns = useMemo<ColumnDef<MatchRow>[]>(
    () => [
      {
        id: 'select',
        header: '选择',
        cell: ({ row }) => (
          <input
            aria-label={`选择 ${row.original.label || '比赛'}`}
            checked={selectedIds.includes(row.original.id)}
            onChange={() =>
              setSelectedIds((current) =>
                current.includes(row.original.id)
                  ? current.filter((id) => id !== row.original.id)
                  : [...current, row.original.id],
              )
            }
            type="checkbox"
          />
        ),
      },
      {
        accessorKey: 'label',
        header: '比赛',
        cell: ({ row }) => {
          return (
            <div className="min-w-64">
              <Link
                className="font-black text-slate-950 hover:text-blue-700"
                href={`/admin/matches/${row.original.id}`}
              >
                {row.original.label || '未命名比赛'}
              </Link>
              <p className="mt-1 truncate text-xs text-slate-600">
                {row.original.display_topic || '未记录辩题'}
              </p>
            </div>
          );
        },
      },
      {
        accessorKey: 'status',
        header: '状态',
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        accessorKey: 'created_at',
        header: '创建时间',
        cell: ({ row }) => (
          <span className="text-xs text-slate-600">
            {new Date(row.original.created_at).toLocaleString('zh-CN')}
          </span>
        ),
      },
      {
        id: 'files',
        header: '数据',
        cell: ({ row }) => (
          <span className="text-xs text-slate-600">
            上下文 v{row.original.context_version} · 文件 {row.original.file_count}
            {row.original.files_permanent ? ' · 永久' : ''}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '操作',
        cell: ({ row }) => {
          const match = row.original;
          const terminal = ['FINISHED', 'TERMINATED'].includes(match.status);
          return (
            <div className="flex justify-end">
              <AdminActionMenu>
                <AdminActionItem
                  onSelect={() => window.location.assign(`/admin/matches/${match.id}`)}
                >
                  <Activity className="mr-2 size-3.5" aria-hidden="true" />
                  打开比赛工作台
                </AdminActionItem>
                <AdminActionItem onSelect={() => setDiagnosticMatch(match)}>
                  <Activity className="mr-2 size-3.5" aria-hidden="true" />
                  模型诊断
                </AdminActionItem>
                {!terminal ? (
                  <AdminActionItem
                    onSelect={() => setTarget({ match, action: 'terminate' })}
                    tone="danger"
                  >
                    <Radio className="mr-2 size-3.5" aria-hidden="true" />
                    终止比赛
                  </AdminActionItem>
                ) : null}
                {match.status === 'FINISHED' ? (
                  <AdminActionItem onSelect={() => setTarget({ match, action: 'judge' })}>
                    <Gavel className="mr-2 size-3.5" aria-hidden="true" />
                    重新评分
                  </AdminActionItem>
                ) : null}
                {terminal ? (
                  <AdminActionItem onSelect={() => setTarget({ match, action: 'audio' })}>
                    <Volume2 className="mr-2 size-3.5" aria-hidden="true" />
                    生成/重试回放
                  </AdminActionItem>
                ) : null}
                {terminal && match.file_count > 0 ? (
                  <AdminActionItem onSelect={() => setTarget({ match, action: 'retention' })}>
                    <Archive className="mr-2 size-3.5" aria-hidden="true" />
                    {match.files_permanent ? '恢复保留期' : '永久保留音频'}
                  </AdminActionItem>
                ) : null}
                {terminal ? (
                  <AdminActionItem
                    onSelect={() => setTarget({ match, action: 'delete' })}
                    tone="danger"
                  >
                    <Trash2 className="mr-2 size-3.5" aria-hidden="true" />
                    永久删除
                  </AdminActionItem>
                ) : null}
              </AdminActionMenu>
            </div>
          );
        },
      },
    ],
    [selectedIds],
  );
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ data: matches, columns, getCoreRowModel: getCoreRowModel() });

  const confirmation = target ? actionCopy(target) : null;
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={<AdminRefreshButton onRefresh={() => matchesQuery.refetch()} />}
        description="活动比赛进入实时控制；终态比赛查看文字、评分、回放和保留状态。"
        eyebrow="MATCH OPERATIONS"
        title="比赛与数据"
      />
      {matchesQuery.error ? (
        <AdminFeedback message={readableAdminError(matchesQuery.error)} tone="error" />
      ) : null}
      <AdminPanel
        title="比赛列表"
        description={`${matchesQuery.data?.total ?? 0} 场比赛 · 服务端分页`}
      >
        <div className="mb-4 flex flex-wrap gap-2">
          <AdminSearch
            label="搜索比赛标签、辩题或 ID"
            onChange={(event) => {
              setPage(1);
              setQuery(event.target.value);
            }}
            placeholder="搜索比赛标签、辩题或 ID"
            value={query}
          />
          <AdminSelect
            label="筛选比赛状态"
            onChange={(event) => {
              setPage(1);
              setStatus(event.target.value);
            }}
            value={status}
          >
            <option value="ALL">全部状态</option>
            <option value="RUNNING">进行中</option>
            <option value="PAUSED">已暂停</option>
            <option value="FINISHED">已结束</option>
            <option value="TERMINATED">已终止</option>
          </AdminSelect>
          <AdminSelect
            label="比赛排序"
            onChange={(event) => {
              setPage(1);
              setSort(event.target.value);
            }}
            value={sort}
          >
            <option value="created_at">创建时间</option>
            <option value="ended_at">结束时间</option>
            <option value="status">状态</option>
          </AdminSelect>
        </div>
        <AdminBulkActions
          ids={selectedIds}
          onClear={() => setSelectedIds([])}
          onCompleted={() => matchesQuery.refetch()}
          resource="match"
        />
        {matchesQuery.isLoading ? (
          <div
            aria-label="正在加载比赛"
            className="h-56 animate-pulse rounded-xl bg-slate-50"
            role="status"
          />
        ) : matches.length ? (
          <AdminDataTable emptyTitle="暂无比赛" table={table} />
        ) : (
          <AdminEmpty>没有符合筛选条件的比赛。</AdminEmpty>
        )}
        {matchesQuery.data ? (
          <AdminPagination
            onPageChange={setPage}
            page={matchesQuery.data.page}
            total={matchesQuery.data.total}
            totalPages={matchesQuery.data.total_pages}
          />
        ) : null}
      </AdminPanel>
      <AdminConfirmDialog
        confirmLabel={confirmation?.confirmLabel ?? '确认'}
        description={confirmation?.description ?? ''}
        loading={isSubmitting}
        onConfirm={() => void executeAction(target)}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setTarget(null);
        }}
        open={Boolean(target)}
        title={confirmation?.title ?? '确认操作？'}
      />
      <AdminDrawer
        description="查看正式发言生成与自由辩论快速决策；包含脱敏结果、Token 与延迟，不包含密钥或供应商原始响应。"
        onOpenChange={(open) => {
          if (!open) setDiagnosticMatch(null);
        }}
        open={Boolean(diagnosticMatch)}
        title={`模型诊断${diagnosticMatch?.label ? ` · ${diagnosticMatch.label}` : ''}`}
      >
        {diagnosticsQuery.isLoading || decisionDiagnosticsQuery.isLoading ? (
          <div
            className="h-48 animate-pulse rounded-xl bg-slate-100"
            role="status"
            aria-label="正在加载模型诊断"
          />
        ) : diagnosticsQuery.error || decisionDiagnosticsQuery.error ? (
          <AdminFeedback
            message={readableAdminError(diagnosticsQuery.error ?? decisionDiagnosticsQuery.error)}
            tone="error"
          />
        ) : diagnosticsQuery.data?.length || decisionDiagnosticsQuery.data?.length ? (
          <div className="space-y-4">
            {decisionDiagnosticsQuery.data?.length ? (
              <section className="space-y-3">
                <h3 className="text-sm font-black text-slate-950">自由辩论快速决策</h3>
                {decisionDiagnosticsQuery.data.map((decision) => (
                  <FreeDecisionDiagnostic decision={decision} key={decision.id} />
                ))}
              </section>
            ) : null}
            {diagnosticsQuery.data?.length ? (
              <h3 className="pt-2 text-sm font-black text-slate-950">正式发言生成</h3>
            ) : null}
            {diagnosticsQuery.data?.map((generation) => (
              <GenerationDiagnostic
                key={generation.id}
                generation={generation}
                matchId={diagnosticMatch?.id ?? ''}
              />
            ))}
          </div>
        ) : (
          <AdminEmpty>这场比赛还没有 Agent 模型调用。</AdminEmpty>
        )}
      </AdminDrawer>
    </div>
  );
}

function FreeDecisionDiagnostic({ decision }: { decision: AgentFreeDebateDecisionDiagnostic }) {
  const side = decision.side === 'AFFIRMATIVE' ? '正方' : '反方';
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-black text-slate-950">{decision.agent_name}</p>
          <p className="mt-1 text-xs font-semibold text-slate-500">
            {side}
            {decision.seat_no}辩 · {decision.action_key} · 第 {decision.attempt_no} 次尝试
          </p>
        </div>
        <StatusBadge status={decision.selected ? 'SELECTED' : decision.status} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <Metric
          label="是否举手"
          value={decision.should_speak === null ? '调用失败' : decision.should_speak ? '是' : '否'}
        />
        <Metric
          label="意愿值"
          value={decision.willingness === null ? '—' : decision.willingness.toFixed(2)}
        />
        <Metric label="决策耗时" value={formatMetric(decision.duration_ms, ' ms')} />
      </dl>
      <p className="mt-3 text-xs font-semibold text-slate-600">
        {decision.final_queue_rank
          ? `最终队列第 ${decision.final_queue_rank} 名`
          : '未进入举手队列'}
        {decision.fallback ? ' · 系统兜底' : ''}
        {decision.human_hand_at_lock ? ' · 锁定时已有真人举手' : ''}
      </p>
      {decision.error_code ? (
        <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 font-mono text-xs text-amber-800">
          {decision.error_code}
        </p>
      ) : null}
    </article>
  );
}

function GenerationDiagnostic({
  generation,
  matchId,
}: {
  generation: AgentGenerationDiagnostic;
  matchId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailQuery = useQuery({
    queryKey: ['admin', 'matches', matchId, 'agent-generations', generation.id],
    queryFn: () => adminApi.matchGeneration(matchId, generation.id),
    enabled: expanded,
  });
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-black text-slate-950">{generation.agent_name}</p>
          <p className="mt-1 font-mono text-xs text-slate-500">
            {generation.action_key} · attempt {generation.attempt_no} · context v
            {generation.context_version}
          </p>
        </div>
        <StatusBadge status={generation.status} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <Metric label="首 Token" value={formatMetric(generation.first_token_latency_ms, ' ms')} />
        <Metric label="完整响应" value={formatMetric(generation.completed_latency_ms, ' ms')} />
        <Metric label="输出 Token" value={formatMetric(generation.completion_tokens)} />
      </dl>
      {generation.error_code ? (
        <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 font-mono text-xs text-amber-800">
          {generation.error_code}
        </p>
      ) : null}
      <button
        className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-black text-blue-800 transition hover:bg-blue-100"
        type="button"
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? '收起输入与草稿' : '查看输入与草稿'}
      </button>
      {expanded ? (
        detailQuery.isLoading ? (
          <div
            className="mt-3 h-24 animate-pulse rounded-lg bg-slate-100"
            role="status"
            aria-label="正在加载调用详情"
          />
        ) : detailQuery.error ? (
          <AdminFeedback message={readableAdminError(detailQuery.error)} tone="error" />
        ) : detailQuery.data ? (
          <div className="mt-3 space-y-2">
            <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <summary className="cursor-pointer text-xs font-black text-slate-700">
                脱敏输入快照
              </summary>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">
                {JSON.stringify(detailQuery.data.input_snapshot, null, 2)}
              </pre>
            </details>
            <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <summary className="cursor-pointer text-xs font-black text-slate-700">
                LLM 正式草稿
              </summary>
              <p className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words text-sm leading-6 text-slate-700">
                {detailQuery.data.llm_draft_text || '尚未产生完整草稿'}
              </p>
            </details>
          </div>
        ) : null
      ) : null}
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2.5">
      <dt className="text-slate-500">{label}</dt>
      <dd className="mt-1 font-mono font-bold text-slate-900">{value}</dd>
    </div>
  );
}

function formatMetric(value: number | null, suffix = ''): string {
  return value === null ? '—' : `${value}${suffix}`;
}

function actionCopy(target: NonNullable<ActionTarget>) {
  const name = target.match.label || '该比赛';
  if (target.action === 'terminate')
    return {
      title: '终止比赛？',
      confirmLabel: '确认终止',
      description: `${name} 终止后不可恢复，但已完成的数据会保留。`,
    };
  if (target.action === 'judge')
    return {
      title: '重新评分？',
      confirmLabel: '确认重试',
      description: `${name} 将按截止当前时刻的文字版本重新评分。`,
    };
  if (target.action === 'audio')
    return {
      title: '重新生成回放？',
      confirmLabel: '确认排队',
      description: `${name} 的整场回放任务将重新排队。`,
    };
  if (target.action === 'retention')
    return {
      title: target.match.files_permanent ? '恢复默认保留期？' : '永久保留音频？',
      confirmLabel: '确认修改',
      description: `仅修改 ${name} 的音频保留策略，不改变比赛文字和评分。`,
    };
  return {
    title: '永久删除比赛？',
    confirmLabel: '确认永久删除',
    description: `${name} 的文字、评分、音频和房间数据将被删除；审计日志仍保留。`,
  };
}
