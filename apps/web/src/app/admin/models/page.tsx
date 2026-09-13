'use client';

import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { FlaskConical, KeyRound, Pencil, Plus } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';

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
import type { ModelRow } from '@/features/admin/admin-types';
import { commitAdminAction } from '@/features/admin/commit-admin-action';
import { useAdminCatalog } from '@/features/admin/use-admin-catalog';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';
import { submitCatalogSave } from '@/features/admin/submit-catalog-save';
import { requestJson } from '@/lib/auth-api';
import { useSingleFlight } from '@/hooks/use-single-flight';
import { AdminBulkActions } from '@/features/admin/admin-bulk-actions';

export default function AdminModelsPage() {
  const { catalog, reload, error, loading } = useAdminCatalog();
  const toast = useOptionalToast();
  const { isSubmitting, submit } = useAdminSubmit();
  const { run: runProbe } = useSingleFlight();
  const [drawer, setDrawer] = useState<ModelRow | 'create' | null>(null);
  const [statusTarget, setStatusTarget] = useState<ModelRow | null>(null);
  const [keyTarget, setKeyTarget] = useState<ModelRow | null>(null);
  const [testingId, setTestingId] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const testModel = useCallback(
    async (model: ModelRow) => {
      await runProbe(async () => {
        setTestingId(model.id);
        try {
          const result = await requestJson<{ first_token_latency_ms: number }>(
            `/api/admin/models/${model.id}/test`,
            { method: 'POST', body: '{}' },
          );
          toast?.showToast({
            message: `${model.name} 连接成功，首 Token ${Math.round(result.first_token_latency_ms)}ms。`,
            tone: 'success',
          });
        } catch (requestError: unknown) {
          toast?.showToast({ message: readableAdminError(requestError), tone: 'error' });
        } finally {
          setTestingId('');
        }
      });
    },
    [runProbe, toast],
  );

  async function toggleStatus(target = statusTarget) {
    if (!target) return;
    const next = target.status === 'ENABLED' ? 'DISABLED' : 'ENABLED';
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(
          () =>
            requestJson(`/api/admin/catalog/models/${target.id}/status`, {
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
          message: '模型状态已修改，但目录未同步；请重新进入页面。',
          tone: 'info',
        });
      }
    } catch (requestError: unknown) {
      toast?.showToast({ message: readableAdminError(requestError), tone: 'error' });
    }
  }

  const columns = useMemo<ColumnDef<ModelRow>[]>(
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
        header: '模型',
        cell: ({ row }) => (
          <div>
            <p className="font-black text-slate-950">{row.original.name}</p>
            <p className="text-xs text-slate-600">{row.original.model_id || '未设置模型 ID'}</p>
          </div>
        ),
      },
      {
        accessorKey: 'base_url',
        header: 'Base URL',
        cell: ({ row }) => (
          <span className="block max-w-80 truncate font-mono text-xs text-slate-600">
            {row.original.base_url || '—'}
          </span>
        ),
      },
      {
        accessorKey: 'max_concurrency',
        header: '并发',
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.max_concurrency ?? 50}</span>
        ),
      },
      {
        accessorKey: 'api_key_last4',
        header: 'API Key',
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-600">
            {row.original.api_key_last4 ? `•••• ${row.original.api_key_last4}` : '未配置'}
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
                编辑配置
              </AdminActionItem>
              <AdminActionItem
                disabled={Boolean(testingId)}
                onSelect={() => void testModel(row.original)}
              >
                <FlaskConical className="mr-2 size-3.5" aria-hidden="true" />
                {testingId === row.original.id ? '测试中…' : '测试连接'}
              </AdminActionItem>
              <AdminActionItem onSelect={() => setKeyTarget(row.original)}>
                <KeyRound className="mr-2 size-3.5" aria-hidden="true" />
                轮换 API Key
              </AdminActionItem>
              <AdminActionItem
                onSelect={() => setStatusTarget(row.original)}
                tone={row.original.status === 'ENABLED' ? 'danger' : 'default'}
              >
                {row.original.status === 'ENABLED' ? '停用模型' : '启用模型'}
              </AdminActionItem>
            </AdminActionMenu>
          </div>
        ),
      },
    ],
    [selectedIds, testModel, testingId],
  );
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: catalog.models,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <AdminButton onClick={() => setDrawer('create')} tone="primary">
            <Plus className="size-4" aria-hidden="true" />
            导入模型
          </AdminButton>
        }
        description="管理 OpenAI 兼容模型、连接参数和 API Key；密钥只显示末四位。"
        eyebrow="MODEL PROVIDERS"
        title="模型设置"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel title="模型目录" description={`${catalog.models.length} 个模型配置`}>
        {loading ? (
          <AdminTableSkeleton />
        ) : (
          <>
            <AdminBulkActions
              ids={selectedIds}
              onClear={() => setSelectedIds([])}
              onCompleted={reload}
              resource="model"
            />
            <AdminDataTable
              table={table}
              emptyTitle="还没有模型配置"
              emptyDescription="点击右上角导入第一个模型。"
            />
          </>
        )}
      </AdminPanel>
      <ModelDrawer
        key={drawer === 'create' ? 'create' : (drawer?.id ?? 'closed')}
        model={drawer === 'create' ? null : drawer}
        open={Boolean(drawer)}
        onClose={() => setDrawer(null)}
        onSaved={reload}
      />
      <ApiKeyDrawer
        key={keyTarget?.id ?? 'closed-key'}
        model={keyTarget}
        onClose={() => setKeyTarget(null)}
        onSaved={reload}
      />
      <AdminConfirmDialog
        confirmLabel={statusTarget?.status === 'ENABLED' ? '确认停用' : '确认启用'}
        description={
          statusTarget ? `${statusTarget.name} 的状态将改变；活动比赛引用时服务端会拒绝操作。` : ''
        }
        onConfirm={() => void toggleStatus(statusTarget)}
        loading={isSubmitting}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setStatusTarget(null);
        }}
        open={Boolean(statusTarget)}
        title={statusTarget?.status === 'ENABLED' ? '停用模型？' : '启用模型？'}
      />
    </div>
  );
}

