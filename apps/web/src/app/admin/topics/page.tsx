'use client';

import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Pencil, Plus } from 'lucide-react';
import { useMemo, useState } from 'react';

import { useOptionalToast } from '@/components/ui/toast-provider';
import { readableAdminError } from '@/features/admin/admin-api';
import {
  AdminActionItem,
  AdminActionMenu,
  AdminButton,
  AdminConfirmDialog,
  AdminDrawer,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminDataTable, AdminTableSkeleton } from '@/features/admin/admin-data-table';
import { AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type { TopicRow } from '@/features/admin/admin-types';
import { commitAdminAction } from '@/features/admin/commit-admin-action';
import { useAdminCatalog } from '@/features/admin/use-admin-catalog';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';
import { submitCatalogSave } from '@/features/admin/submit-catalog-save';
import { requestJson } from '@/lib/auth-api';
import { AdminBulkActions } from '@/features/admin/admin-bulk-actions';

export default function AdminTopicsPage() {
  const { catalog, reload, error, loading } = useAdminCatalog();
  const toast = useOptionalToast();
  const { isSubmitting, submit } = useAdminSubmit();
  const [drawer, setDrawer] = useState<TopicRow | 'create' | null>(null);
  const [statusTarget, setStatusTarget] = useState<TopicRow | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  async function toggleStatus(target = statusTarget) {
    if (!target) return;
    const next = target.status === 'ENABLED' ? 'DISABLED' : 'ENABLED';
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(
          () =>
            requestJson(`/api/admin/catalog/topics/${target.id}/status`, {
              method: 'PATCH',
              body: JSON.stringify({ status: next }),
            }),
          reload,
        );
      });
      if (!submitted) return;
      setStatusTarget(null);
      toast?.showToast({
        message: `${target.title} 已${next === 'ENABLED' ? '启用' : '停用'}。`,
        tone: 'success',
      });
      if (refreshResult.value === 'refresh_failed') {
        toast?.showToast({
          message: '辩题状态已修改，但目录未同步；请重新进入页面。',
          tone: 'info',
        });
      }
    } catch (requestError: unknown) {
      toast?.showToast({ message: readableAdminError(requestError), tone: 'error' });
    }
  }
  const columns = useMemo<ColumnDef<TopicRow>[]>(
    () => [
      {
        id: 'select',
        header: '选择',
        cell: ({ row }) => (
          <input
            aria-label={`选择 ${row.original.title}`}
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
        accessorKey: 'title',
        header: '辩题',
        cell: ({ row }) => (
          <div>
            <p className="font-black text-slate-950">{row.original.title}</p>
            <p className="mt-1 text-xs text-slate-600">
              版本 v{row.original.version ?? 1} ·{' '}
              {row.original.topic_key ?? row.original.id.slice(0, 8)}
            </p>
          </div>
        ),
      },
      {
        id: 'sides',
        header: '立场文本',
        cell: ({ row }) => (
          <div className="max-w-xl space-y-1 text-xs text-slate-600">
            <p className="truncate">正方：{row.original.affirmative_text}</p>
            <p className="truncate">反方：{row.original.negative_text}</p>
          </div>
        ),
      },
      {
        accessorKey: 'status',
        header: '状态',
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        id: 'actions',
        header: '操作',
        cell: ({ row }) => (
          <div className="flex justify-end">
            <AdminActionMenu>
              <AdminActionItem onSelect={() => setDrawer(row.original)}>
                <Pencil className="mr-2 size-3.5" aria-hidden="true" />
                编辑辩题
              </AdminActionItem>
              <AdminActionItem
                onSelect={() => setStatusTarget(row.original)}
                tone={row.original.status === 'ENABLED' ? 'danger' : 'default'}
              >
                {row.original.status === 'ENABLED' ? '停用辩题' : '启用辩题'}
              </AdminActionItem>
            </AdminActionMenu>
          </div>
        ),
      },
    ],
    [selectedIds],
  );
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: catalog.topics,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <AdminButton onClick={() => setDrawer('create')} tone="primary">
            <Plus className="size-4" aria-hidden="true" />
            添加辩题
          </AdminButton>
        }
        description="维护公开辩题和正反立场文本；活动比赛使用创建时快照。"
        eyebrow="TOPIC LIBRARY"
        title="辩题管理"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel title="辩题目录" description={`${catalog.topics.length} 个辩题配置`}>
        {loading ? (
          <AdminTableSkeleton />
        ) : (
          <>
            <AdminBulkActions
              ids={selectedIds}
              onClear={() => setSelectedIds([])}
              onCompleted={reload}
              resource="topic"
            />
            <AdminDataTable
              table={table}
              emptyTitle="还没有辩题"
              emptyDescription="点击右上角添加第一个辩题。"
            />
          </>
        )}
      </AdminPanel>
      <TopicDrawer
        key={drawer === 'create' ? 'create' : (drawer?.id ?? 'closed')}
        topic={drawer === 'create' ? null : drawer}
        open={Boolean(drawer)}
        onClose={() => setDrawer(null)}
        onSaved={reload}
      />
      <AdminConfirmDialog
        confirmLabel={statusTarget?.status === 'ENABLED' ? '确认停用' : '确认启用'}
        description="活动比赛引用的辩题由服务端保护，停用只影响后续新房间。"
        onConfirm={() => void toggleStatus(statusTarget)}
        loading={isSubmitting}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setStatusTarget(null);
        }}
        open={Boolean(statusTarget)}
        title={statusTarget?.status === 'ENABLED' ? '停用辩题？' : '启用辩题？'}
      />
    </div>
  );
}
function TopicDrawer({
  topic,
  open,
  onClose,
  onSaved,
}: {
  topic: TopicRow | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => Promise<{ isError: boolean }>;
}) {
  const toast = useOptionalToast();
  const { isSubmitting: saving, submit } = useAdminSubmit();
  const [title, setTitle] = useState(topic?.title ?? '');
  const [affirmative, setAffirmative] = useState(topic?.affirmative_text ?? '');
  const [negative, setNegative] = useState(topic?.negative_text ?? '');
  async function save() {
    try {
      const result = await submitCatalogSave(
        submit,
        () =>
          requestJson(
            topic ? `/api/admin/catalog/topics/${topic.id}` : '/api/admin/catalog/topics',
            {
              method: topic ? 'PATCH' : 'POST',
              body: JSON.stringify({
                title: title.trim(),
                affirmative_text: affirmative.trim(),
                negative_text: negative.trim(),
              }),
            },
          ),
        onSaved,
      );
      if (result === 'not_started') return;
      onClose();
      toast?.showToast({ message: topic ? '辩题已更新。' : '辩题已创建。', tone: 'success' });
      if (result === 'refresh_failed') {
        toast?.showToast({ message: '辩题已保存，但目录未同步；请重新进入页面。', tone: 'info' });
      }
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }
  return (
    <AdminDrawer
      description="保存后新房间使用最新文本，历史比赛快照不受影响。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton disabled={saving} onClick={onClose}>
            取消
          </AdminButton>
          <AdminButton loading={saving} onClick={() => void save()} tone="primary">
            保存辩题
          </AdminButton>
        </div>
      }
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
      open={open}
      title={topic ? `编辑辩题 · ${topic.title}` : '添加辩题'}
    >
      <div className="space-y-4">
        <Field label="辩题标题" value={title} onChange={setTitle} />
        <Area label="正方立场" value={affirmative} onChange={setAffirmative} />
        <Area label="反方立场" value={negative} onChange={setNegative} />
      </div>
    </AdminDrawer>
  );
}
function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <input
        className="admin-field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
function Area({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <textarea
        className="admin-field min-h-28 resize-y"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
