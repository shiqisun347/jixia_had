'use client';

import { useEffect, useState } from 'react';

import {
  AdminButton,
  AdminFeedback,
  AdminPageHeader,
  AdminPanel,
  readableAdminError,
  type StorageStatus,
  type SystemSettings,
} from '@/features/admin';
import { useOptionalToast } from '@/components/ui/toast-provider';
import { requestJson } from '@/lib/auth-api';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';

export default function AdminSettingsPage() {
  const toast = useOptionalToast();
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [error, setError] = useState('');
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const { isSubmitting, submit } = useAdminSubmit();

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<StorageStatus>('/api/admin/storage'),
      requestJson<SystemSettings>('/api/admin/settings'),
    ])
      .then(([storageResult, settingsResult]) => {
        if (active) {
          setStorage(storageResult);
          setSettings(settingsResult);
        }
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
        <AdminPanel
          title="运行参数"
          description="只影响后续日志写入、清理和上传请求；不改变已发布快照或进行中的比赛。"
        >
          {settings ? (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                void submit(() =>
                  requestJson<SystemSettings>('/api/admin/settings', {
                    method: 'PATCH',
                    body: JSON.stringify(settings),
                  }).then(setSettings),
                )
                  .then((submitted) => {
                    if (submitted) {
                      toast?.showToast({ message: '系统设置已保存。', tone: 'success' });
                    }
                  })
                  .catch((requestError: unknown) =>
                    toast?.showToast({
                      message: readableAdminError(requestError),
                      tone: 'error',
                    }),
                  );
              }}
            >
              <label className="grid gap-1.5 text-xs font-bold text-slate-600">
                运行日志保留天数
                <input
                  className="admin-field"
                  max={3650}
                  min={1}
                  onChange={(event) =>
                    setSettings({ ...settings, log_retention_days: Number(event.target.value) })
                  }
                  type="number"
                  value={settings.log_retention_days}
                />
              </label>
              <label className="flex items-center gap-2 text-xs font-bold text-slate-700">
                <input
                  checked={settings.debug_enabled}
                  onChange={(event) =>
                    setSettings({
                      ...settings,
                      debug_enabled: event.target.checked,
                      debug_expires_at: event.target.checked
                        ? (settings.debug_expires_at ?? defaultDebugExpiry())
                        : null,
                    })
                  }
                  type="checkbox"
                />
                临时记录 DEBUG 日志
              </label>
              {settings.debug_enabled ? (
                <label className="grid gap-1.5 text-xs font-bold text-slate-600">
                  DEBUG 自动关闭时间
                  <input
                    className="admin-field"
                    min={toLocalDateTime(new Date())}
                    onChange={(event) =>
                      setSettings({
                        ...settings,
                        debug_expires_at: new Date(event.target.value).toISOString(),
                      })
                    }
                    type="datetime-local"
                    value={toLocalDateTime(new Date(settings.debug_expires_at ?? ''))}
                  />
                </label>
              ) : null}
              <label className="grid gap-1.5 text-xs font-bold text-slate-600">
                单文件上传上限（MB）
                <input
                  className="admin-field"
                  max={50}
                  min={0.25}
                  onChange={(event) =>
                    setSettings({
                      ...settings,
                      max_upload_bytes: Math.round(Number(event.target.value) * 1024 * 1024),
                    })
                  }
                  step="0.25"
                  type="number"
                  value={settings.max_upload_bytes / 1024 / 1024}
                />
              </label>
              <AdminButton loading={isSubmitting} tone="primary" type="submit">
                保存设置
              </AdminButton>
            </form>
          ) : (
            <p className="text-sm text-slate-500">正在加载设置…</p>
          )}
        </AdminPanel>
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

function defaultDebugExpiry() {
  return new Date(Date.now() + 60 * 60 * 1000).toISOString();
}

function toLocalDateTime(value: Date) {
  if (Number.isNaN(value.getTime())) return '';
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}
