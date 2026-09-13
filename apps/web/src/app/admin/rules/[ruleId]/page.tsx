'use client';

import {
  ArrowLeft,
  ChevronDown,
  FileText,
  Gavel,
  ListChecks,
  Settings2,
  ShieldCheck,
} from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useOptionalToast } from '@/components/ui/toast-provider';
import { readableAdminError } from '@/features/admin/admin-api';
import { AdminButton, AdminDrawer, StatusBadge } from '@/features/admin/admin-controls';
import { AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type { Catalog, RuleRow } from '@/features/admin/admin-types';
import { requestJson } from '@/lib/auth-api';

type Prompt = { purpose: 'SPEECH' | 'DECISION'; template_text: string; variables: string[] };
type Stage = {
  id: string;
  position: number;
  name: string;
  stage_kind: string;
  duration_seconds: number;
  start_host_text: string;
  end_host_text: string;
  parameters: Record<string, unknown>;
  actions: Array<{
    id: string;
    side: string | null;
    seat_no: number | null;
    duration_seconds: number;
    action_kind?: string;
    parameters?: Record<string, unknown>;
  }>;
  prompts: Prompt[];
};
type Workspace = {
  rule: RuleRow;
  agent_count: number;
  stages: Stage[];
  judge: {
    enabled: boolean;
    model_profile_id: string | null;
    judge_prompt: string;
    include_in_leaderboard: boolean;
  } | null;
};
type Tab = 'basic' | 'stages' | 'prompts' | 'judge' | 'validation';

const tabs: Array<{ id: Tab; label: string; icon: typeof Settings2 }> = [
  { id: 'basic', label: '基本信息', icon: Settings2 },
  { id: 'stages', label: '阶段流程', icon: ListChecks },
  { id: 'prompts', label: 'Agent Prompt', icon: FileText },
  { id: 'judge', label: 'AI 裁判', icon: Gavel },
  { id: 'validation', label: '校验与启停', icon: ShieldCheck },
];

export default function RuleWorkspacePage() {
  const { ruleId } = useParams<{ ruleId: string }>();
  const toast = useOptionalToast();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [tab, setTab] = useState<Tab>('stages');
  const [selectedStage, setSelectedStage] = useState<Stage | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [nextWorkspace, nextCatalog] = await Promise.all([
        requestJson<Workspace>(`/api/admin/rules/${ruleId}/workspace`),
        requestJson<Catalog>('/api/admin/catalog'),
      ]);
      setWorkspace(nextWorkspace);
      setCatalog(nextCatalog);
      setError('');
    } catch (requestError: unknown) {
      setError(readableAdminError(requestError));
    } finally {
      setLoading(false);
    }
  }, [ruleId]);

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<Workspace>(`/api/admin/rules/${ruleId}/workspace`),
      requestJson<Catalog>('/api/admin/catalog'),
    ])
      .then(([nextWorkspace, nextCatalog]) => {
        if (!active) return;
        setWorkspace(nextWorkspace);
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
  }, [ruleId]);

  if (loading && !workspace)
    return <p className="py-16 text-center text-sm text-slate-500">正在加载规则工作区…</p>;
  if (!workspace) return <AdminFeedback message={error || '规则不存在'} tone="error" />;
  const rule = workspace.rule;
  return (
    <div className="space-y-5">
      <Link
        className="inline-flex items-center gap-1 text-sm font-bold text-slate-600 hover:text-slate-950"
        href="/admin/rules"
      >
        <ArrowLeft className="size-4" />
        返回规则目录
      </Link>
      <AdminPageHeader
        actions={<StatusBadge status={rule.status} />}
        description={`${rule.side_size}v${rule.side_size} · 配置 r${rule.config_revision} · ${workspace.agent_count} 个 Agent`}
        eyebrow="RULE WORKSPACE"
        title={rule.name}
      />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      <nav aria-label="规则工作区" className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {tabs.map((item) => {
          const Icon = item.icon;
          return (
            <button
              className={`inline-flex min-h-11 items-center gap-2 border-b-2 px-3 text-sm font-bold ${tab === item.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-900'}`}
              key={item.id}
              onClick={() => setTab(item.id)}
              type="button"
            >
              <Icon className="size-4" />
              {item.label}
            </button>
          );
        })}
      </nav>

      {tab === 'basic' ? (
        <BasicTab
          key={rule.config_revision}
          rule={rule}
          catalog={catalog}
          onSaved={load}
          ruleId={ruleId}
          showMessage={(message, tone) => toast?.showToast({ message, tone })}
        />
      ) : null}
      {tab === 'stages' ? (
        <StageTimeline stages={workspace.stages} onSelect={setSelectedStage} />
      ) : null}
      {tab === 'prompts' ? (
        <PromptOverview stages={workspace.stages} onSelect={setSelectedStage} />
      ) : null}
      {tab === 'judge' ? (
        <JudgeTab
          catalog={catalog}
          judge={workspace.judge}
          onSaved={load}
          ruleId={ruleId}
          showMessage={(message, tone) => toast?.showToast({ message, tone })}
        />
      ) : null}
      {tab === 'validation' ? (
        <ValidationTab
          onSaved={load}
          rule={rule}
          ruleId={ruleId}
          showMessage={(message, tone) => toast?.showToast({ message, tone })}
          stages={workspace.stages}
          agentCount={workspace.agent_count}
        />
      ) : null}

      {selectedStage ? (
        <StageDrawer
          key={selectedStage.id}
          onClose={() => setSelectedStage(null)}
          onSaved={load}
          ruleId={ruleId}
          showMessage={(message, tone) => toast?.showToast({ message, tone })}
          stage={selectedStage}
        />
      ) : null}
    </div>
  );
}

