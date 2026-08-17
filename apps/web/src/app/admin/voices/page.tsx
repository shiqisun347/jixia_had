'use client';

import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Pencil, Play, Plus } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import Image from 'next/image';

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
import type { VoiceRow } from '@/features/admin/admin-types';
import { commitAdminAction } from '@/features/admin/commit-admin-action';
import { useAdminCatalog } from '@/features/admin/use-admin-catalog';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';
import { submitCatalogSave } from '@/features/admin/submit-catalog-save';
import { playVoicePreview } from '@/features/admin/play-voice-preview';
import { requestJson } from '@/lib/auth-api';
import { AGENT_AVATAR_KEYS, avatarAssetUrl } from '@/lib/avatar-catalog';
import { useSingleFlight } from '@/hooks/use-single-flight';
import { AdminBulkActions } from '@/features/admin/admin-bulk-actions';

export default function AdminVoicesPage() {
  const { catalog, reload, error, loading } = useAdminCatalog();
  const toast = useOptionalToast();
  const { isSubmitting, submit } = useAdminSubmit();
  const { run: runPreview } = useSingleFlight();
  const [drawer, setDrawer] = useState<VoiceRow | 'create' | null>(null);
  const [statusTarget, setStatusTarget] = useState<VoiceRow | null>(null);
  const [previewing, setPreviewing] = useState('');
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const preview = useCallback(
    async (voice: VoiceRow) => {
      await runPreview(async () => {
        setPreviewing(voice.id);
        try {
          try {
            const result = await playVoicePreview({
              cachedUrl: previewUrls[voice.id],
              generate: () =>
                requestJson(`/api/admin/voices/${voice.id}/preview`, {
                  method: 'POST',
                  body: '{}',
                }),
              makeUrl: () => `/api/admin/voices/${voice.id}/preview?ts=${Date.now()}`,
              play: (url) => new Audio(url).play(),
            });
            setPreviewUrls((current) => ({ ...current, [voice.id]: result.url }));
            toast?.showToast(
              result.played
                ? { message: `${voice.name} 试听已开始。`, tone: 'success' }
                : {
                    message: `${voice.name} 试听已生成；浏览器阻止了自动播放，请再次点击试听。`,
                    tone: 'info',
                  },
            );
          } catch (requestError: unknown) {
            toast?.showToast({ message: readableAdminError(requestError), tone: 'error' });
          }
        } finally {
          setPreviewing('');
        }
      });
    },
    [previewUrls, runPreview, toast],
  );
  async function toggleStatus(target = statusTarget) {
    if (!target) return;
    const next = target.status === 'ENABLED' ? 'DISABLED' : 'ENABLED';
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(
          () =>
            requestJson(`/api/admin/catalog/voices/${target.id}/status`, {
              method: 'PATCH',
              body: JSON.stringify({ status: next }),
            }),
          reload,
        );
      });
      if (!submitted) return;
      setStatusTarget(null);
      toast?.showToast({
        message: `${target.name} 已${next === 'ENABLED' ? '启用' : '停用'}。`,
        tone: 'success',
      });
      if (refreshResult.value === 'refresh_failed') {
        toast?.showToast({
          message: '音色状态已修改，但目录未同步；请重新进入页面。',
          tone: 'info',
        });
      }
    } catch (requestError: unknown) {
      toast?.showToast({ message: readableAdminError(requestError), tone: 'error' });
    }
  }
  const columns = useMemo<ColumnDef<VoiceRow>[]>(
    () => [
      {
        id: 'select',
        header: '选择',
        cell: ({ row }) => (
          <input
            aria-label={`选择 ${row.original.name}`}
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
        accessorKey: 'name',
        header: '音色',
        cell: ({ row }) => (
          <div className="flex items-center gap-3">
            {row.original.kind === 'AGENT' && row.original.avatar_key ? (
              <Image
                alt=""
                className="jx-identity-avatar size-10 rounded-full object-cover"
                height={40}
                src={avatarAssetUrl(row.original.avatar_key)}
                width={40}
              />
            ) : null}
            <div>
              <p className="font-black text-slate-950">{row.original.name}</p>
              <p className="text-xs text-slate-600">
                {row.original.kind === 'HOST' ? '主持' : 'Agent'} · {row.original.provider_voice}
              </p>
            </div>
          </div>
        ),
      },
      {
        accessorKey: 'rate',
        header: '语速',
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.rate.toFixed(2)}×</span>
        ),
      },
      {
        accessorKey: 'chars_per_second',
        header: '字/秒',
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-600">
            {row.original.chars_per_second?.toFixed(2) ?? '自动'}
          </span>
        ),
      },
      {
        accessorKey: 'playback_gain',
        header: '播放增益',
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-600">
            {(row.original.playback_gain ?? 1).toFixed(2)}×
          </span>
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
                编辑音色
              </AdminActionItem>
              <AdminActionItem
                disabled={Boolean(previewing)}
                onSelect={() => void preview(row.original)}
              >
                <Play className="mr-2 size-3.5" aria-hidden="true" />
                {previewing === row.original.id ? '生成中…' : '试听/重新生成'}
              </AdminActionItem>
              <AdminActionItem
                onSelect={() => setStatusTarget(row.original)}
                tone={row.original.status === 'ENABLED' ? 'danger' : 'default'}
              >
                {row.original.status === 'ENABLED' ? '停用音色' : '启用音色'}
              </AdminActionItem>
            </AdminActionMenu>
          </div>
        ),
      },
    ],
    [preview, previewing, selectedIds],
  );
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: catalog.voices,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <AdminButton onClick={() => setDrawer('create')} tone="primary">
            <Plus className="size-4" aria-hidden="true" />
            添加音色
          </AdminButton>
        }
        description="统一管理主持和 Agent 音色；编辑语速或供应商标识后，旧试听会自动失效。"
        eyebrow="VOICE PROFILES"
        title="语音方案"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel title="音色目录" description={`${catalog.voices.length} 个音色配置`}>
        {loading ? (
          <AdminTableSkeleton />
        ) : (
          <>
            <AdminBulkActions
              ids={selectedIds}
              onClear={() => setSelectedIds([])}
              onCompleted={reload}
              resource="voice"
            />
            <AdminDataTable
              table={table}
              emptyTitle="还没有音色配置"
              emptyDescription="点击右上角添加第一个音色。"
            />
          </>
        )}
      </AdminPanel>
      <VoiceDrawer
        key={drawer === 'create' ? 'create' : (drawer?.id ?? 'closed')}
        voice={drawer === 'create' ? null : drawer}
        open={Boolean(drawer)}
        onClose={() => setDrawer(null)}
        onSaved={reload}
      />
      <AdminConfirmDialog
        confirmLabel={statusTarget?.status === 'ENABLED' ? '确认停用' : '确认启用'}
        description="活动比赛引用的音色由服务端保护，停用只影响后续新房间。"
        onConfirm={() => void toggleStatus(statusTarget)}
        loading={isSubmitting}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setStatusTarget(null);
        }}
        open={Boolean(statusTarget)}
        title={statusTarget?.status === 'ENABLED' ? '停用音色？' : '启用音色？'}
      />
    </div>
  );
}

