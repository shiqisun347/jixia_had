'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { ArrowUpRight, Bot, FileText, Pencil, Plus, Search, ShieldAlert } from 'lucide-react';
import { useDeferredValue, useEffect, useMemo, useState, type ReactNode } from 'react';
import Image from 'next/image';
import { useForm, useWatch } from 'react-hook-form';
import {
  type ColumnDef,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table';
import { z } from 'zod';

import {
  AdminActionItem,
  AdminActionMenu,
  AdminButton,
  AdminConfirmDialog,
  AdminDrawer,
  AdminSearch,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import { readableAdminError } from '@/features/admin/admin-api';
import { commitAdminAction } from '@/features/admin/commit-admin-action';
import type { AgentRow, VoiceRow } from '@/features/admin/admin-types';
import {
  AdminDataTable,
  AdminSortableHeader,
  AdminTableSkeleton,
} from '@/features/admin/admin-data-table';
import { useAdminCatalog } from '@/features/admin/use-admin-catalog';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';
import { submitCatalogSave } from '@/features/admin/submit-catalog-save';
import { useToast } from '@/components/ui/toast-provider';
import { AdminBulkActions } from '@/features/admin/admin-bulk-actions';
import { requestJson } from '@/lib/auth-api';
import { avatarAssetUrl } from '@/lib/avatar-catalog';

const agentFormSchema = z.object({
  name: z.string().trim().min(1, '请输入 Agent 名称').max(128),
  model_profile_id: z.string().uuid('请选择 LLM 模型'),
  voice_profile_id: z.string().uuid('请选择 TTS 音色'),
  system_prompt: z.string().max(20_000),
  debater_prompt: z.string().max(20_000),
  temperature: z.number().min(0, '生成温度不能小于 0').max(2, '生成温度不能大于 2'),
});

type AgentFormValues = z.infer<typeof agentFormSchema>;
type DrawerState = { mode: 'create' | 'edit' | 'view'; agent: AgentRow | null } | null;

export default function AdminAgentsPage() {
  const { catalog, reload, error, loading } = useAdminCatalog();
  const { showToast } = useToast();
  const { isSubmitting, submit } = useAdminSubmit();
  const [drawer, setDrawer] = useState<DrawerState>(null);
  const [statusTarget, setStatusTarget] = useState<AgentRow | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'ALL' | 'ENABLED' | 'DISABLED'>('ALL');
  const [sorting, setSorting] = useState<SortingState>([{ id: 'name', desc: false }]);
  const deferredQuery = useDeferredValue(query);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setQuery(params.get('q') ?? '');
    const nextStatus = params.get('status');
    if (nextStatus === 'ENABLED' || nextStatus === 'DISABLED') setStatus(nextStatus);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set('q', query.trim());
    else params.delete('q');
    if (status === 'ALL') params.delete('status');
    else params.set('status', status);
    const next = params.toString();
    window.history.replaceState(null, '', next ? `/admin/agents?${next}` : '/admin/agents');
  }, [query, status]);

  const enabledModels = useMemo(
    () => catalog.models.filter((model) => model.status === 'ENABLED'),
    [catalog.models],
  );
  const enabledVoices = useMemo(
    () => catalog.voices.filter((voice) => voice.kind === 'AGENT' && voice.status === 'ENABLED'),
    [catalog.voices],
  );
  const modelNames = useMemo(
    () => new Map(catalog.models.map((model) => [model.id, model.name])),
    [catalog.models],
  );
  const voiceNames = useMemo(
    () => new Map(catalog.voices.map((voice) => [voice.id, voice.name])),
    [catalog.voices],
  );
  const filteredAgents = useMemo(() => {
    const needle = deferredQuery.trim().toLocaleLowerCase();
    return catalog.agents.filter((agent) => {
      const matchesStatus = status === 'ALL' || agent.status === status;
      const matchesQuery =
        !needle ||
        [agent.name, modelNames.get(agent.model_profile_id), voiceNames.get(agent.voice_profile_id)]
          .filter(Boolean)
          .some((value) => value?.toLocaleLowerCase().includes(needle));
      return matchesStatus && matchesQuery;
    });
  }, [catalog.agents, deferredQuery, modelNames, status, voiceNames]);

  async function saveAgent(values: AgentFormValues, currentAgent: AgentRow | null) {
    try {
      const result = await submitCatalogSave(
        submit,
        () =>
          requestJson(
            currentAgent
              ? `/api/admin/catalog/agents/${currentAgent.id}`
              : '/api/admin/catalog/agents',
            {
              method: currentAgent ? 'PATCH' : 'POST',
              body: JSON.stringify({
                name: values.name,
                model_profile_id: values.model_profile_id,
                voice_profile_id: values.voice_profile_id,
                system_prompt: values.system_prompt,
                debater_prompt: values.debater_prompt,
                generation_params: { temperature: values.temperature },
              }),
            },
          ),
        reload,
      );
      if (result === 'not_started') return;
      setDrawer(null);
      showToast({
        message: currentAgent ? 'Agent 配置已更新。' : 'Agent 配置已创建。',
        tone: 'success',
      });
      if (result === 'refresh_failed') {
        showToast({ message: 'Agent 配置已保存，但目录未同步；请重新进入页面。', tone: 'info' });
      }
    } catch (requestError: unknown) {
      showToast({ message: readableAdminError(requestError), tone: 'error' });
    }
  }

  async function toggleStatus() {
    if (!statusTarget) return;
    const target = statusTarget;
    const nextStatus = target.status === 'ENABLED' ? 'DISABLED' : 'ENABLED';
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(
          () =>
            requestJson(`/api/admin/catalog/agents/${target.id}/status`, {
              method: 'PATCH',
              body: JSON.stringify({ status: nextStatus }),
            }),
          reload,
        );
      });
      if (!submitted) return;
      setStatusTarget(null);
      showToast({
        message: nextStatus === 'ENABLED' ? `${target.name} 已启用。` : `${target.name} 已停用。`,
        tone: 'success',
      });
      if (refreshResult.value === 'refresh_failed') {
        showToast({ message: 'Agent 状态已修改，但目录未同步；请重新进入页面。', tone: 'info' });
      }
    } catch (requestError: unknown) {
      showToast({ message: readableAdminError(requestError), tone: 'error' });
    }
  }

  const columns = useMemo<ColumnDef<AgentRow>[]>(
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
        header: ({ header }) => <AdminSortableHeader header={header}>Agent</AdminSortableHeader>,
        cell: ({ row }) => (
          <div className="flex min-w-40 items-center gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-50 text-blue-700">
              <Bot className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="truncate font-black text-slate-950">{row.original.name}</p>
              <p className="mt-0.5 truncate text-[0.68rem] text-slate-600">
                {row.original.id.slice(0, 8)}
              </p>
            </div>
          </div>
        ),
      },
      {
        id: 'model',
        accessorFn: (row) => modelNames.get(row.model_profile_id) ?? '',
        header: ({ header }) => <AdminSortableHeader header={header}>LLM 模型</AdminSortableHeader>,
        cell: ({ getValue }) => (
          <span className="font-semibold">{getValue<string>() || '未知模型'}</span>
        ),
      },
      {
        id: 'voice',
        accessorFn: (row) => voiceNames.get(row.voice_profile_id) ?? '',
        header: ({ header }) => <AdminSortableHeader header={header}>TTS 音色</AdminSortableHeader>,
        cell: ({ getValue }) => (
          <span className="font-semibold">{getValue<string>() || '未知音色'}</span>
        ),
      },
      {
        id: 'temperature',
        accessorFn: (row) => Number(row.generation_params?.temperature ?? 0.7),
        header: ({ header }) => <AdminSortableHeader header={header}>参数</AdminSortableHeader>,
        cell: ({ getValue }) => (
          <span className="font-mono text-xs text-slate-500">
            T {getValue<number>().toFixed(1)}
          </span>
        ),
      },
      {
        accessorKey: 'status',
        header: ({ header }) => <AdminSortableHeader header={header}>状态</AdminSortableHeader>,
        cell: ({ getValue }) => <StatusBadge status={getValue<string>()} />,
      },
      {
        id: 'actions',
        enableSorting: false,
        header: () => <span className="block text-right">操作</span>,
        cell: ({ row }) => (
          <div className="flex justify-end">
            <AdminActionMenu>
              <AdminActionItem onSelect={() => setDrawer({ mode: 'view', agent: row.original })}>
                查看详情
              </AdminActionItem>
              <AdminActionItem onSelect={() => setDrawer({ mode: 'edit', agent: row.original })}>
                <Pencil className="mr-2 size-3.5" aria-hidden="true" /> 编辑配置
              </AdminActionItem>
              <AdminActionItem
                onSelect={() => setStatusTarget(row.original)}
                tone={row.original.status === 'ENABLED' ? 'danger' : 'default'}
              >
                {row.original.status === 'ENABLED' ? '停用 Agent' : '启用 Agent'}
              </AdminActionItem>
            </AdminActionMenu>
          </div>
        ),
      },
    ],
    [modelNames, selectedIds, voiceNames],
  );

  // TanStack Table intentionally exposes stateful callbacks that React Compiler cannot memoize.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: filteredAgents,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="space-y-6">
      <AdminPageHeader
        eyebrow="AGENT PROFILES"
        title="Agent 管理"
        description="配置模型、提示词和 TTS 音色。活动比赛引用期间，影响运行的字段会自动锁定。"
        actions={
          <AdminButton onClick={() => setDrawer({ mode: 'create', agent: null })} tone="primary">
            <Plus className="size-4" aria-hidden="true" /> 创建 Agent
          </AdminButton>
        }
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel
        title="配置目录"
        description={`${filteredAgents.length} 个结果 · 共 ${catalog.agents.length} 个 Agent`}
        action={
          <span className="hidden items-center gap-1.5 text-xs font-bold text-slate-600 sm:inline-flex">
            <ShieldAlert className="size-3.5" aria-hidden="true" /> 活动引用自动锁定
          </span>
        }
      >
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <AdminSearch
            label="搜索 Agent、模型或音色"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Agent、模型或音色"
            value={query}
          />
          <AdminSelect
            label="状态筛选"
            onChange={(event) => setStatus(event.target.value as typeof status)}
            value={status}
          >
            <option value="ALL">全部状态</option>
            <option value="ENABLED">已启用</option>
            <option value="DISABLED">已停用</option>
          </AdminSelect>
          <span className="ml-auto hidden text-xs font-semibold text-slate-600 lg:inline-flex">
            <Search className="mr-1.5 size-3.5" aria-hidden="true" /> 支持名称、模型和音色搜索
          </span>
        </div>
        <AdminBulkActions
          ids={selectedIds}
          onClear={() => setSelectedIds([])}
          onCompleted={reload}
          resource="agent"
        />
        {loading ? (
          <AdminTableSkeleton />
        ) : (
          <AdminDataTable
            emptyDescription={
              query || status !== 'ALL' ? '请调整搜索或筛选条件。' : '点击右上角创建第一个 Agent。'
            }
            emptyTitle={query || status !== 'ALL' ? '没有匹配的 Agent' : '还没有 Agent 配置'}
            table={table}
          />
        )}
      </AdminPanel>
      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <FileText className="size-3.5 text-blue-600" aria-hidden="true" />{' '}
          修改后新房间使用最新配置
        </span>
        <a
          className="inline-flex items-center gap-1 font-bold text-blue-700 hover:text-blue-900"
          href="/admin/models"
        >
          管理模型 <ArrowUpRight className="size-3.5" aria-hidden="true" />
        </a>
      </div>
      <AgentDrawer
        agent={drawer?.agent ?? null}
        allVoices={catalog.voices}
        enabledModels={enabledModels}
        enabledVoices={enabledVoices}
        mode={drawer?.mode ?? null}
        saving={isSubmitting}
        onOpenChange={(open) => {
          if (open || !isSubmitting) setDrawer(open ? drawer : null);
        }}
        onSave={saveAgent}
      />
      <AdminConfirmDialog
        confirmLabel={statusTarget?.status === 'ENABLED' ? '确认停用' : '确认启用'}
        description={
          statusTarget?.status === 'ENABLED'
            ? `停用后，${statusTarget.name} 不会被自动填充到新房间；已有比赛快照不受影响。`
            : `确认重新启用 ${statusTarget?.name ?? '该 Agent'}？`
        }
        loading={isSubmitting}
        onConfirm={() => void toggleStatus()}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setStatusTarget(null);
        }}
        open={Boolean(statusTarget)}
        title={statusTarget?.status === 'ENABLED' ? '停用 Agent？' : '启用 Agent？'}
      />
    </div>
  );
}