function BasicTab({
  rule,
  catalog,
  ruleId,
  onSaved,
  showMessage,
}: {
  rule: RuleRow;
  catalog: Catalog | null;
  ruleId: string;
  onSaved: () => Promise<void>;
  showMessage: (message: string, tone: 'success' | 'error') => void;
}) {
  const [name, setName] = useState(rule.name);
  const [description, setDescription] = useState(rule.description);
  const [hostVoiceId, setHostVoiceId] = useState(rule.host_voice_profile_id ?? '');
  const [modelId, setModelId] = useState(rule.default_agent_model_profile_id ?? '');
  const [topicPolicy, setTopicPolicy] = useState(rule.topic_policy ?? 'BOTH');
  const [postmatchQuestionnaire, setPostmatchQuestionnaire] = useState(
    rule.postmatch_questionnaire_enabled ?? false,
  );
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    try {
      await requestJson(`/api/admin/rules/${ruleId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          description,
          host_voice_profile_id: hostVoiceId,
          default_agent_model_profile_id: modelId,
          topic_policy: topicPolicy,
          postmatch_questionnaire_enabled: postmatchQuestionnaire,
        }),
      });
      showMessage('规则基本信息已保存', 'success');
      await onSaved();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    } finally {
      setSaving(false);
    }
  }
  return (
    <AdminPanel
      description={`基础资源属于规则当前配置 · ${rule.side_size}v${rule.side_size} · 约 ${Math.ceil(rule.estimated_seconds / 60)} 分钟`}
      title="基本信息"
    >
      <div className="max-w-3xl space-y-4">
        <Field label="规则名称" value={name} onChange={setName} />
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          规则说明
          <textarea
            className="admin-field min-h-24"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <Select label="主持音色" value={hostVoiceId} onChange={setHostVoiceId}>
          {catalog?.voices
            .filter((voice) => voice.kind === 'HOST' && voice.status === 'ENABLED')
            .map((voice) => (
              <option key={voice.id} value={voice.id}>
                {voice.name}
              </option>
            ))}
        </Select>
        <Select label="默认 Agent 模型" value={modelId} onChange={setModelId}>
          {catalog?.models
            .filter((model) => model.status === 'ENABLED')
            .map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
        </Select>
        <Select label="辩题策略" value={topicPolicy} onChange={setTopicPolicy}>
          <option value="BOTH">预设与自定义均允许</option>
          <option value="PRESET_ONLY">仅预设辩题</option>
          <option value="CUSTOM_ONLY">仅自定义辩题</option>
        </Select>
        <label className="flex items-center gap-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4 text-sm font-bold text-slate-800">
          <input
            checked={postmatchQuestionnaire}
            className="size-4 accent-blue-600"
            onChange={(event) => setPostmatchQuestionnaire(event.target.checked)}
            type="checkbox"
          />
          启用赛后 AI / 自身发言问卷
        </label>
        <AdminButton loading={saving} onClick={() => void save()} tone="primary">
          保存基本信息
        </AdminButton>
      </div>
    </AdminPanel>
  );
}

function StageTimeline({
  stages,
  onSelect,
}: {
  stages: Stage[];
  onSelect: (stage: Stage) => void;
}) {
  return (
    <AdminPanel
      description="阶段严格按顺序执行。点击阶段在右侧抽屉查看设置并编辑 Prompt。"
      title="阶段流程"
    >
      <ol className="relative ml-4 border-l border-slate-300 pl-7">
        {stages.map((stage) => (
          <li className="relative pb-5 last:pb-0" key={stage.id}>
            <span className="absolute -left-[2.2rem] grid size-7 place-items-center rounded-full border border-slate-300 bg-white text-xs font-black text-slate-700">
              {stage.position}
            </span>
            <button
              className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 rounded-lg border border-slate-200 bg-white p-4 text-left hover:border-blue-300 hover:bg-blue-50/40"
              onClick={() => onSelect(stage)}
              type="button"
            >
              <span>
                <b className="block text-slate-950">{stage.name}</b>
                <span className="mt-1 block text-xs text-slate-500">{stageSummary(stage)}</span>
              </span>
              <span className="flex items-center gap-2">
                <span className="text-xs font-bold text-slate-500">
                  {stage.prompts.length} 个 Prompt
                </span>
                <ChevronDown className="size-4 -rotate-90" />
              </span>
            </button>
          </li>
        ))}
      </ol>
    </AdminPanel>
  );
}

function PromptOverview({
  stages,
  onSelect,
}: {
  stages: Stage[];
  onSelect: (stage: Stage) => void;
}) {
  const promptStages = stages.filter((stage) => stage.prompts.length);
  return (
    <AdminPanel description="通用模板动态应用到未个性化的规则 Agent。" title="Agent Prompt 模板">
      <div className="divide-y divide-slate-200">
        {promptStages.map((stage) => (
          <button
            className="flex w-full items-center justify-between gap-4 py-4 text-left"
            key={stage.id}
            onClick={() => onSelect(stage)}
            type="button"
          >
            <span>
              <b>{stage.name}</b>
              <span className="mt-1 block text-xs text-slate-500">
                {stage.prompts
                  .map((prompt) => (prompt.purpose === 'DECISION' ? '发言决策' : '正式发言'))
                  .join('、')}
              </span>
            </span>
            <span className="text-sm font-bold text-blue-700">编辑</span>
          </button>
        ))}
      </div>
    </AdminPanel>
  );
}

function StageDrawer({
  stage,
  ruleId,
  onClose,
  onSaved,
  showMessage,
}: {
  stage: Stage;
  ruleId: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
  showMessage: (message: string, tone: 'success' | 'error') => void;
}) {
  const [purpose, setPurpose] = useState<'SPEECH' | 'DECISION'>(
    stage.prompts[0]?.purpose ?? 'SPEECH',
  );
  const [text, setText] = useState(stage.prompts[0]?.template_text ?? '');
  const [name, setName] = useState(stage.name);
  const [duration, setDuration] = useState(stage.duration_seconds);
  const [startHostText, setStartHostText] = useState(stage.start_host_text);
  const [endHostText, setEndHostText] = useState(stage.end_host_text);
  const [actions, setActions] = useState(stage.actions);
  const [maxSpeechSeconds, setMaxSpeechSeconds] = useState(
    Number(stage.parameters.max_speech_seconds ?? 30),
  );
  const [startingSide, setStartingSide] = useState(
    String(stage.parameters.starting_side ?? 'AFFIRMATIVE'),
  );
  const [saving, setSaving] = useState(false);
  const prompt = stage?.prompts.find((item) => item.purpose === purpose) ?? stage?.prompts[0];
  async function save() {
    if (!stage || !prompt) return;
    setSaving(true);
    try {
      await requestJson(`/api/admin/rules/${ruleId}/stages/${stage.id}/prompts/${purpose}`, {
        method: 'PUT',
        body: JSON.stringify({ template_text: text }),
      });
      showMessage('阶段 Prompt 已保存', 'success');
      await onSaved();
      onClose();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    } finally {
      setSaving(false);
    }
  }
  async function saveStage() {
    setSaving(true);
    try {
      await requestJson(`/api/admin/rules/${ruleId}/stages/${stage.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          duration_seconds:
            stage.stage_kind === 'FREE_DEBATE' || stage.stage_kind === 'PREPARATION' ? duration : 0,
          start_host_text: startHostText,
          end_host_text: endHostText,
          parameters:
            stage.stage_kind === 'FREE_DEBATE'
              ? {
                  ...stage.parameters,
                  max_speech_seconds: maxSpeechSeconds,
                  starting_side: startingSide,
                }
              : stage.parameters,
          actions:
            stage.stage_kind === 'FIXED_SPEECH'
              ? actions.map((action) => ({
                  action_kind: action.action_kind ?? 'SPEECH',
                  side: action.side,
                  seat_no: action.seat_no,
                  duration_seconds: action.duration_seconds,
                  parameters: action.parameters ?? {},
                }))
              : [],
        }),
      });
      showMessage('阶段设置已保存', 'success');
      await onSaved();
      onClose();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    } finally {
      setSaving(false);
    }
  }
  return (
    <AdminDrawer
      description="只显示当前阶段适用的设置和 Prompt。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton onClick={onClose}>取消</AdminButton>
          <AdminButton loading={saving} onClick={() => void saveStage()}>
            保存阶段
          </AdminButton>
          {prompt ? (
            <AdminButton loading={saving} onClick={() => void save()} tone="primary">
              保存 Prompt
            </AdminButton>
          ) : null}
        </div>
      }
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open
      title={stage.name}
    >
      <div className="space-y-5">
        <Item label="类型" value={stage.stage_kind} />
        <Field label="阶段名称" value={name} onChange={setName} />
        {stage.stage_kind === 'FREE_DEBATE' || stage.stage_kind === 'PREPARATION' ? (
          <NumberField
            label={stage.stage_kind === 'FREE_DEBATE' ? '每方累计时长（秒）' : '阶段时长（秒）'}
            value={duration}
            onChange={setDuration}
          />
        ) : null}
        {stage.stage_kind === 'FREE_DEBATE' ? (
          <>
            <NumberField
              label="单次发言上限（秒）"
              value={maxSpeechSeconds}
              onChange={setMaxSpeechSeconds}
            />
            <Select label="起始方" value={startingSide} onChange={setStartingSide}>
              <option value="AFFIRMATIVE">正方</option>
              <option value="NEGATIVE">反方</option>
            </Select>
          </>
        ) : null}
        {stage.stage_kind === 'FIXED_SPEECH' ? (
          <div className="space-y-3">
            <p className="text-xs font-bold text-slate-600">发言动作</p>
            {actions.map((action, index) => (
              <div className="grid grid-cols-3 gap-2" key={action.id}>
                <Select
                  label="阵营"
                  value={action.side ?? ''}
                  onChange={(value) =>
                    setActions((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index ? { ...item, side: value } : item,
                      ),
                    )
                  }
                >
                  <option value="AFFIRMATIVE">正方</option>
                  <option value="NEGATIVE">反方</option>
                </Select>
                <NumberField
                  label="席位"
                  value={action.seat_no ?? 1}
                  onChange={(value) =>
                    setActions((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index ? { ...item, seat_no: value } : item,
                      ),
                    )
                  }
                />
                <NumberField
                  label="时长（秒）"
                  value={action.duration_seconds}
                  onChange={(value) =>
                    setActions((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index ? { ...item, duration_seconds: value } : item,
                      ),
                    )
                  }
                />
              </div>
            ))}
          </div>
        ) : null}
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          开场主持词
          <textarea
            className="admin-field min-h-20"
            value={startHostText}
            onChange={(event) => setStartHostText(event.target.value)}
          />
        </label>
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          结束主持词
          <textarea
            className="admin-field min-h-20"
            value={endHostText}
            onChange={(event) => setEndHostText(event.target.value)}
          />
        </label>
        {prompt ? (
          <>
            <label className="grid gap-1.5 text-xs font-bold text-slate-600">
              Prompt 槽位
              <select
                className="admin-field"
                value={purpose}
                onChange={(event) => {
                  const nextPurpose = event.target.value as typeof purpose;
                  setPurpose(nextPurpose);
                  setText(
                    stage.prompts.find((item) => item.purpose === nextPurpose)?.template_text ?? '',
                  );
                }}
              >
                {stage.prompts.map((item) => (
                  <option key={item.purpose} value={item.purpose}>
                    {item.purpose === 'DECISION' ? '发言决策' : '正式发言'}
                  </option>
                ))}
              </select>
            </label>
            <textarea
              className="admin-field min-h-[22rem] font-mono text-xs leading-5"
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
            <p className="text-xs text-slate-500">变量：{prompt.variables.join('、')}</p>
          </>
        ) : (
          <AdminFeedback message="该阶段没有 Agent Prompt。" tone="info" />
        )}
      </div>
    </AdminDrawer>
  );
}