function VoiceDrawer({
  voice,
  open,
  onClose,
  onSaved,
}: {
  voice: VoiceRow | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => Promise<{ isError: boolean }>;
}) {
  const toast = useOptionalToast();
  const { isSubmitting: saving, submit } = useAdminSubmit();
  const [name, setName] = useState(voice?.name ?? '');
  const [kind, setKind] = useState(voice?.kind ?? 'AGENT');
  const [provider, setProvider] = useState(voice?.provider_voice ?? '');
  const [rate, setRate] = useState(voice?.rate ?? 1);
  const [chars, setChars] = useState(voice?.chars_per_second ?? 4.5);
  const [playbackGain, setPlaybackGain] = useState(voice?.playback_gain ?? 1);
  const [avatarKey, setAvatarKey] = useState(voice?.avatar_key ?? 'agent-01');
  async function save() {
    try {
      const result = await submitCatalogSave(
        submit,
        () =>
          requestJson(
            voice ? `/api/admin/catalog/voices/${voice.id}` : '/api/admin/catalog/voices',
            {
              method: voice ? 'PATCH' : 'POST',
              body: JSON.stringify({
                name: name.trim(),
                kind,
                provider_voice: provider.trim(),
                rate,
                chars_per_second: chars,
                playback_gain: playbackGain,
                avatar_key: kind === 'AGENT' ? avatarKey : null,
              }),
            },
          ),
        onSaved,
      );
      if (result === 'not_started') return;
      onClose();
      toast?.showToast({
        message: voice ? '音色配置已更新，试听缓存已失效。' : '音色配置已创建。',
        tone: 'success',
      });
      if (result === 'refresh_failed') {
        toast?.showToast({
          message: '音色配置已保存，但目录未同步；请重新进入页面。',
          tone: 'info',
        });
      }
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }
  return (
    <AdminDrawer
      description="建议 Agent 语速保持 0.85–1.20；保存后需要重新生成试听。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton disabled={saving} onClick={onClose}>
            取消
          </AdminButton>
          <AdminButton loading={saving} onClick={() => void save()} tone="primary">
            保存音色
          </AdminButton>
        </div>
      }
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
      open={open}
      title={voice ? `编辑音色 · ${voice.name}` : '添加音色'}
    >
      <div className="space-y-4">
        <Field label="名称" value={name} onChange={setName} />
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          类型
          <select
            className="admin-field"
            disabled={Boolean(voice)}
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="AGENT">Agent</option>
            <option value="HOST">主持</option>
          </select>
        </label>
        <Field label="供应商音色 ID" value={provider} onChange={setProvider} />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="语速"
            type="number"
            value={String(rate)}
            onChange={(value) => setRate(Number(value))}
          />
          <Field
            label="字/秒"
            type="number"
            value={String(chars)}
            onChange={(value) => setChars(Number(value))}
          />
          <Field
            label="播放增益"
            type="number"
            value={String(playbackGain)}
            onChange={(value) => setPlaybackGain(Number(value))}
          />
        </div>
        {kind === 'AGENT' ? (
          <fieldset>
            <legend className="text-xs font-bold text-slate-600">Agent 头像</legend>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              试听后选择与声音年龄感、性别表达和气质最匹配的形象。
            </p>
            <div className="mt-3 grid grid-cols-6 gap-2" role="radiogroup">
              {AGENT_AVATAR_KEYS.map((key) => (
                <button
                  aria-checked={avatarKey === key}
                  aria-label={`Agent 头像 ${key}`}
                  className={`avatar-preset ${avatarKey === key ? 'is-selected' : ''}`}
                  key={key}
                  onClick={() => setAvatarKey(key)}
                  role="radio"
                  type="button"
                >
                  <Image
                    alt=""
                    className="jx-identity-avatar size-full rounded-full object-cover"
                    height={64}
                    src={avatarAssetUrl(key)}
                    width={64}
                  />
                </button>
              ))}
            </div>
          </fieldset>
        ) : null}
      </div>
    </AdminDrawer>
  );
}
function Field({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <input
        className="admin-field"
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
