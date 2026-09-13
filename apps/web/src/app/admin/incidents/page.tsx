'use client';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import {
  AdminButton,
  AdminDrawer,
  AdminRefreshButton,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';

export default function AdminIncidentsPage() {
  const [status, setStatus] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notes, setNotes] = useState('');
  const query = useQuery({ queryKey: ['admin', 'incidents'], queryFn: () => adminApi.incidents() });
  const detail = useQuery({
    queryKey: ['admin', 'incident', selectedId],
    queryFn: () => adminApi.incident(selectedId ?? ''),
    enabled: Boolean(selectedId),
  });
  const items = query.data?.items ?? [];
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={<AdminRefreshButton onRefresh={() => query.refetch()} />}
        description="按故障指纹聚合影响范围和处理状态，避免重复错误淹没排查视线。"
        eyebrow="INCIDENTS"
        title="事故"
      />
      {query.error ? (
        <AdminFeedback message={readableAdminError(query.error)} tone="error" />
      ) : null}
      <AdminPanel title="事故列表" description={`${items.length} 个当前查询结果`}>
        {query.isLoading ? (
          <div
            className="h-56 animate-pulse rounded-lg bg-slate-50"
            role="status"
            aria-label="正在加载事故"
          />
        ) : items.length ? (
          <div className="divide-y divide-slate-200 rounded-lg border border-slate-200">
            {items.map((item) => (
              <button
                className="grid w-full grid-cols-[minmax(0,1fr)_8rem_10rem] items-center gap-4 px-4 py-3 text-left hover:bg-slate-50"
                key={item.id}
                onClick={() => {
                  setSelectedId(item.id);
                  setStatus(item.status);
                  setNotes(item.notes ?? '');
                }}
                type="button"
              >
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-bold text-slate-950">{item.title}</h2>
                  <p className="mt-1 text-xs text-slate-600">
                    {item.occurrence_count} 次 · 影响 {item.affected_match_count} 场比赛 /{' '}
                    {item.affected_user_count} 名用户
                  </p>
                </div>
                <StatusBadge status={item.status} />
                <time className="text-right text-xs text-slate-600">
                  {new Date(item.last_seen_at).toLocaleString('zh-CN')}
                </time>
              </button>
            ))}
          </div>
        ) : (
          <AdminEmpty>暂无聚合事故。</AdminEmpty>
        )}
      </AdminPanel>
      <AdminDrawer
        open={Boolean(selectedId)}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
        title="事故处理"
        description="事故是日志、比赛和任务影响范围的聚合，不等同单条日志。"
        footer={
          <div className="flex justify-end">
            <AdminButton
              tone="primary"
              onClick={() =>
                selectedId
                  ? void adminApi.updateIncident(selectedId, status, notes).then(() => {
                      setSelectedId(null);
                      return query.refetch();
                    })
                  : undefined
              }
            >
              保存处理状态
            </AdminButton>
          </div>
        }
      >
        {detail.data ? (
          <div className="space-y-4">
            <AdminSelect
              label="处理状态"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="OPEN">待处理</option>
              <option value="ACKNOWLEDGED">已确认</option>
              <option value="RESOLVED">已解决</option>
            </AdminSelect>
            <label className="grid gap-1.5 text-xs font-bold text-slate-600">
              处理备注
              <textarea
                className="admin-field min-h-28 resize-y"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>
            <div className="max-h-72 space-y-2 overflow-auto">
              {detail.data.events.map((event) => (
                <div className="rounded-md border border-slate-200 p-3 text-xs" key={event.id}>
                  <b>
                    {event.level} · {event.service}
                  </b>
                  <p className="mt-1 text-slate-600">{event.message}</p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </AdminDrawer>
    </div>
  );
}