function ModelDrawer({
  model,
  open,
  onClose,
  onSaved,
}: {
  model: ModelRow | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => Promise<{ isError: boolean }>;
}) {
  const toast = useOptionalToast();
  const { isSubmitting: saving, submit } = useAdminSubmit();
  const [name, setName] = useState(model?.name ?? '');
  const [configRef, setConfigRef] = useState(model?.config_ref ?? '');
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? '');
  const [modelId, setModelId] = useState(model?.model_id ?? '');
  const [apiKey, setApiKey] = useState('');
  const [maxConcurrency, setMaxConcurrency] = useState(model?.max_concurrency ?? 50);
  const [tokenPerChar, setTokenPerChar] = useState(model?.token_per_char ?? 1);
  const [temperature, setTemperature] = useState(
    model?.capability_schema?.temperature ?? { minimum: 0, maximum: 2 },
  );
  const [topP, setTopP] = useState(model?.capability_schema?.top_p ?? { minimum: 0, maximum: 1 });
  const [maxTokens, setMaxTokens] = useState(
    model?.capability_schema?.max_tokens ?? { minimum: 1, maximum: 32768 },
  );
  const [temperatureEnabled, setTemperatureEnabled] = useState(
    model?.capability_schema?.temperature !== null,
  );
  const [topPEnabled, setTopPEnabled] = useState(model?.capability_schema?.top_p !== null);
  const [maxTokensEnabled, setMaxTokensEnabled] = useState(
    model?.capability_schema?.max_tokens !== null,
  );
  async function save() {
    try {
      const result = await submitCatalogSave(
        submit,
        () =>
          requestJson(
            model ? `/api/admin/catalog/models/${model.id}` : '/api/admin/catalog/models',
            {
              method: model ? 'PATCH' : 'POST',
              body: JSON.stringify({
                name: name.trim(),
                config_ref: configRef.trim(),
                base_url: baseUrl.trim() || null,
                model_id: modelId.trim() || null,
                api_key: model ? null : apiKey || null,
                max_concurrency: maxConcurrency,
                token_per_char: tokenPerChar,
                generation_params: model?.generation_params ?? {},
                capability_schema: {
                  temperature: temperatureEnabled ? temperature : null,
                  top_p: topPEnabled ? topP : null,
                  max_tokens: maxTokensEnabled ? maxTokens : null,
                },
              }),
            },
          ),
        onSaved,
      );
      if (result === 'not_started') return;
      onClose();
      toast?.showToast({
        message: model ? '模型配置已更新。' : '模型配置已创建。',
        tone: 'success',
      });
      if (result === 'refresh_failed') {
        toast?.showToast({
          message: '模型配置已保存，但目录未同步；请重新进入页面。',
          tone: 'info',
        });
      }
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }
  return (
    <AdminDrawer
      description={
        model ? 'API Key 通过列表中的独立轮换操作维护。' : '默认使用流式输出，单模型并发上限为 50。'
      }
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton disabled={saving} onClick={onClose}>
            取消
          </AdminButton>
          <AdminButton loading={saving} onClick={() => void save()} tone="primary">
            保存配置
          </AdminButton>
        </div>
      }
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
      open={open}
      title={model ? `编辑模型 · ${model.name}` : '导入模型'}
    >
      <div className="space-y-4">
        <Field label="配置名称" value={name} onChange={setName} />
        <Field label="配置引用" value={configRef} onChange={setConfigRef} />
        <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} />
        <Field label="模型 ID" value={modelId} onChange={setModelId} />
        {!model ? (
          <Field label="API Key" type="password" value={apiKey} onChange={setApiKey} />
        ) : null}
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="最大并发"
            type="number"
            value={String(maxConcurrency)}
            onChange={(value) => setMaxConcurrency(Number(value))}
          />
          <Field
            label="Token/字"
            type="number"
            value={String(tokenPerChar)}
            onChange={(value) => setTokenPerChar(Number(value))}
          />
        </div>
        <fieldset className="space-y-3 border-t border-slate-200 pt-4">
          <legend className="text-sm font-black text-slate-900">可用生成参数</legend>
          <p className="text-xs leading-5 text-slate-600">
            Agent 只能配置此处启用且位于范围内的参数。
          </p>
          <CapabilityRange
            checked={temperatureEnabled}
            label="Temperature"
            maximum={temperature.maximum}
            minimum={temperature.minimum}
            onCheckedChange={setTemperatureEnabled}
            onChange={setTemperature}
            step="0.1"
          />
          <CapabilityRange
            checked={topPEnabled}
            label="Top P"
            maximum={topP.maximum}
            minimum={topP.minimum}
            onCheckedChange={setTopPEnabled}
            onChange={setTopP}
            step="0.1"
          />
          <CapabilityRange
            checked={maxTokensEnabled}
            label="最大 Token 数"
            maximum={maxTokens.maximum}
            minimum={maxTokens.minimum}
            onCheckedChange={setMaxTokensEnabled}
            onChange={setMaxTokens}
            step="1"
          />
        </fieldset>
      </div>
    </AdminDrawer>
  );
}

