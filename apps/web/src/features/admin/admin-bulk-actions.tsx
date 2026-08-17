'use client';

import { CheckSquare, Square, Trash2, X } from 'lucide-react';
import { useState } from 'react';

import { adminApi, readableAdminError } from './admin-api';
import { AdminButton, AdminConfirmDialog } from './admin-controls';
import { useOptionalToast } from '@/components/ui/toast-provider';

type Props = {
  resource: string;
  ids: string[];
  onClear: () => void;
  onCompleted: () => Promise<unknown>;
};

export function AdminBulkActions({ resource, ids, onClear, onCompleted }: Props) {
  const toast = useOptionalToast();
  const [pending, setPending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  if (!ids.length) return null;
  async function execute(operation: 'ENABLE' | 'DISABLE' | 'DELETE') {
    setPending(true);
    try {
      const preflight = await adminApi.bulkPreflight(resource, operation, ids);
      if (Number(preflight.available ?? 0) !== ids.length) {
        toast?.showToast({ message: '部分项目当前不可操作，请刷新后重新选择。', tone: 'error' });
        return;
      }
      await adminApi.bulk(resource, operation, ids);
      await onCompleted();
      onClear();
      toast?.showToast({ message: `已提交 ${ids.length} 项批量操作。`, tone: 'success' });
    } catch (error) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    } finally {
      setPending(false);
    }
  }
  return (
    <>
      <div
        className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 p-2"
        role="region"
        aria-label="批量操作"
      >
        <span className="px-2 text-xs font-black text-blue-900">已选 {ids.length} 项</span>
        {resource !== 'match' ? (
          <>
            <AdminButton
              disabled={pending}
              onClick={() => void execute('ENABLE')}
              size="sm"
              tone="secondary"
            >
              <CheckSquare className="size-3.5" />
              启用
            </AdminButton>
            <AdminButton
              disabled={pending}
              onClick={() => void execute('DISABLE')}
              size="sm"
              tone="secondary"
            >
              <Square className="size-3.5" />
              停用
            </AdminButton>
          </>
        ) : (
          <AdminButton
            disabled={pending}
            onClick={() => setConfirmDelete(true)}
            size="sm"
            tone="danger"
          >
            <Trash2 className="size-3.5" />
            删除终态比赛
          </AdminButton>
        )}
        <AdminButton disabled={pending} onClick={onClear} size="sm" tone="ghost">
          <X className="size-3.5" />
          取消选择
        </AdminButton>
      </div>
      <AdminConfirmDialog
        confirmLabel="永久删除"
        description={`确认永久删除选中的 ${ids.length} 项？此操作不可撤销。`}
        loading={pending}
        onConfirm={() => {
          setConfirmDelete(false);
          void execute('DELETE');
        }}
        onOpenChange={setConfirmDelete}
        open={confirmDelete}
        title="确认删除终态比赛"
      />
    </>
  );
}
