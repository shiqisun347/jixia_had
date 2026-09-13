'use client';

import { useQuery } from '@tanstack/react-query';
import { CheckSquare, Download, FileArchive, Square, Trash2, X } from 'lucide-react';
import { useState } from 'react';

import { adminApi, readableAdminError } from './admin-api';
import { AdminButton, AdminConfirmDialog, StatusBadge } from './admin-controls';
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
  const [confirmExport, setConfirmExport] = useState(false);
  const [includeAudio, setIncludeAudio] = useState(false);
  const [exportId, setExportId] = useState<string | null>(null);
  const [preflightTotal, setPreflightTotal] = useState(0);
  const exportStatus = useQuery({
    queryKey: ['admin', 'export', exportId],
    queryFn: () => adminApi.exportStatus(exportId ?? ''),
    enabled: Boolean(exportId),
    refetchInterval: (query) =>
      query.state.data?.status === 'QUEUED' || query.state.data?.status === 'RUNNING'
        ? 2000
        : false,
  });
  if (!ids.length) return null;
  async function execute(operation: 'ENABLE' | 'DISABLE' | 'DELETE') {
    setPending(true);
    try {
      const batches = Array.from({ length: Math.ceil(ids.length / 500) }, (_, index) =>
        ids.slice(index * 500, (index + 1) * 500),
      );
      for (const batch of batches) {
        const preflight = await adminApi.bulkPreflight(resource, operation, batch);
        if (Number(preflight.available ?? 0) !== batch.length) {
          toast?.showToast({ message: '部分项目当前不可操作，请刷新后重新选择。', tone: 'error' });
          return;
        }
      }
      let failed = 0;
      for (const batch of batches) {
        const result = await adminApi.bulk(resource, operation, batch);
        failed += Number(result.failed_items ?? 0);
      }
      await onCompleted();
      onClear();
      toast?.showToast({
        message: failed
          ? `批量操作完成：成功 ${ids.length - failed} 项，失败 ${failed} 项。`
          : `已完成 ${ids.length} 项批量操作。`,
        tone: failed ? 'error' : 'success',
      });
    } catch (error) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    } finally {
      setPending(false);
    }
  }
  async function preflightExport() {
    setPending(true);
    try {
      if (ids.length > 100) {
        toast?.showToast({ message: '单次最多导出 100 场比赛，请缩小选择范围。', tone: 'error' });
        return;
      }
      const result = await adminApi.preflightExport(ids, includeAudio);
      setPreflightTotal(Number(result.total_items ?? ids.length));
      setConfirmExport(true);
    } catch (error) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    } finally {
      setPending(false);
    }
  }
  async function createExport() {
    setPending(true);
    try {
      const result = await adminApi.createExport(ids, includeAudio);
      setExportId(result.id);
      setConfirmExport(false);
      toast?.showToast({ message: '比赛数据导出任务已创建。', tone: 'success' });
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
            {resource === 'voice' ? (
              <AdminButton
                disabled={pending}
                onClick={() => setConfirmDelete(true)}
                size="sm"
                tone="danger"
              >
                <Trash2 className="size-3.5" />
                删除音色
              </AdminButton>
            ) : null}
          </>
        ) : (
          <>
            <label className="flex items-center gap-2 px-2 text-xs font-bold text-blue-900">
              <input
                checked={includeAudio}
                onChange={(event) => setIncludeAudio(event.target.checked)}
                type="checkbox"
              />
              包含已授权音频
            </label>
            <AdminButton
              disabled={pending}
              onClick={() => void preflightExport()}
              size="sm"
              tone="secondary"
            >
              <FileArchive className="size-3.5" />
              导出比赛数据
            </AdminButton>
            <AdminButton
              disabled={pending}
              onClick={() => setConfirmDelete(true)}
              size="sm"
              tone="danger"
            >
              <Trash2 className="size-3.5" />
              删除比赛
            </AdminButton>
          </>
        )}
        <AdminButton disabled={pending} onClick={onClear} size="sm" tone="ghost">
          <X className="size-3.5" />
          取消选择
        </AdminButton>
        {exportStatus.data ? (
          <>
            <StatusBadge status={exportStatus.data.status} />
            <span className="text-xs font-bold text-blue-900">
              {exportStatus.data.processed_items} / {exportStatus.data.total_items}
            </span>
            {['SUCCEEDED', 'PARTIAL'].includes(exportStatus.data.status) ? (
              <a
                className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-2 text-xs font-black text-white"
                href={`/api/admin/exports/${exportId}/download`}
              >
                <Download className="size-3.5" /> 下载 ZIP
              </a>
            ) : null}
          </>
        ) : null}
      </div>
      <AdminConfirmDialog
        confirmLabel="永久删除"
        description={
          resource === 'voice'
            ? `确认删除选中的 ${ids.length} 个音色？有历史引用的音色将改为归档停用。`
            : `确认永久删除选中的 ${ids.length} 项？此操作不可撤销。`
        }
        loading={pending}
        onConfirm={() => {
          setConfirmDelete(false);
          void execute('DELETE');
        }}
        onOpenChange={setConfirmDelete}
        open={confirmDelete}
        title={resource === 'voice' ? '确认删除音色' : '确认删除比赛'}
      />
      <AdminConfirmDialog
        confirmLabel="创建导出"
        description={`将导出 ${preflightTotal} 场比赛${includeAudio ? '，包含可用音频' : ''}。导出包包含身份、文字、事件及该比赛的第三方 API 请求日志。`}
        loading={pending}
        onConfirm={() => void createExport()}
        onOpenChange={setConfirmExport}
        open={confirmExport}
        title="确认创建研究导出"
      />
    </>
  );
}
