'use client';

import { useEffect, useState } from 'react';

import {
  AdminButton,
  AdminFeedback,
  AdminPageHeader,
  AdminPanel,
  readableAdminError,
  type StorageStatus,
} from '@/features/admin';
import { useOptionalToast } from '@/components/ui/toast-provider';
import { requestJson } from '@/lib/auth-api';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';

export default function AdminSettingsPage() {
  const toast = useOptionalToast();
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [error, setError] = useState('');
  const { isSubmitting, submit } = useAdminSubmit();

  useEffect(() => {
    let active = true;
    void requestJson<StorageStatus>('/api/admin/storage')
      .then((result) => {
        if (active) setStorage(result);
      })
      .catch((requestError: unknown) => {
        if (active) setError(readableAdminError(requestError));
      });
    return () => {
      active = false;
    };
  }, []);

  const ratio = storage?.used_ratio ?? 0;
  return (
    <div className="space-y-6">
      <AdminPageHeader
        eyebrow="SYSTEM SETTINGS"
        title="系统设置"
        description="查看本地存储门禁并触发已有后台维护任务。MVP 不提供自动备份。"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <div className="grid gap-5 xl:grid-cols-2">
        <AdminPanel title="本地存储" description="超过 80% 告警，达到 90% 阻止新比赛开赛。">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-4xl font-black">
                {storage ? `${(ratio * 100).toFixed(1)}%` : '—'}
              </p>
              <p className="mt-1 text-xs text-slate-500">磁盘已用</p>
            </div>
            <p className="text-right text-sm font-bold text-slate-600">
              剩余 {storage ? (storage.free_bytes / 1024 ** 3).toFixed(1) : '—'} GB
              {storage?.estimated_days_remaining ? (
                <>
                  <br />约 {storage.estimated_days_remaining} 天
                </>
              ) : null}
            </p>
          </div>
          <div className="mt-5 h-3 overflow-hidden rounded-full bg-slate-100">
            <div
              className={
                ratio >= 0.9
                  ? 'h-full rounded-full bg-red-500'
                  : ratio >= 0.8
                    ? 'h-full rounded-full bg-amber-500'
                    : 'h-full rounded-full bg-blue-600'
              }
              style={{ width: `${Math.min(100, ratio * 100)}%` }}
            />
          </div>
          <p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900">
            系统不执行自动备份。本地磁盘损坏可能导致录音、回放和导出文件不可恢复。
          </p>
        </AdminPanel>
        <AdminPanel
          title="排行榜维护"
          description="正常情况下每天自动全量更新；手动操作使用相同的幂等任务。"
        >
          <p className="text-sm leading-6 text-slate-600">
            当管理员修正赛后评分或需要立即刷新榜单时，可手动排队一次重算。已有快照会保留到新批次成功。
          </p>
          <AdminButton
            className="mt-5"
            loading={isSubmitting}
            tone="primary"
            onClick={() => {
              void submit(() =>
                requestJson('/api/admin/leaderboards/rebuild', {
                  method: 'POST',
                  body: '{}',
                }).then(() => undefined),
              )
                .then((submitted) => {
                  if (submitted) {
                    toast?.showToast({ message: '排行榜重算任务已排队。', tone: 'success' });
                  }
                })
                .catch((requestError: unknown) =>
                  toast?.showToast({ message: readableAdminError(requestError), tone: 'error' }),
                );
            }}
          >
            立即重算排行榜
          </AdminButton>
        </AdminPanel>
      </div>
    </div>
  );
}
