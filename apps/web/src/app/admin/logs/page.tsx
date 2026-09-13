'use client';

import { useQuery } from '@tanstack/react-query';
import { Eye, Pause, Radio } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import {
  AdminButton,
  AdminDrawer,
  AdminPagination,
  AdminSearch,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type {
  AdminPage,
  ExternalCallDetail,
  ExternalCallRow,
} from '@/features/admin/admin-types';

const REFRESH_OPTIONS = [
  { value: 0, label: '关闭' },
  { value: 2000, label: '2 秒' },
  { value: 5000, label: '5 秒' },
  { value: 10000, label: '10 秒' },
  { value: 30000, label: '30 秒' },
  { value: 60000, label: '1 分钟' },
] as const;
const KIND_OPTIONS = [
  ['ALL', '全部类型'],
  ['LLM_SPEECH', 'Agent 发言稿'],
  ['LLM_DECISION', 'Agent 发言决策'],
  ['JUDGE', 'AI 裁判'],
  ['ASR', '语音识别'],
  ['TTS', '语音合成'],
] as const;
const SOURCE_OPTIONS = [
  ['ALL', '全部来源'],
  ['MATCH', '比赛'],
  ['HOST_AUDIO', '主持音频'],
  ['CONFIG_TEST', '配置测试'],
  ['OPS_PROBE', '运维探针'],
] as const;

function sinceValue(range: string): string {
  const milliseconds = { '15m': 900_000, '1h': 3_600_000, '6h': 21_600_000, '24h': 86_400_000 }[
    range
  ];
  return milliseconds ? new Date(Date.now() - milliseconds).toISOString() : '';
}

function sourceLabel(value: string | null): string {
  return SOURCE_OPTIONS.find(([key]) => key === value)?.[1] ?? value ?? '未标记';
}

export default function AdminApiRequestLogsPage() {
  const [matchId, setMatchId] = useState('');
  const [kind, setKind] = useState('ALL');
  const [source, setSource] = useState('ALL');
  const [status, setStatus] = useState('ALL');
  const [range, setRange] = useState('1h');
  const [page, setPage] = useState(1);
  const [refreshMs, setRefreshMs] = useState(0);
  const [selected, setSelected] = useState<ExternalCallRow | null>(null);
  const [visible, setVisible] = useState<AdminPage<ExternalCallRow> | null>(null);
  const [buffered, setBuffered] = useState<AdminPage<ExternalCallRow> | null>(null);
  const signature = `${matchId}|${kind}|${source}|${status}|${range}|${page}`;
  const previousSignature = useRef(signature);
  const params = useMemo(
    () => ({
      page,
      page_size: 50 as const,
      call_kind: kind === 'ALL' ? '' : kind,
      source_kind: source === 'ALL' ? '' : source,
      status: status === 'ALL' ? '' : status,
      match_id: matchId.trim(),
      since: sinceValue(range),
    }),
    [kind, matchId, page, range, source, status],
  );
  const callsQuery = useQuery({
    queryKey: ['admin', 'external-calls', params],
    queryFn: () => adminApi.externalCalls(params),
    refetchInterval: refreshMs || false,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    const url = new URL(window.location.href);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMatchId(url.searchParams.get('match_id') ?? '');
    setKind(url.searchParams.get('call_kind') ?? 'ALL');
    setSource(url.searchParams.get('source_kind') ?? 'ALL');
    setStatus(url.searchParams.get('status') ?? 'ALL');
    setRange(url.searchParams.get('range') ?? '1h');
    setPage(Number(url.searchParams.get('page') ?? 1) || 1);
  }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    for (const [key, value, fallback] of [
      ['match_id', matchId.trim(), ''],
      ['call_kind', kind, 'ALL'],
      ['source_kind', source, 'ALL'],
      ['status', status, 'ALL'],
      ['range', range, '1h'],
      ['page', String(page), '1'],
    ]) {
      if (value === fallback) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    window.history.replaceState(null, '', `${url.pathname}${url.search}`);
  }, [kind, matchId, page, range, source, status]);
  useEffect(() => {
    const data = callsQuery.data;
    if (!data) return;
    if (!visible || previousSignature.current !== signature || refreshMs === 0) {
      previousSignature.current = signature;
      setVisible(data);
      setBuffered(null);
      return;
    }
    if (data.items[0]?.id !== visible.items[0]?.id) {
      // Poll results stay buffered so an operator's reading position does not jump.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setBuffered(data);
    }
  }, [callsQuery.data, refreshMs, signature, visible]);

  const rows = visible?.items ?? [];
  const newCount = buffered
    ? buffered.items.filter((item) => !rows.some((visibleItem) => visibleItem.id === item.id)).length
    : 0;
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <div className="flex items-center gap-2">
            <AdminSelect
              aria-label="自动刷新间隔"
              label="自动刷新间隔"
              value={String(refreshMs)}
              onChange={(event) => setRefreshMs(Number(event.target.value))}
            >
              {REFRESH_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </AdminSelect>
            <span className="inline-flex items-center gap-1.5 text-xs font-bold text-slate-600">
              {refreshMs ? (
                <Radio className="size-3.5 text-emerald-600" />
              ) : (
                <Pause className="size-3.5" />
              )}
              {refreshMs ? '实时观察' : '查询模式'}
            </span>
          </div>
        }
        description="查看平台向第三方供应商实际发送和收到的安全 JSON；正文仅在打开详情时加载。"
        eyebrow="PROVIDER API CALLS"
        title="API 请求日志"
      />
      {callsQuery.error ? (
        <AdminFeedback message={readableAdminError(callsQuery.error)} tone="error" />
      ) : null}
      <AdminPanel title="调用记录" description={`${visible?.total ?? 0} 条匹配记录`}>
        <div className="mb-4 flex flex-wrap gap-2">
          <AdminSearch
            label="比赛 ID"
            value={matchId}
            onChange={(event) => {
              setPage(1);
              setMatchId(event.target.value);
            }}
            placeholder="精确输入比赛 ID"
          />
          <LogSelect label="调用类型" value={kind} options={KIND_OPTIONS} onChange={setKind} setPage={setPage} />
          <LogSelect label="来源" value={source} options={SOURCE_OPTIONS} onChange={setSource} setPage={setPage} />
          <LogSelect label="状态" value={status} options={[
            ['ALL', '全部状态'], ['STARTED', '进行中'], ['SUCCEEDED', '成功'], ['FAILED', '失败'], ['CANCELLED', '已取消'],
          ]} onChange={setStatus} setPage={setPage} />
          <LogSelect label="时间范围" value={range} options={[
            ['15m', '最近 15 分钟'], ['1h', '最近 1 小时'], ['6h', '最近 6 小时'], ['24h', '最近 24 小时'], ['ALL', '全部时间'],
          ]} onChange={setRange} setPage={setPage} />
        </div>
        {newCount > 0 ? (
          <AdminButton
            className="mb-3"
            onClick={() => {
              setVisible(buffered);
              setBuffered(null);
            }}
            tone="secondary"
          >
            有 {newCount} 条新记录，点击查看
          </AdminButton>
        ) : null}
        {callsQuery.isLoading && !visible ? (
          <div className="h-64 animate-pulse rounded-md bg-slate-50" role="status" aria-label="正在加载 API 请求日志" />
        ) : rows.length ? (
          <div className="overflow-x-auto rounded-md border border-slate-200 text-xs">
            <div className="min-w-[64rem]">
              {rows.map((row) => (
                <button
                  className="grid w-full grid-cols-[10rem_8rem_7rem_12rem_6rem_5rem_6rem_2rem] items-center gap-3 border-b border-slate-100 px-3 py-2 text-left hover:bg-slate-50"
                  key={row.id}
                  onClick={() => setSelected(row)}
                  type="button"
                >
                  <time className="text-slate-500">{new Date(row.started_at).toLocaleString('zh-CN')}</time>
                  <span className="font-bold text-slate-900">{row.kind_label}</span>
                  <span className="text-slate-600">{sourceLabel(row.source_kind)}</span>
                  <span className="truncate font-mono text-slate-600">{row.provider} / {row.operation}</span>
                  <StatusBadge status={row.status} />
                  <span>第 {row.attempt_no} 次</span>
                  <span>{row.completed_latency_ms === null ? '未完成' : `${row.completed_latency_ms} ms`}</span>
                  <Eye className="size-3.5 text-slate-400" />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <AdminEmpty>没有符合筛选条件的新版本 API 请求日志。</AdminEmpty>
        )}
        {visible ? (
          <AdminPagination page={visible.page} total={visible.total} totalPages={visible.total_pages} onPageChange={setPage} />
        ) : null}
      </AdminPanel>
      <ProviderCallDrawer selected={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function LogSelect({ label, value, options, onChange, setPage }: {
  label: string;
  value: string;
  options: readonly (readonly [string, string])[];
  onChange: (value: string) => void;
  setPage: (page: number) => void;
}) {
  return (
    <AdminSelect aria-label={label} label={label} value={value} onChange={(event) => {
      setPage(1);
      onChange(event.target.value);
    }}>
      {options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}
    </AdminSelect>
  );
}

function ProviderCallDrawer({ selected, onClose }: {
  selected: ExternalCallRow | null;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ['admin', 'external-call', selected?.id],
    queryFn: () => adminApi.externalCall(selected!.id),
    enabled: Boolean(selected),
  });
  return (
    <AdminDrawer
      open={Boolean(selected)}
      onOpenChange={(open) => { if (!open) onClose(); }}
      title="API 请求详情"
      description="仅显示已经通过服务端安全边界的供应商 JSON。"
    >
      {detail.isLoading ? <div className="h-64 animate-pulse rounded-md bg-slate-100" role="status" /> : null}
      {detail.error ? <AdminFeedback message={readableAdminError(detail.error)} tone="error" /> : null}
      {detail.data ? <ProviderJson detail={detail.data} /> : null}
    </AdminDrawer>
  );
}

function ProviderJson({ detail }: { detail: ExternalCallDetail }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        <p><strong>业务动作：</strong><span className="break-all font-mono">{detail.logical_call_id ?? '未记录'}</span></p>
        <p><strong>供应商请求：</strong><span className="break-all font-mono">{detail.provider_request_id ?? '未记录'}</span></p>
      </div>
      <JsonSection title="请求 JSON" value={detail.request} status={detail.request_capture_status} />
      <JsonSection title="响应 JSON" value={detail.response} status={detail.response_capture_status} />
      {detail.content_errors.length ? (
        <AdminFeedback message={`部分内容无法读取：${detail.content_errors.join('、')}`} tone="error" />
      ) : null}
    </div>
  );
}

function JsonSection({ title, value, status }: { title: string; value: unknown; status: string | null }) {
  const text = value === null || value === undefined ? '未记录' : JSON.stringify(value, null, 2);
  return (
    <section className="overflow-hidden rounded-md border border-slate-200">
      <div className="flex items-center justify-between bg-slate-50 px-3 py-2">
        <h3 className="text-sm font-black text-slate-900">{title}</h3>
        <StatusBadge status={status ?? 'UNAVAILABLE'} />
      </div>
      <pre className="max-h-[24rem] overflow-auto whitespace-pre-wrap break-all bg-slate-950 p-4 text-xs leading-6 text-slate-100">{text}</pre>
    </section>
  );
}
