'use client';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import {
  AdminDrawer,
  AdminPagination,
  AdminRefreshButton,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type { LogRow } from '@/features/admin/admin-types';

export default function AdminAuditPage() {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<LogRow | null>(null);
  async function openDetail(logId: string) {
    setSelected(await adminApi.auditLog(logId));
  }
  const query = useQuery({
    queryKey: ['admin', 'audit', page],
    queryFn: () => adminApi.logs({ page, page_size: 50 }),
  });
  const items = query.data?.items ?? [];
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={<AdminRefreshButton onRefresh={() => query.refetch()} />}
        description="记录管理员对账号、资源、赛制、比赛与敏感详情执行的管理操作。"
        eyebrow="AUDIT TRAIL"
        title="审计日志"
      />
      {query.error ? (
        <AdminFeedback message={readableAdminError(query.error)} tone="error" />
      ) : null}
      <AdminPanel
        title="只读审计记录"
        description={`${query.data?.total ?? 0} 条记录 · 服务端分页`}
      >
        {query.isLoading ? (
          <div
            className="h-56 animate-pulse rounded-md bg-slate-50"
            role="status"
            aria-label="正在加载审计日志"
          />
        ) : items.length ? (
          <div className="divide-y divide-slate-200 rounded-md border border-slate-200">
            {items.map((item) => (
              <button
                className="grid w-full grid-cols-[minmax(0,1fr)_7rem_12rem] gap-4 px-4 py-3 text-left hover:bg-slate-50"
                key={item.id}
                onClick={() => void openDetail(item.id)}
                type="button"
              >
                <div>
                  <p className="font-mono text-xs font-bold text-slate-900">{item.action}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {item.target_type} · {item.target_id || '—'}
                  </p>
                </div>
                <StatusBadge status={item.result} />
                <time className="text-right text-xs text-slate-600">
                  {new Date(item.created_at).toLocaleString('zh-CN')}
                </time>
              </button>
            ))}
          </div>
        ) : (
          <AdminEmpty>暂无审计记录。</AdminEmpty>
        )}
        {query.data ? (
          <AdminPagination
            page={query.data.page}
            total={query.data.total}
            totalPages={query.data.total_pages}
            onPageChange={setPage}
          />
        ) : null}
      </AdminPanel>
      <AdminDrawer
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title="审计详情"
        description="记录只读，内容已经服务端脱敏。"
      >
        {selected ? (
          <pre className="max-h-[38rem] overflow-auto rounded-md bg-slate-950 p-4 text-xs leading-6 text-slate-100">
            {JSON.stringify(selected, null, 2)}
          </pre>
        ) : null}
      </AdminDrawer>
    </div>
  );
}
