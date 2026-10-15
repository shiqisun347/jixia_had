'use client';

import { Bot, Pencil, RotateCcw } from 'lucide-react';
import Image from 'next/image';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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
  const [pendingAgentId, setPendingAgentId] = useState('');
  const loadVersion = useRef(0);
  const currentRuleId = useRef('');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<Catalog>('/api/admin/catalog'),
      requestJson<RuleRow[]>('/api/admin/rules'),
    ])
      .then(([nextCatalog, nextRules]) => {
        if (!active) return;
        const usable = nextRules.filter(
          (rule) =>
            rule.side_size === 4 && !rule.historical_read_only && rule.status !== 'ARCHIVED',
        );
        setCatalog(nextCatalog);
        setRules(usable);
        const remembered = window.localStorage.getItem(RULE_STORAGE_KEY) ?? '';
        if (!usable.some((rule) => rule.id === remembered)) {
          setRuleId('');
          return;
        }
        setRuleId(remembered);
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
    const version = ++loadVersion.current;
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
      if (version !== loadVersion.current) return;
      setAgents(nextAgents);
      setWorkspace(nextWorkspace);
      setError('');
    } catch (requestError: unknown) {
      if (version === loadVersion.current) setError(readableAdminError(requestError));
    } finally {
      if (version === loadVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    currentRuleId.current = ruleId;
    let active = true;
    queueMicrotask(() => {
      if (active) void reloadRule(ruleId);
    });
    return () => {
      active = false;
      loadVersion.current += 1;
    };
  }, [ruleId, reloadRule]);

  function selectRule(selectedRuleId: string) {
    currentRuleId.current = selectedRuleId;
    loadVersion.current += 1;
    setSelectedAgent(null);
    setAgents([]);
    setWorkspace(null);
    setRuleId(selectedRuleId);
    if (selectedRuleId) window.localStorage.setItem(RULE_STORAGE_KEY, selectedRuleId);
    else window.localStorage.removeItem(RULE_STORAGE_KEY);
  }

  async function toggleAgent(agent: AgentRow) {
    if (pendingAgentId) return;
    const selectedRuleId = ruleId;
    setPendingAgentId(agent.id);
    try {
      const updated = await requestJson<AgentRow>(
        `/api/admin/rules/${selectedRuleId}/agents/${agent.id}/status`,
        {
          method: 'PATCH',
          body: JSON.stringify({ status: agent.status === 'ENABLED' ? 'DISABLED' : 'ENABLED' }),
        },
      );
      if (currentRuleId.current === selectedRuleId) {
        setAgents((items) =>
          items.map((item) =>
            item.id === agent.id
              ? { ...item, ...updated, prompt_override_count: item.prompt_override_count }
              : item,
          ),
        );
      }
      toast?.showToast({
        message: `${agent.name} 已在本赛制${updated.status === 'ENABLED' ? '启用' : '停用'}`,
        tone: 'success',
      });
    } catch (requestError: unknown) {
      setError(readableAdminError(requestError));
      toast?.showToast({ message: readableAdminError(requestError), tone: 'error' });
    } finally {
      setPendingAgentId('');
    }
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
        description="启用的 Agent 音色自动同步到各赛制；每套赛制独立决定是否使用对应 Agent。"
        eyebrow="RULE AGENT POOLS"
        title="Agent 管理"
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <AdminPanel
        action={selectedRule ? <StatusBadge status={selectedRule.status} /> : null}
        description="名称、头像和语速跟随全局音色。本赛制的启停选择会保留，不受音色编辑或重新启用影响。"
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
              {rule.name} ·{' '}
              {rule.status === 'ENABLED'
                ? '已启用'
                : rule.status === 'READY'
                  ? '已就绪'
                  : rule.status === 'DISABLED'
                    ? '已停用'
                    : '配置中'}{' '}
              · {rule.id.slice(0, 8)}
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
          {selectedRule && !selectedRule.default_agent_model_profile_id ? (
            <div className="mb-4">
              <AdminFeedback
                message="本赛制尚未配置默认 Agent 模型，选择默认模型后将自动同步启用音色。"
                tone="info"
              />
              <a
                className="text-sm font-bold text-blue-700 underline"
                href={`/admin/rules/${ruleId}`}
              >
                配置赛制默认模型
              </a>
            </div>
          ) : null}
          {loading ? (
            <p className="py-10 text-center text-sm text-slate-500">正在加载 Agent 池…</p>
          ) : agents.length ? (
            <AgentTable
              agents={agents}
              catalog={catalog}
              onEdit={setSelectedAgent}
              onToggle={(agent) => void toggleAgent(agent)}
              pendingAgentId={pendingAgentId}
            />
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
  onToggle,
  pendingAgentId,
}: {
  agents: AgentRow[];
  catalog: Catalog;
  onEdit: (agent: AgentRow) => void;
  onToggle: (agent: AgentRow) => void;
  pendingAgentId: string;
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
            <th className="px-4 py-3">本赛制启用</th>
            <th className="px-4 py-3">当前可用状态</th>
            <th className="px-4 py-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {agents.map((agent) => {
            const voice = voices.get(agent.voice_profile_id);
            const model = catalog.models.find((item) => item.id === agent.model_profile_id);
            const reason =
              agent.status !== 'ENABLED'
                ? '本赛制已停用'
                : (agent.voice_status ?? voice?.status) !== 'ENABLED'
                  ? '音色已停用'
                  : (agent.model_status ?? model?.status) !== 'ENABLED'
                    ? '模型已停用'
                    : null;
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
                  <label className="flex cursor-pointer items-center gap-2 whitespace-nowrap">
                    <input
                      type="checkbox"
                      role="switch"
                      aria-label={`${agent.name} 本赛制启用`}
                      checked={agent.status === 'ENABLED'}
                      disabled={Boolean(pendingAgentId)}
                      onChange={() => onToggle(agent)}
                      className="size-4 accent-blue-700"
                    />
                    {pendingAgentId === agent.id
                      ? '保存中…'
                      : agent.status === 'ENABLED'
                        ? '启用'
                        : '停用'}
                  </label>
                </td>
                <td className="px-4 py-3">
                  {reason ? (
                    <span className="text-xs text-slate-600">{reason}</span>
                  ) : (
                    <StatusBadge status="ENABLED" />
                  )}
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
          label="本赛制启用状态"
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