function JudgeTab({
  judge,
  catalog,
  ruleId,
  onSaved,
  showMessage,
}: {
  judge: Workspace['judge'];
  catalog: Catalog | null;
  ruleId: string;
  onSaved: () => Promise<void>;
  showMessage: (message: string, tone: 'success' | 'error') => void;
}) {
  const [enabled, setEnabled] = useState(judge?.enabled ?? false);
  const [modelId, setModelId] = useState(judge?.model_profile_id ?? '');
  const [prompt, setPrompt] = useState(judge?.judge_prompt ?? '');
  const [leaderboard, setLeaderboard] = useState(judge?.include_in_leaderboard ?? false);
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    try {
      await requestJson(`/api/admin/rules/${ruleId}/judge`, {
        method: 'PUT',
        body: JSON.stringify({
          enabled,
          model_profile_id: modelId || null,
          judge_prompt: prompt,
          include_in_leaderboard: enabled && leaderboard,
        }),
      });
      showMessage('AI 裁判配置已保存', 'success');
      await onSaved();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    } finally {
      setSaving(false);
    }
  }
  return (
    <AdminPanel description="裁判配置只属于当前规则，并随房间冻结。" title="AI 裁判">
      <div className="max-w-3xl space-y-4">
        <Check
          label="启用 AI 裁判"
          checked={enabled}
          onChange={(value) => {
            setEnabled(value);
            if (!value) setLeaderboard(false);
          }}
        />
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          全局模型
          <select
            className="admin-field"
            disabled={!enabled}
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
          >
            <option value="">请选择模型</option>
            {catalog?.models
              .filter((model) => model.status === 'ENABLED')
              .map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
          </select>
        </label>
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          裁判 Prompt
          <textarea
            className="admin-field min-h-72 font-mono text-xs leading-5"
            disabled={!enabled}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>
        <Check
          label="结果进入排行榜"
          checked={leaderboard}
          disabled={!enabled}
          onChange={setLeaderboard}
        />
        <AdminButton loading={saving} onClick={() => void save()} tone="primary">
          保存裁判配置
        </AdminButton>
      </div>
    </AdminPanel>
  );
}

