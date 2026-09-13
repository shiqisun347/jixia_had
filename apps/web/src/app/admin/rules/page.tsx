'use client';

import { ArrowRight, Plus } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useState, type ReactNode } from 'react';

import { useOptionalToast } from '@/components/ui/toast-provider';
import { readableAdminError } from '@/features/admin/admin-api';
import { AdminButton, AdminDrawer, StatusBadge } from '@/features/admin/admin-controls';
import { AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type { Catalog, RuleRow } from '@/features/admin/admin-types';
import { requestJson } from '@/lib/auth-api';

export default function AdminRulesPage() {
  const toast = useOptionalToast();
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [nextRules, nextCatalog] = await Promise.all([
        requestJson<RuleRow[]>('/api/admin/rules'),
        requestJson<Catalog>('/api/admin/catalog'),
      ]);
      setRules(nextRules);
      setCatalog(nextCatalog);
      setError('');
    } catch (requestError: unknown) {
      setError(readableAdminError(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<RuleRow[]>('/api/admin/rules'),
      requestJson<Catalog>('/api/admin/catalog'),
    ])
      .then(([nextRules, nextCatalog]) => {
        if (!active) return;
        setRules(nextRules);
        setCatalog(nextCatalog);
        setError('');
      })
      .catch((requestError: unknown) => {
        if (active) setError(readableAdminError(requestError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <AdminButton onClick={() => setCreating(true)} tone="primary">
            <Plus className="size-4" />
            新建规则
          </AdminButton>
        }
        description="管理规则当前配置。保存只影响之后创建的房间，已有房间继续使用冻结快照。"
        eyebrow="RULE DIRECTORY"
        title="赛制规则"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel description={`${rules.length} 套规则`} title="规则目录">
        {loading ? (
          <p className="py-10 text-center text-sm text-slate-500">正在加载规则…</p>
        ) : rules.length ? (
          <RuleTable rules={rules} />
        ) : (
          <AdminFeedback message="还没有赛制规则。" tone="info" />
        )}
      </AdminPanel>
      {creating ? (
        <CreateRuleDrawer
          catalog={catalog}
          onClose={() => setCreating(false)}
          onCreated={async () => {
            setCreating(false);
            await load();
          }}
          showMessage={(message, tone) => toast?.showToast({ message, tone })}
        />
      ) : null}
    </div>
  );
}

function RuleTable({ rules }: { rules: RuleRow[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs text-slate-600">
          <tr>
            <th className="px-4 py-3">名称</th>
            <th className="px-4 py-3">规模</th>
            <th className="px-4 py-3">估算时长</th>
            <th className="px-4 py-3">配置</th>
            <th className="px-4 py-3">状态</th>
            <th className="px-4 py-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {rules.map((rule) => (
            <tr key={rule.id}>
              <td className="px-4 py-3">
                <p className="font-bold text-slate-950">{rule.name}</p>
                <p className="mt-1 max-w-md truncate text-xs text-slate-500">
                  {rule.description || '无说明'}
                </p>
              </td>
              <td className="px-4 py-3">
                {rule.side_size}v{rule.side_size}
              </td>
              <td className="px-4 py-3">{Math.ceil(rule.estimated_seconds / 60)} 分钟</td>
              <td className="px-4 py-3">
                r{rule.config_revision}
                {rule.historical_read_only ? ' · 历史只读' : ''}
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={rule.status} />
              </td>
              <td className="px-4 py-3 text-right">
                <Link
                  className="inline-flex items-center gap-1 font-bold text-blue-700 hover:text-blue-900"
                  href={`/admin/rules/${rule.id}`}
                >
                  打开工作区
                  <ArrowRight className="size-3.5" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CreateRuleDrawer({
  catalog,
  onClose,
  onCreated,
  showMessage,
}: {
  catalog: Catalog | null;
  onClose: () => void;
  onCreated: () => Promise<void>;
  showMessage: (message: string, tone: 'success' | 'error') => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const hostVoices =
    catalog?.voices.filter((voice) => voice.kind === 'HOST' && voice.status === 'ENABLED') ?? [];
  const models = catalog?.models.filter((model) => model.status === 'ENABLED') ?? [];
  const [hostVoiceId, setHostVoiceId] = useState(hostVoices[0]?.id ?? '');
  const [modelId, setModelId] = useState(models[0]?.id ?? '');
  const [saving, setSaving] = useState(false);

  async function create() {
    if (!name.trim() || !hostVoiceId || !modelId) {
      showMessage('请填写名称并选择主持音色和默认模型', 'error');
      return;
    }
    setSaving(true);
    try {
      await requestJson('/api/admin/rules', {
        method: 'POST',
        body: JSON.stringify({
          host_voice_profile_id: hostVoiceId,
          default_agent_model_profile_id: modelId,
          topic_policy: 'BOTH',
          draft: {
            name: name.trim(),
            description: description.trim(),
            side_size: 4,
            stages: initialStages,
          },
        }),
      });
      setName('');
      setDescription('');
      showMessage('规则已创建，Agent 池和通用 Prompt 已自动生成', 'success');
      await onCreated();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <AdminDrawer
      description="先创建可用的 4v4 起始结构，再进入工作区编辑阶段与 Prompt。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton onClick={onClose}>取消</AdminButton>
          <AdminButton loading={saving} onClick={() => void create()} tone="primary">
            创建规则
          </AdminButton>
        </div>
      }
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open
      title="新建赛制规则"
    >
      <div className="space-y-4">
        <Field label="规则名称" onChange={setName} value={name} />
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          说明
          <textarea
            className="admin-field min-h-24"
            onChange={(event) => setDescription(event.target.value)}
            value={description}
          />
        </label>
        <SelectField label="主持音色" onChange={setHostVoiceId} value={hostVoiceId}>
          {hostVoices.map((voice) => (
            <option key={voice.id} value={voice.id}>
              {voice.name}
            </option>
          ))}
        </SelectField>
        <SelectField label="默认 Agent 模型" onChange={setModelId} value={modelId}>
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </SelectField>
        <p className="border-l-2 border-blue-500 pl-3 text-xs leading-5 text-slate-600">
          系统将为每个启用的 Agent 音色生成一个规则 Agent。人类仍可在等待房间占据任意席位。
        </p>
      </div>
    </AdminDrawer>
  );
}

const initialStages = [
  {
    name: '正方立论',
    stage_kind: 'FIXED_SPEECH',
    actions: [{ side: 'AFFIRMATIVE', seat_no: 1, duration_seconds: 90 }],
  },
  {
    name: '反方立论',
    stage_kind: 'FIXED_SPEECH',
    actions: [{ side: 'NEGATIVE', seat_no: 1, duration_seconds: 90 }],
  },
  {
    name: '自由辩论',
    stage_kind: 'FREE_DEBATE',
    duration_seconds: 360,
    parameters: { max_speech_seconds: 30, starting_side: 'AFFIRMATIVE' },
  },
  { name: '比赛结束', stage_kind: 'END' },
];

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
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}
function SelectField({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <select
        className="admin-field"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        {children}
      </select>
    </label>
  );
}