function ApiKeyDrawer({
  model,
  onClose,
  onSaved,
}: {
  model: ModelRow | null;
  onClose: () => void;
  onSaved: () => Promise<{ isError: boolean }>;
}) {
  const toast = useOptionalToast();
  const { isSubmitting, submit } = useAdminSubmit();
  const [apiKey, setApiKey] = useState('');

  async function rotate() {
    if (!model || !apiKey) return;
    try {
      const submitted = await submit(() =>
        requestJson(`/api/admin/catalog/models/${model.id}/api-key`, {
          method: 'POST',
          body: JSON.stringify({ api_key: apiKey }),
        }).then(() => undefined),
      );
      if (!submitted) return;
      await onSaved();
      onClose();
      toast?.showToast({ message: `${model.name} 的 API Key 已轮换。`, tone: 'success' });
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }

  return (
    <AdminDrawer
      description="旧密钥不会显示；保存后立即用于该模型的新请求。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton disabled={isSubmitting} onClick={onClose}>
            取消
          </AdminButton>
          <AdminButton
            disabled={!apiKey}
            loading={isSubmitting}
            onClick={() => void rotate()}
            tone="primary"
          >
            确认轮换
          </AdminButton>
        </div>
      }
      onOpenChange={(open) => {
        if (!open && !isSubmitting) onClose();
      }}
      open={Boolean(model)}
      title={`轮换 API Key · ${model?.name ?? ''}`}
    >
      <Field label="新 API Key" onChange={setApiKey} type="password" value={apiKey} />
    </AdminDrawer>
  );
}

function CapabilityRange({
  checked,
  label,
  minimum,
  maximum,
  onCheckedChange,
  onChange,
  step,
}: {
  checked: boolean;
  label: string;
  minimum: number;
  maximum: number;
  onCheckedChange: (checked: boolean) => void;
  onChange: (range: { minimum: number; maximum: number }) => void;
  step: string;
}) {
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <label className="flex items-center gap-2 text-xs font-bold text-slate-700">
        <input
          checked={checked}
          onChange={(event) => onCheckedChange(event.target.checked)}
          type="checkbox"
        />
        {label}
      </label>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field
          disabled={!checked}
          label={`${label} 最小值`}
          onChange={(value) => onChange({ minimum: Number(value), maximum })}
          step={step}
          type="number"
          value={String(minimum)}
        />
        <Field
          disabled={!checked}
          label={`${label} 最大值`}
          onChange={(value) => onChange({ minimum, maximum: Number(value) })}
          step={step}
          type="number"
          value={String(maximum)}
        />
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = 'text',
  disabled = false,
  step,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  disabled?: boolean;
  step?: string;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <input
        className="admin-field"
        disabled={disabled}
        step={step}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
