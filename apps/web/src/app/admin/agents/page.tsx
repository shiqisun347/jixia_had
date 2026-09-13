'use client';

import { Bot, Pencil, RotateCcw } from 'lucide-react';
import Image from 'next/image';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useOptionalToast } from '@/components/ui/toast-provider';
import { readableAdminError } from '@/features/admin/admin-api';
import {
  AdminButton,
  AdminDrawer,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type { AgentRow, Catalog, RuleRow } from '@/features/admin/admin-types';
import { avatarAssetUrl } from '@/lib/avatar-catalog';
import { requestJson } from '@/lib/auth-api';

type PromptSlot = { stageId: string; purpose: 'SPEECH' | 'DECISION'; label: string };
type Workspace = {
  stages: Array<{
    id: string;
    name: string;
    prompts: Array<{ purpose: 'SPEECH' | 'DECISION' }>;
  }>;
};
type PromptState = {
  uses_default: boolean;
  template_text: string;
  default_template_text: string;
};

const RULE_STORAGE_KEY = 'jx-admin-agent-rule-v1';

export default function AdminAgentsPage() {
  const toast = useOptionalToast();
  const [catalog, setCatalog] = useState<Catalog>({
    agents: [],
    models: [],
    voices: [],
    topics: [],
    rules: [],
  });
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [ruleId, setRuleId] = useState('');
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<AgentRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<Catalog>('/api/admin/catalog'),
      requestJson<RuleRow[]>('/api/admin/rules'),
    ])
      .then(async ([nextCatalog, nextRules]) => {
        if (!active) return;
        const usable = nextRules.filter(
          (rule) => rule.side_size === 4 && !rule.historical_read_only,
        );
        setCatalog(nextCatalog);
        setRules(usable);
        const remembered = window.localStorage.getItem(RULE_STORAGE_KEY) ?? '';
        if (!usable.some((rule) => rule.id === remembered)) {
          setRuleId('');
          return;
        }
        setRuleId(remembered);
        const [nextAgents, nextWorkspace] = await Promise.all([
          requestJson<AgentRow[]>(`/api/admin/rules/${remembered}/agents`),
          requestJson<Workspace>(`/api/admin/rules/${remembered}/workspace`),
        ]);
        if (!active) return;
        setAgents(nextAgents);
        setWorkspace(nextWorkspace);
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

  const reloadRule = useCallback(async (selectedRuleId: string) => {
    if (!selectedRuleId) {
      setAgents([]);
      setWorkspace(null);
      return;
    }
    setLoading(true);
    try {
      const [nextAgents, nextWorkspace] = await Promise.all([
        requestJson<AgentRow[]>(`/api/admin/rules/${selectedRuleId}/agents`),
        requestJson<Workspace>(`/api/admin/rules/${selectedRuleId}/workspace`),
      ]);
      setAgents(nextAgents);
      setWorkspace(nextWorkspace);
      setError('');
    } catch (requestError: unknown) {
      setError(readableAdminError(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  function selectRule(selectedRuleId: string) {
    setRuleId(selectedRuleId);
    if (selectedRuleId) window.localStorage.setItem(RULE_STORAGE_KEY, selectedRuleId);
    void reloadRule(selectedRuleId);
  }

  const selectedRule = rules.find((rule) => rule.id === ruleId);
  const slots = useMemo<PromptSlot[]>(
    () =>
      workspace?.stages.flatMap((stage) =>
        stage.prompts.map((prompt) => ({
          stageId: stage.id,
          purpose: prompt.purpose,
          label: `${stage.name} · ${prompt.purpose === 'DECISION' ? '发言决策' : '正式发言'}`,
        })),
      ) ?? [],
    [workspace],
  );

  return (
    <div className="space-y-6">
      <AdminPageHeader
        description="每套赛制拥有独立 Agent 池。选择赛制后，才能查看和编辑其中的 Agent。"
        eyebrow="RULE AGENT POOLS"
        title="Agent 管理"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel
        action={selectedRule ? <StatusBadge status={selectedRule.status} /> : null}
        description="Agent 由启用的全局音色自动生成，不支持手工新增、删除或跨赛制复制。"
        title="选择赛制规则"
      >
        <AdminSelect
          className="min-w-72"
          label="赛制规则"
          onChange={(event) => selectRule(event.target.value)}
          value={ruleId}
        >
          <option value="">请选择赛制规则</option>
          {rules.map((rule) => (
            <option key={rule.id} value={rule.id}>
              {rule.name}
            </option>
          ))}
        </AdminSelect>
      </AdminPanel>

      {!ruleId ? (
        <AdminFeedback message="先选择一套赛制规则，再管理它的 Agent 池。" tone="info" />
      ) : (
        <AdminPanel
          description={`${agents.length} 个 Agent · 名称、头像、音色和语速跟随全局音色`}
          title={selectedRule?.name ?? 'Agent 池'}
        >
          {loading ? (
            <p className="py-10 text-center text-sm text-slate-500">正在加载 Agent 池…</p>
          ) : agents.length ? (
            <AgentTable agents={agents} catalog={catalog} onEdit={setSelectedAgent} />
          ) : (
            <AdminFeedback
              message="该规则还没有 Agent。请先启用 Agent 音色或检查默认模型。"
              tone="info"
            />
          )}
        </AdminPanel>
      )}

      {selectedAgent ? (
        <AgentEditor
          agent={selectedAgent}
          catalog={catalog}
          key={selectedAgent.id}
          onClose={() => setSelectedAgent(null)}
          onSaved={async () => {
            setSelectedAgent(null);
            await reloadRule(ruleId);
          }}
          ruleId={ruleId}
          slots={slots}
          showMessage={(message, tone) => toast?.showToast({ message, tone })}
        />
      ) : null}
    </div>
  );
}

function AgentTable({
  agents,
  catalog,
  onEdit,
}: {
  agents: AgentRow[];
  catalog: Catalog;
  onEdit: (agent: AgentRow) => void;
}) {
  const voices = new Map(catalog.voices.map((voice) => [voice.id, voice]));
  const models = new Map(catalog.models.map((model) => [model.id, model.name]));
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs text-slate-600">
          <tr>
            <th className="px-4 py-3">Agent</th>
            <th className="px-4 py-3">音色</th>
            <th className="px-4 py-3">模型</th>
            <th className="px-4 py-3">Prompt</th>
            <th className="px-4 py-3">状态</th>
            <th className="px-4 py-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {agents.map((agent) => {
            const voice = voices.get(agent.voice_profile_id);
            return (
              <tr key={agent.id}>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <Image
                      alt=""
                      className="size-9 rounded-full object-cover"
                      height={36}
                      src={avatarAssetUrl(voice?.avatar_key ?? agent.avatar_key ?? 'agent-01')}
                      width={36}
                    />
                    <span className="font-bold text-slate-950">{agent.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3">{voice?.name ?? '未知音色'}</td>
                <td className="px-4 py-3">{models.get(agent.model_profile_id) ?? '未知模型'}</td>
                <td className="px-4 py-3">
                  {agent.prompt_override_count
                    ? `已个性化 ${agent.prompt_override_count} 项`
                    : '使用赛制默认'}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={agent.status} />
                </td>
                <td className="px-4 py-3 text-right">
                  <AdminButton onClick={() => onEdit(agent)} size="sm">
                    <Pencil className="size-3.5" />
                    编辑
                  </AdminButton>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AgentEditor({
  agent,
  catalog,
  slots,
  ruleId,
  onClose,
  onSaved,
  showMessage,
}: {
  agent: AgentRow;
  catalog: Catalog;
  slots: PromptSlot[];
  ruleId: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
  showMessage: (message: string, tone: 'success' | 'error') => void;
}) {
  const [modelId, setModelId] = useState(agent.model_profile_id);
  const [status, setStatus] = useState<'ENABLED' | 'DISABLED'>(
    agent.status === 'DISABLED' ? 'DISABLED' : 'ENABLED',
  );
  const [params, setParams] = useState(JSON.stringify(agent.generation_params ?? {}, null, 2));
  const [slotKey, setSlotKey] = useState(slots[0] ? `${slots[0].stageId}:${slots[0].purpose}` : '');
  const [prompt, setPrompt] = useState<PromptState | null>(null);
  const [usesDefault, setUsesDefault] = useState(true);
  const [promptText, setPromptText] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!slotKey) return;
    const [stageId, purpose] = slotKey.split(':');
    void requestJson<PromptState>(
      `/api/admin/rules/${ruleId}/agents/${agent.id}/stages/${stageId}/prompts/${purpose}`,
    ).then((next) => {
      setPrompt(next);
      setUsesDefault(next.uses_default);
      setPromptText(next.template_text);
    });
  }, [agent, ruleId, slotKey]);

  async function save() {
    if (!agent) return;
    setSaving(true);
    try {
      const generationParams = JSON.parse(params) as Record<string, unknown>;
      await requestJson(`/api/admin/rules/${ruleId}/agents/${agent.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          model_profile_id: modelId,
          generation_params: generationParams,
          status,
        }),
      });
      if (slotKey) {
        const [stageId, purpose] = slotKey.split(':');
        const path = `/api/admin/rules/${ruleId}/agents/${agent.id}/stages/${stageId}/prompts/${purpose}`;
        await requestJson(
          path,
          usesDefault
            ? { method: 'DELETE' }
            : { method: 'PUT', body: JSON.stringify({ template_text: promptText }) },
        );
      }
      showMessage('Agent 配置已保存', 'success');
      await onSaved();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    } finally {
      setSaving(false);
    }
  }

  const voice = catalog.voices.find((item) => item.id === agent?.voice_profile_id);
  const enabledModels = catalog.models.filter((model) => model.status === 'ENABLED');
  return (
    <AdminDrawer
      description="基础身份跟随全局音色；这里只编辑规则内运行配置。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton onClick={onClose}>取消</AdminButton>
          <AdminButton loading={saving} onClick={() => void save()} tone="primary">
            保存
          </AdminButton>
        </div>
      }
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open
      title={`编辑 Agent · ${agent.name}`}
    >
      <div className="space-y-5">
        <div className="flex items-center gap-3 border-b border-slate-200 pb-4">
          <Bot className="size-5 text-blue-700" />
          <div>
            <p className="font-bold">{agent.name}</p>
            <p className="text-xs text-slate-500">
              {voice?.name} · 语速 {voice?.rate ?? 1}
            </p>
          </div>
        </div>
        <EditorSelect label="模型" onChange={setModelId} value={modelId}>
          {enabledModels.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </EditorSelect>
        <EditorSelect
          label="状态"
          onChange={(value) => setStatus(value as typeof status)}
          value={status}
        >
          <option value="ENABLED">启用</option>
          <option value="DISABLED">停用</option>
        </EditorSelect>
        <details className="border-t border-slate-200 pt-4">
          <summary className="cursor-pointer text-sm font-bold">生成参数</summary>
          <textarea
            className="admin-field mt-3 min-h-32 font-mono text-xs"
            value={params}
            onChange={(event) => setParams(event.target.value)}
          />
        </details>
        {slots.length ? (
          <div className="space-y-3 border-t border-slate-200 pt-4">
            <EditorSelect
              label="Prompt 槽位"
              onChange={(value) => {
                setSlotKey(value);
                setPrompt(null);
              }}
              value={slotKey}
            >
              {slots.map((slot) => (
                <option
                  key={`${slot.stageId}:${slot.purpose}`}
                  value={`${slot.stageId}:${slot.purpose}`}
                >
                  {slot.label}
                </option>
              ))}
            </EditorSelect>
            <label className="flex items-center gap-2 text-sm font-bold">
              <input
                checked={usesDefault}
                onChange={(event) => {
                  setUsesDefault(event.target.checked);
                  setPromptText(
                    event.target.checked
                      ? (prompt?.default_template_text ?? '')
                      : (prompt?.template_text ?? ''),
                  );
                }}
                type="checkbox"
              />
              使用赛制默认 Prompt
            </label>
            {!usesDefault ? (
              <textarea
                className="admin-field min-h-72 font-mono text-xs leading-5"
                value={promptText}
                onChange={(event) => setPromptText(event.target.value)}
              />
            ) : (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap border-l-2 border-slate-200 pl-4 text-xs leading-5 text-slate-600">
                {prompt?.default_template_text}
              </pre>
            )}
            {!usesDefault ? (
              <AdminButton
                onClick={() => {
                  setUsesDefault(true);
                  setPromptText(prompt?.default_template_text ?? '');
                }}
                size="sm"
              >
                <RotateCcw className="size-3.5" />
                恢复默认
              </AdminButton>
            ) : null}
          </div>
        ) : null}
      </div>
    </AdminDrawer>
  );
}

function EditorSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <select
        className="admin-field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}
