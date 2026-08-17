'use client';

import { CheckCircle2, Download, FileArchive, RefreshCcw, XCircle } from 'lucide-react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { AdminButton, AdminConfirmDialog, StatusBadge } from './admin-controls';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from './admin-ui';
import type { ExternalCallRow, MatchWorkbenchOverview, WorkbenchTimelineItem } from './admin-types';
import { adminApi, readableAdminError } from './admin-api';

type Tab = 'overview' | 'participants' | 'transcript' | 'timeline' | 'events' | 'calls' | 'export';

export function AdminMatchWorkbench({ matchId }: Readonly<{ matchId: string }>) {
  const [tab, setTab] = useState<Tab>('overview');
  const [includeAudio, setIncludeAudio] = useState(false);
  const [exportId, setExportId] = useState<string | null>(null);
  const overview = useQuery({
    queryKey: ['admin', 'workbench', matchId, 'overview'],
    queryFn: () => adminApi.matchWorkbenchOverview(matchId),
  });
  const participants = useQuery({
    queryKey: ['admin', 'workbench', matchId, 'participants'],
    queryFn: () => adminApi.matchWorkbenchParticipants(matchId),
    enabled: tab === 'participants',
  });
  const transcript = useQuery({
    queryKey: ['admin', 'workbench', matchId, 'transcript'],
    queryFn: () => adminApi.matchWorkbenchTranscript(matchId),
    enabled: tab === 'transcript',
  });
  const events = useQuery({
    queryKey: ['admin', 'workbench', matchId, 'events'],
    queryFn: () => adminApi.matchWorkbenchEvents(matchId),
    enabled: tab === 'events',
  });
  const calls = useQuery({
    queryKey: ['admin', 'workbench', matchId, 'calls'],
    queryFn: () => adminApi.matchWorkbenchCalls(matchId),
    enabled: tab === 'calls',
  });
  const timeline = useQuery({
    queryKey: ['admin', 'workbench', matchId, 'timeline'],
    queryFn: () => adminApi.matchWorkbenchTimeline(matchId),
    enabled: tab === 'timeline',
  });
  const exportStatus = useQuery({
    queryKey: ['admin', 'export', exportId],
    queryFn: () => adminApi.exportStatus(exportId ?? ''),
    enabled: Boolean(exportId),
    refetchInterval: (query) =>
      query.state.data?.status === 'QUEUED' || query.state.data?.status === 'RUNNING'
        ? 2000
        : false,
  });
  const data = overview.data;
  const match = data?.match;
  if (overview.isLoading)
    return <div className="p-8 text-sm font-bold text-slate-500">正在加载比赛工作台…</div>;
  if (overview.error || !match)
    return <AdminFeedback message={readableAdminError(overview.error)} tone="error" />;
  const tabs: Array<[Tab, string]> = [
    ['overview', '概览'],
    ['participants', '参赛者 / 席位'],
    ['transcript', '文字记录'],
    ['timeline', '运行时间线'],
    ['events', '原始事件'],
    ['calls', '请求日志'],
    ['export', '导出'],
  ];
  return (
    <div className="space-y-6">
      <AdminPageHeader
        eyebrow="MATCH WORKBENCH"
        title={match.label || '比赛工作台'}
        description={`${match.status} · sequence ${match.sequence} · context v${match.context_version}`}
        actions={
          <AdminButton onClick={() => void overview.refetch()}>
            <RefreshCcw className="size-4" />
            刷新
          </AdminButton>
        }
      />
      <div className="flex flex-wrap gap-2 border-b border-slate-200 pb-2">
        {tabs.map(([key, label]) => (
          <button
            className={`rounded-lg border px-3 py-2 text-sm font-black transition ${tab === key ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 bg-white text-slate-700 hover:border-blue-300'}`}
            key={key}
            onClick={() => setTab(key)}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' ? <Overview match={match} counts={data.counts} /> : null}
      {tab === 'participants' ? <JsonTable query={participants} empty="没有参赛席位记录" /> : null}
      {tab === 'transcript' ? <JsonTable query={transcript} empty="没有文字记录" /> : null}
      {tab === 'timeline' ? <TimelineTable query={timeline} /> : null}
      {tab === 'events' ? <JsonTable query={events} empty="没有比赛事件" /> : null}
      {tab === 'calls' ? <CallTable query={calls} /> : null}
      {tab === 'export' ? (
        <ExportPanel
          matchId={matchId}
          includeAudio={includeAudio}
          setIncludeAudio={setIncludeAudio}
          exportId={exportId}
          setExportId={setExportId}
          exportStatus={exportStatus.data}
        />
      ) : null}
    </div>
  );
}

function TimelineTable({
  query,
}: {
  query: { data?: { items: WorkbenchTimelineItem[] }; isLoading: boolean; error: unknown };
}) {
  if (query.isLoading)
    return <div className="h-48 animate-pulse rounded-xl bg-slate-100" role="status" />;
  if (query.error) return <AdminFeedback message={readableAdminError(query.error)} tone="error" />;
  const items = query.data?.items ?? [];
  if (!items.length) return <AdminEmpty>还没有可展示的运行时间线。</AdminEmpty>;
  return (
    <AdminPanel
      title="运行时间线"
      description="按服务端记录时间展示比赛、发言和外部调用；前端刷新不会改变顺序"
    >
      <div className="space-y-3">
        {items.map((item) => (
          <article
            className="flex gap-3 rounded-xl border border-slate-200 bg-white p-4"
            key={item.id}
          >
            <div className="mt-1 shrink-0">
              {item.status === 'FAILED' ? (
                <XCircle className="size-4 text-red-600" />
              ) : (
                <CheckCircle2 className="size-4 text-emerald-600" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-black text-slate-950">{item.title}</p>
                <time className="text-xs font-semibold text-slate-500">
                  {new Date(item.at).toLocaleString('zh-CN')}
                </time>
              </div>
              <p className="mt-1 text-sm text-slate-600">{item.description}</p>
              <p className="mt-2 text-xs font-bold text-slate-500">
                {item.type_label} · {item.status}
              </p>
            </div>
          </article>
        ))}
      </div>
    </AdminPanel>
  );
}

function CallTable({
  query,
}: {
  query: { data?: { items: ExternalCallRow[] }; isLoading: boolean; error: unknown };
}) {
  const [kind, setKind] = useState('ALL');
  const [expanded, setExpanded] = useState<string | null>(null);
  if (query.isLoading)
    return <div className="h-48 animate-pulse rounded-xl bg-slate-100" role="status" />;
  if (query.error) return <AdminFeedback message={readableAdminError(query.error)} tone="error" />;
  const items = (query.data?.items ?? []).filter((item) => kind === 'ALL' || item.kind === kind);
  const kinds = Array.from(new Set((query.data?.items ?? []).map((item) => item.kind)));
  return (
    <AdminPanel
      title="外部请求日志"
      description="列表只显示摘要；完整 Prompt 与回复按单条请求展开，并已在服务端脱敏"
    >
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="text-xs font-black text-slate-600" htmlFor="call-kind-filter">
          调用类型
        </label>
        <select
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800"
          id="call-kind-filter"
          onChange={(event) => setKind(event.target.value)}
          value={kind}
        >
          <option value="ALL">全部</option>
          {kinds.map((value) => (
            <option key={value} value={value}>
              {query.data?.items.find((item) => item.kind === value)?.kind_label ?? value}
            </option>
          ))}
        </select>
        <span className="text-xs font-semibold text-slate-500">共 {items.length} 条</span>
      </div>
      <div className="space-y-3">
        {items.map((item) => (
          <CallCard
            expanded={expanded === item.id}
            item={item}
            key={item.id}
            onToggle={() => setExpanded((current) => (current === item.id ? null : item.id))}
          />
        ))}
      </div>
      {!items.length ? <AdminEmpty>没有符合筛选条件的外部调用。</AdminEmpty> : null}
    </AdminPanel>
  );
}

function CallCard({
  item,
  expanded,
  onToggle,
}: {
  item: ExternalCallRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const detail = useQuery({
    queryKey: ['admin', 'external-call', item.id],
    queryFn: () => adminApi.externalCall(item.id),
    enabled: expanded,
  });
  return (
    <article
      className={`rounded-xl border p-4 ${item.status === 'FAILED' ? 'border-red-200 bg-red-50/40' : 'border-slate-200 bg-white'}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-black text-slate-950">{item.kind_label}</p>
          <p className="mt-1 text-xs font-semibold text-slate-500">
            {item.provider} · {item.operation} · 第 {item.attempt_no} 次尝试
          </p>
        </div>
        <StatusBadge status={item.status} />
      </div>
      <dl className="mt-3 grid gap-2 sm:grid-cols-4">
        <Metric label="发生时间" value={new Date(item.started_at).toLocaleTimeString('zh-CN')} />
        <Metric label="首结果" value={formatMetric(item.first_result_latency_ms, ' ms')} />
        <Metric label="总耗时" value={formatMetric(item.completed_latency_ms, ' ms')} />
        <Metric
          label="上下文"
          value={item.context_version === null ? '未记录' : `v${item.context_version}`}
        />
      </dl>
      <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
        <p className="font-black text-slate-800">{item.explanation.what}</p>
        <p className="mt-1 text-slate-600">{item.explanation.why}</p>
        <p className="mt-1 text-slate-600">影响：{item.explanation.impact}</p>
      </div>
      <button
        className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-black text-blue-800 hover:bg-blue-100"
        onClick={onToggle}
        type="button"
      >
        {expanded ? '收起完整请求与回复' : '查看完整请求与回复'}
      </button>
      {expanded ? <CallDetail detail={detail} /> : null}
    </article>
  );
}

function CallDetail({
  detail,
}: {
  detail: {
    data?: {
      request: unknown;
      response: unknown;
      content_errors: string[];
      technical: Record<string, unknown>;
    };
    isLoading: boolean;
    error: unknown;
  };
}) {
  if (detail.isLoading)
    return <div className="mt-3 h-24 animate-pulse rounded-lg bg-slate-100" role="status" />;
  if (detail.error)
    return <AdminFeedback message={readableAdminError(detail.error)} tone="error" />;
  if (!detail.data) return null;
  return (
    <div className="mt-3 space-y-2">
      <details className="rounded-lg border border-slate-200 bg-slate-50 p-3" open>
        <summary className="cursor-pointer text-xs font-black text-slate-700">
          完整请求（已脱敏）
        </summary>
        <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">
          {formatJson(detail.data.request)}
        </pre>
      </details>
      <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        <summary className="cursor-pointer text-xs font-black text-slate-700">
          完整回复（已脱敏）
        </summary>
        <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">
          {formatJson(detail.data.response)}
        </pre>
      </details>
      {detail.data.content_errors.length ? (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
          部分内容 Blob 无法读取：{detail.data.content_errors.join('、')}
        </p>
      ) : null}
      <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        <summary className="cursor-pointer text-xs font-black text-slate-700">技术关联</summary>
        <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-600">
          {formatJson(detail.data.technical)}
        </pre>
      </details>
    </div>
  );
}

function formatJson(value: unknown): string {
  if (value === null || value === undefined) return '未记录';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return '内容无法展示';
  }
}

function formatMetric(value: number | null, suffix = ''): string {
  return value === null || value === undefined ? '未记录' : `${value}${suffix}`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2.5">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 text-xs font-bold text-slate-900">{value}</dd>
    </div>
  );
}

function Overview({
  match,
  counts,
}: {
  match: MatchWorkbenchOverview['match'];
  counts: Record<string, number>;
}) {
  return (
    <AdminPanel title="比赛摘要" description="截止当前已提交状态的只读汇总">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries({
          状态: match.status,
          辩题: (match.topic as { title?: string })?.title ?? '未记录',
          sequence: match.sequence,
          'context 版本': match.context_version,
          ...counts,
        }).map(([label, value]) => (
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={label}>
            <p className="text-xs font-black text-slate-500">{label}</p>
            <p className="mt-2 break-all text-sm font-black text-slate-950">{String(value)}</p>
          </div>
        ))}
      </div>
    </AdminPanel>
  );
}

function JsonTable({
  query,
  empty,
}: {
  query: {
    data?: { items: Record<string, unknown>[] } | Record<string, unknown>[];
    isLoading: boolean;
    error: unknown;
  };
  empty: string;
}) {
  if (query.isLoading)
    return <div className="h-48 animate-pulse rounded-xl bg-slate-100" role="status" />;
  if (query.error) return <AdminFeedback message={readableAdminError(query.error)} tone="error" />;
  const items = Array.isArray(query.data) ? query.data : (query.data?.items ?? []);
  if (!items.length) return <AdminEmpty>{empty}</AdminEmpty>;
  return (
    <AdminPanel title="只读数据" description={`${items.length} 条当前页记录`}>
      <div className="space-y-3">
        {items.map((item, index) => (
          <pre
            className="max-h-72 overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs leading-6 text-slate-700"
            key={String(item.id ?? index)}
          >
            {JSON.stringify(item, null, 2)}
          </pre>
        ))}
      </div>
    </AdminPanel>
  );
}

function ExportPanel({
  matchId,
  includeAudio,
  setIncludeAudio,
  exportId,
  setExportId,
  exportStatus,
}: {
  matchId: string;
  includeAudio: boolean;
  setIncludeAudio: (value: boolean) => void;
  exportId: string | null;
  setExportId: (value: string | null) => void;
  exportStatus?: {
    status: string;
    processed_items: number;
    total_items: number;
    sha256: string | null;
    error_code: string | null;
  };
}) {
  const [pending, setPending] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [preflightTotal, setPreflightTotal] = useState(1);
  async function create() {
    setPending(true);
    try {
      const preflight = await adminApi.preflightExport([matchId], includeAudio);
      setPreflightTotal(Number(preflight.total_items ?? 1));
      setConfirmOpen(true);
    } finally {
      setPending(false);
    }
  }
  async function submitExport() {
    setPending(true);
    try {
      const created = await adminApi.createExport([matchId], includeAudio);
      setExportId(created.id);
      setConfirmOpen(false);
    } finally {
      setPending(false);
    }
  }
  return (
    <>
      <AdminPanel
        title="研究导出"
        description="导出包含身份、文字、事件和调用摘要；运行中比赛会标记为不完整快照。"
      >
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
            <input
              checked={includeAudio}
              onChange={(event) => setIncludeAudio(event.target.checked)}
              type="checkbox"
            />
            包含已授权音频
          </label>
          <AdminButton disabled={pending} onClick={() => void create()}>
            <FileArchive className="size-4" />
            创建 ZIP 导出
          </AdminButton>
        </div>
        {exportStatus ? (
          <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <StatusBadge status={exportStatus.status} />
              <span className="text-sm font-bold text-slate-600">
                {exportStatus.processed_items} / {exportStatus.total_items}
              </span>
            </div>
            {exportStatus.error_code ? (
              <p className="mt-3 text-sm font-bold text-red-700">{exportStatus.error_code}</p>
            ) : null}
            {exportStatus.sha256 ? (
              <p className="mt-3 break-all font-mono text-xs text-slate-600">
                SHA-256: {exportStatus.sha256}
              </p>
            ) : null}
            {['SUCCEEDED', 'PARTIAL'].includes(exportStatus.status) ? (
              <a
                className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-black text-white hover:bg-blue-700"
                href={`/api/admin/exports/${exportId}/download`}
              >
                <Download className="size-4" />
                下载 ZIP
              </a>
            ) : null}
          </div>
        ) : null}
      </AdminPanel>
      <AdminConfirmDialog
        confirmLabel="创建导出"
        description={`将导出 ${preflightTotal} 场比赛${includeAudio ? '，包含可用音频' : ''}。导出文件可能包含真实身份和研究数据。`}
        loading={pending}
        onConfirm={() => void submitExport()}
        onOpenChange={setConfirmOpen}
        open={confirmOpen}
        title="确认创建研究导出"
      />
    </>
  );
}
