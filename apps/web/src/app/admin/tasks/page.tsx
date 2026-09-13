'use client';

import { useQuery } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { useState } from 'react';

import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import {
  AdminButton,
  AdminRefreshButton,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';

export default function AdminTasksPage() {
  const [status, setStatus] = useState('ALL');
  const query = useQuery({
    queryKey: ['admin', 'diagnostic-tasks', status],
    queryFn: () => adminApi.diagnosticTasks({ status: status === 'ALL' ? '' : status }),
  });
  const items = query.data?.items ?? [];
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={<AdminRefreshButton onRefresh={() => query.refetch()} />}
        description="查看主持音频、归档、导出与排行榜等有界后台任务的执行状态。"
        eyebrow="BACKGROUND TASKS"
        title="后台任务"
      />
      {query.error ? (
        <AdminFeedback message={readableAdminError(query.error)} tone="error" />
      ) : null}
      <AdminPanel title="任务队列" description={`${items.length} 个最近任务`}>
        <AdminSelect
          className="mb-4"
          label="任务状态"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="ALL">全部状态</option>
          <option value="PENDING">等待中</option>
          <option value="RUNNING">执行中</option>
          <option value="SUCCEEDED">成功</option>
          <option value="FAILED">失败</option>
        </AdminSelect>
        {query.isLoading ? (
          <div
            className="h-56 animate-pulse rounded-lg bg-slate-50"
            role="status"
            aria-label="正在加载后台任务"
          />
        ) : items.length ? (
          <div className="divide-y divide-slate-200 rounded-lg border border-slate-200">
            {items.map((item) => (
              <article
                className="grid grid-cols-[minmax(0,1fr)_8rem_10rem_6rem] items-center gap-4 px-4 py-3"
                key={item.id}
              >
                <div className="min-w-0">
                  <h2 className="truncate font-mono text-xs font-bold text-slate-950">
                    {item.task_type}
                  </h2>
                  <p className="mt-1 text-xs text-slate-600">
                    尝试 {item.attempts}/{item.max_attempts}
                    {item.error_code ? ` · ${item.error_code}` : ''}
                  </p>
                </div>
                <StatusBadge status={item.status} />
                <time className="text-right text-xs text-slate-600">
                  {new Date(item.updated_at).toLocaleString('zh-CN')}
                </time>
                {item.status === 'FAILED' ? (
                  <AdminButton
                    size="sm"
                    onClick={() =>
                      void adminApi.retryDiagnosticTask(item.id).then(() => query.refetch())
                    }
                  >
                    <RotateCcw className="size-3.5" />
                    重试
                  </AdminButton>
                ) : (
                  <span />
                )}
              </article>
            ))}
          </div>
        ) : (
          <AdminEmpty>暂无后台任务。</AdminEmpty>
        )}
      </AdminPanel>
    </div>
  );
}