function AgentDrawer({
  mode,
  agent,
  enabledModels,
  enabledVoices,
  allVoices,
  onOpenChange,
  onSave,
  saving,
}: {
  mode: 'create' | 'edit' | 'view' | null;
  agent: AgentRow | null;
  enabledModels: Array<{ id: string; name: string }>;
  enabledVoices: VoiceRow[];
  allVoices: VoiceRow[];
  onOpenChange: (open: boolean) => void;
  onSave: (values: AgentFormValues, agent: AgentRow | null) => Promise<void>;
  saving: boolean;
}) {
  const editing = mode === 'edit' || mode === 'create';
  const form = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    defaultValues: {
      name: '',
      model_profile_id: '',
      voice_profile_id: '',
      system_prompt: '',
      debater_prompt: '',
      temperature: 0.7,
    },
  });

  useEffect(() => {
    form.reset({
      name: agent?.name ?? '',
      model_profile_id: agent?.model_profile_id ?? '',
      voice_profile_id: agent?.voice_profile_id ?? '',
      system_prompt: agent?.system_prompt ?? '',
      debater_prompt: agent?.debater_prompt ?? '',
      temperature: Number(agent?.generation_params?.temperature ?? 0.7),
    });
  }, [agent, form]);
  const selectedVoiceId = useWatch({ control: form.control, name: 'voice_profile_id' });
  const selectedVoice = enabledVoices.find((item) => item.id === selectedVoiceId);

  return (
    <AdminDrawer
      description={
        mode === 'view'
          ? '查看当前配置和提示词；历史比赛使用启动时快照。'
          : '保存前请确认模型、音色和提示词均已完成配置。'
      }
      footer={
        editing ? (
          <div className="flex justify-end gap-2">
            <AdminButton disabled={saving} onClick={() => onOpenChange(false)} type="button">
              取消
            </AdminButton>
            <AdminButton
              loading={saving || form.formState.isSubmitting}
              onClick={() =>
                void form.handleSubmit((values) => onSave(values, mode === 'edit' ? agent : null))()
              }
              tone="primary"
              type="button"
            >
              保存配置
            </AdminButton>
          </div>
        ) : null
      }
      onOpenChange={onOpenChange}
      open={Boolean(mode)}
      title={
        mode === 'create'
          ? '创建 Agent'
          : mode === 'edit'
            ? `编辑 Agent · ${agent?.name ?? ''}`
            : `Agent 详情 · ${agent?.name ?? ''}`
      }
    >
      {mode === 'view' && agent ? (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3">
            <DetailItem label="头像">
              <Image
                alt={`${agent.name}的头像`}
                className="jx-identity-avatar size-14 rounded-full object-cover"
                height={56}
                src={avatarAssetUrl(
                  allVoices.find((item) => item.id === agent.voice_profile_id)?.avatar_key ??
                    agent.avatar_key ??
                    'agent-01',
                )}
                width={56}
              />
            </DetailItem>
            <DetailItem label="状态">
              <StatusBadge status={agent.status} />
            </DetailItem>
            <DetailItem label="生成温度">
              {String(agent.generation_params?.temperature ?? 0.7)}
            </DetailItem>
            <DetailItem label="LLM 模型">
              {enabledModels.find((item) => item.id === agent.model_profile_id)?.name ??
                '已停用模型'}
            </DetailItem>
            <DetailItem label="TTS 音色">
              {allVoices.find((item) => item.id === agent.voice_profile_id)?.name ?? '已停用音色'}
            </DetailItem>
          </div>
          <PromptBlock label="系统提示词" value={agent.system_prompt ?? '未设置'} />
          <PromptBlock label="辩手提示词" value={agent.debater_prompt ?? '未设置'} />
        </div>
      ) : editing ? (
        <form
          className="space-y-5"
          onSubmit={form.handleSubmit((values) => onSave(values, mode === 'edit' ? agent : null))}
        >
          <FormFieldError message={form.formState.errors.root?.message} />
          <label className="grid gap-1.5 text-xs font-bold text-slate-600">
            Agent 名称
            <input {...form.register('name')} className="admin-field" placeholder="例如：乾元" />
            <FormFieldError message={form.formState.errors.name?.message} />
          </label>
          <label className="grid gap-1.5 text-xs font-bold text-slate-600">
            LLM 模型
            <select {...form.register('model_profile_id')} className="admin-field">
              <option value="">请选择启用中的模型</option>
              {enabledModels.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <FormFieldError message={form.formState.errors.model_profile_id?.message} />
          </label>
          <label className="grid gap-1.5 text-xs font-bold text-slate-600">
            TTS 音色
            <select {...form.register('voice_profile_id')} className="admin-field">
              <option value="">请选择启用中的音色</option>
              {enabledVoices.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <FormFieldError message={form.formState.errors.voice_profile_id?.message} />
          </label>
          {selectedVoice?.avatar_key ? (
            <div className="flex items-center gap-3 rounded-lg border border-blue-100 bg-blue-50 p-3">
              <Image
                alt={`${selectedVoice.name}对应的 Agent 头像`}
                className="jx-identity-avatar size-12 rounded-full object-cover"
                height={48}
                src={avatarAssetUrl(selectedVoice.avatar_key)}
                width={48}
              />
              <div>
                <p className="text-sm font-bold text-slate-900">头像由 TTS 音色决定</p>
                <p className="text-xs text-slate-600">切换音色后自动同步，无需单独设置。</p>
              </div>
            </div>
          ) : null}
          <label className="grid gap-1.5 text-xs font-bold text-slate-600">
            生成温度
            <input
              {...form.register('temperature', { valueAsNumber: true })}
              className="admin-field"
              max="2"
              min="0"
              step="0.1"
              type="number"
            />
            <FormFieldError message={form.formState.errors.temperature?.message} />
          </label>
          <label className="grid gap-1.5 text-xs font-bold text-slate-600">
            系统提示词
            <textarea
              {...form.register('system_prompt')}
              className="admin-field min-h-32 resize-y"
              placeholder="角色、边界和输出约束"
            />
            <FormFieldError message={form.formState.errors.system_prompt?.message} />
          </label>
          <label className="grid gap-1.5 text-xs font-bold text-slate-600">
            辩手提示词
            <textarea
              {...form.register('debater_prompt')}
              className="admin-field min-h-32 resize-y"
              placeholder="辩论策略、表达方式和位置职责"
            />
            <FormFieldError message={form.formState.errors.debater_prompt?.message} />
          </label>
        </form>
      ) : null}
    </AdminDrawer>
  );
}

function DetailItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <p className="text-[0.65rem] font-black tracking-wide text-slate-600">{label}</p>
      <div className="mt-2 text-sm font-bold text-slate-800">{children}</div>
    </div>
  );
}

function PromptBlock({ label, value }: { label: string; value: string }) {
  return (
    <section>
      <h3 className="text-xs font-black text-slate-600">{label}</h3>
      <p className="mt-2 whitespace-pre-wrap rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-700">
        {value}
      </p>
    </section>
  );
}

function FormFieldError({ message }: { message?: string }) {
  return message ? <p className="text-xs font-semibold text-red-600">{message}</p> : null;
}