function ValidationTab({
  rule,
  ruleId,
  stages,
  agentCount,
  onSaved,
  showMessage,
}: {
  rule: RuleRow;
  ruleId: string;
  stages: Stage[];
  agentCount: number;
  onSaved: () => Promise<void>;
  showMessage: (message: string, tone: 'success' | 'error') => void;
}) {
  const checks = [
    { label: '正式 4v4', ok: rule.side_size === 4 && !rule.historical_read_only },
    { label: 'Agent 候选不少于 8 个', ok: agentCount >= 8 },
    {
      label: '每个发言阶段已配置 Prompt',
      ok: stages.every((stage) =>
        stage.stage_kind === 'FIXED_SPEECH'
          ? stage.prompts.some((item) => item.purpose === 'SPEECH')
          : stage.stage_kind === 'FREE_DEBATE'
            ? stage.prompts.length === 2
            : true,
      ),
    },
  ];
  async function action(name: 'enable' | 'disable' | 'review-audio') {
    try {
      await requestJson(`/api/admin/rules/${ruleId}/${name}`, { method: 'POST', body: '{}' });
      showMessage(
        name === 'enable' ? '规则已启用' : name === 'disable' ? '规则已停用' : '主持音频已审核',
        'success',
      );
      await onSaved();
    } catch (requestError: unknown) {
      showMessage(readableAdminError(requestError), 'error');
    }
  }
  return (
    <AdminPanel description="全部校验通过后才能用于新建房间。" title="校验与启停">
      <div className="space-y-3">
        {checks.map((check) => (
          <div
            className="flex items-center justify-between border-b border-slate-100 py-3"
            key={check.label}
          >
            <span className="text-sm font-bold">{check.label}</span>
            <span
              className={
                check.ok ? 'text-sm font-bold text-green-700' : 'text-sm font-bold text-red-700'
              }
            >
              {check.ok ? '通过' : '未通过'}
            </span>
          </div>
        ))}
        <div className="flex flex-wrap gap-2 pt-3">
          {rule.status === 'GENERATING_AUDIO' ? (
            <AdminButton onClick={() => void action('review-audio')}>审核主持音频</AdminButton>
          ) : null}
          {rule.status === 'ENABLED' ? (
            <AdminButton onClick={() => void action('disable')}>停用规则</AdminButton>
          ) : (
            <AdminButton
              disabled={!checks.every((item) => item.ok)}
              onClick={() => void action('enable')}
              tone="primary"
            >
              启用规则
            </AdminButton>
          )}
        </div>
      </div>
    </AdminPanel>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-bold text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm font-bold text-slate-900">{value}</dd>
    </div>
  );
}
function Check({
  label,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm font-bold">
      <input
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      {label}
    </label>
  );
}
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
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <input
        className="admin-field"
        min={0}
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
function Select({
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
function stageSummary(stage: Stage) {
  if (stage.stage_kind === 'FIXED_SPEECH')
    return stage.actions
      .map(
        (action) =>
          `${action.side === 'AFFIRMATIVE' ? '正方' : '反方'}${action.seat_no ?? ''}辩 · ${action.duration_seconds} 秒`,
      )
      .join('；');
  if (stage.stage_kind === 'FREE_DEBATE')
    return `每方 ${stage.duration_seconds} 秒 · 单次 ${String(stage.parameters.max_speech_seconds ?? 30)} 秒`;
  return stage.duration_seconds ? `${stage.duration_seconds} 秒` : '结束';
}
