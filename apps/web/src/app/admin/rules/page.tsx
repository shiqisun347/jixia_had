'use client';

import { useEffect, useState } from 'react';

import {
  AdminButton,
  AdminConfirmDialog,
  AdminFeedback,
  AdminPageHeader,
  StatusBadge,
} from '@/features/admin';
import { ApiClientError, requestJson } from '@/lib/auth-api';

type Voice = { id: string; name: string; kind: string };
type Rule = {
  id: string;
  rule_key: string;
  version: number;
  name: string;
  description: string;
  side_size: number;
  estimated_seconds: number;
  status: string;
  audio_reviewed_at: string | null;
};
type Catalog = { voices: Voice[]; rules: Rule[] };
type RuleDraftResponse = {
  rule_key: string;
  host_voice_profile_id: string;
  draft: {
    name: string;
    description: string;
    side_size: number;
    stages: Array<{
      name: string;
      stage_kind: Stage['stage_kind'];
      duration_seconds: number;
      start_host_text: string;
      end_host_text: string;
      parameters: Record<string, unknown>;
      actions: Array<FixedAction & { action_kind: string }>;
    }>;
  };
};
type FixedAction = { side: 'AFFIRMATIVE' | 'NEGATIVE'; seat_no: number; duration_seconds: number };
type Stage = {
  id: string;
  name: string;
  stage_kind: 'FIXED_SPEECH' | 'FREE_DEBATE' | 'PREPARATION';
  duration_seconds: number;
  start_host_text: string;
  end_host_text: string;
  max_speech_seconds: number;
  starting_side: 'AFFIRMATIVE' | 'NEGATIVE';
  actions: FixedAction[];
};

const fieldClass =
  'w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100';

const newStage = (kind: Stage['stage_kind'], id = crypto.randomUUID()): Stage => ({
  id,
  name: kind === 'FIXED_SPEECH' ? '固定发言' : kind === 'FREE_DEBATE' ? '自由辩论' : '准备时间',
  stage_kind: kind,
  duration_seconds: kind === 'FIXED_SPEECH' ? 0 : 60,
  start_host_text: '',
  end_host_text: '',
  max_speech_seconds: 60,
  starting_side: 'AFFIRMATIVE',
  actions:
    kind === 'FIXED_SPEECH' ? [{ side: 'AFFIRMATIVE', seat_no: 1, duration_seconds: 180 }] : [],
});

const fixedStage = (
  name: string,
  side: FixedAction['side'],
  seatNo: number,
  durationSeconds: number,
  startHostText: string,
  endHostText = '',
): Stage => ({
  id: crypto.randomUUID(),
  name,
  stage_kind: 'FIXED_SPEECH',
  duration_seconds: 0,
  start_host_text: startHostText,
  end_host_text: endHostText,
  max_speech_seconds: 60,
  starting_side: 'AFFIRMATIVE',
  actions: [{ side, seat_no: seatNo, duration_seconds: durationSeconds }],
});

export function formal4v4Stages(): Stage[] {
  return [
    fixedStage(
      '正方一辩立论',
      'AFFIRMATIVE',
      1,
      180,
      '现在进入立论环节。请正方一辩开始发言，时间三分钟。',
    ),
    fixedStage('反方一辩立论', 'NEGATIVE', 1, 180, '请反方一辩开始立论，时间三分钟。'),
    fixedStage(
      '正方二辩陈词',
      'AFFIRMATIVE',
      2,
      90,
      '现在进入二辩陈词环节。请正方二辩开始发言，时间一分三十秒。',
    ),
    fixedStage('反方二辩陈词', 'NEGATIVE', 2, 90, '请反方二辩开始陈词，时间一分三十秒。'),
    fixedStage(
      '正方三辩陈词',
      'AFFIRMATIVE',
      3,
      90,
      '现在进入三辩陈词环节。请正方三辩开始发言，时间一分三十秒。',
    ),
    fixedStage('反方三辩陈词', 'NEGATIVE', 3, 90, '请反方三辩开始陈词，时间一分三十秒。'),
    {
      id: crypto.randomUUID(),
      name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      duration_seconds: 180,
      start_host_text: '现在进入自由辩论环节。双方各有三分钟，正方先发言，单次发言不超过三十秒。',
      end_host_text: '',
      max_speech_seconds: 30,
      starting_side: 'AFFIRMATIVE',
      actions: [],
    },
    fixedStage(
      '反方四辩总结',
      'NEGATIVE',
      4,
      180,
      '现在进入总结陈词环节。请反方四辩开始总结，时间三分钟。',
    ),
    fixedStage(
      '正方四辩总结',
      'AFFIRMATIVE',
      4,
      180,
      '请正方四辩开始总结，时间三分钟。',
      '本场辩论到此结束，感谢各位辩手。',
    ),
  ];
}

function errorText(error: unknown) {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试';
}

export default function AdminRulesPage() {
  const [catalog, setCatalog] = useState<Catalog>({ voices: [], rules: [] });
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [ruleKey, setRuleKey] = useState('');
  const [sideSize, setSideSize] = useState(2);
  const [hostVoiceId, setHostVoiceId] = useState('');
  const [stages, setStages] = useState<Stage[]>([newStage('FIXED_SPEECH', 'stage-1')]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Rule | null>(null);

  async function load() {
    const result = await requestJson<Catalog>('/api/admin/catalog');
    setCatalog(result);
    setHostVoiceId(
      (current) => current || result.voices.find((voice) => voice.kind === 'HOST')?.id || '',
    );
  }

  useEffect(() => {
    let active = true;
    void requestJson<Catalog>('/api/admin/catalog')
      .then((result) => {
        if (!active) return;
        setCatalog(result);
        setHostVoiceId(result.voices.find((voice) => voice.kind === 'HOST')?.id || '');
      })
      .catch((error: unknown) => {
        if (active) setMessage(errorText(error));
      });
    return () => {
      active = false;
    };
  }, []);

  function updateStage(id: string, patch: Partial<Stage>) {
    setStages((current) =>
      current.map((stage) => (stage.id === id ? { ...stage, ...patch } : stage)),
    );
  }

  function moveStage(index: number, direction: -1 | 1) {
    setStages((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const copy = [...current];
      [copy[index], copy[target]] = [copy[target]!, copy[index]!];
      return copy;
    });
  }

  async function createRule() {
    if (!name.trim() || !hostVoiceId || !stages.length) {
      setMessage('请填写规则名称、主持音色并至少添加一个阶段');
      return;
    }
    setBusy(true);
    setMessage('正在创建规则并生成主持音频…');
    try {
      await requestJson('/api/admin/rules', {
        method: 'POST',
        body: JSON.stringify({
          rule_key: ruleKey || undefined,
          host_voice_profile_id: hostVoiceId,
          draft: {
            name: name.trim(),
            description: description.trim(),
            side_size: sideSize,
            stages: [
              ...stages.map((stage) => ({
                name: stage.name,
                stage_kind: stage.stage_kind,
                duration_seconds: stage.stage_kind === 'FIXED_SPEECH' ? 0 : stage.duration_seconds,
                start_host_text: stage.start_host_text,
                end_host_text: stage.end_host_text,
                parameters:
                  stage.stage_kind === 'FREE_DEBATE'
                    ? {
                        max_speech_seconds: stage.max_speech_seconds,
                        starting_side: stage.starting_side,
                      }
                    : {},
                actions:
                  stage.stage_kind === 'FIXED_SPEECH'
                    ? stage.actions.map((action) => ({
                        ...action,
                        action_kind: 'SPEECH',
                        parameters: {},
                      }))
                    : [],
              })),
              {
                name: '比赛结束',
                stage_kind: 'END',
                duration_seconds: 0,
                start_host_text: '',
                end_host_text: '',
                parameters: {},
                actions: [],
              },
            ],
          },
        }),
      });
      await load();
      setName('');
      setDescription('');
      setRuleKey('');
      setStages([newStage('FIXED_SPEECH', 'stage-1')]);
      setMessage('规则已创建。主持音频完成后试听审核，再启用规则。');
    } catch (error: unknown) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  async function copyRule(rule: Rule) {
    setBusy(true);
    try {
      const result = await requestJson<RuleDraftResponse>(`/api/admin/rules/${rule.id}/draft`);
      setRuleKey(result.rule_key);
      setName(`${result.draft.name} · 新版本`);
      setDescription(result.draft.description);
      setSideSize(result.draft.side_size);
      setHostVoiceId(result.host_voice_profile_id);
      setStages(
        result.draft.stages.map((stage) => ({
          id: crypto.randomUUID(),
          name: stage.name,
          stage_kind: stage.stage_kind,
          duration_seconds: stage.duration_seconds,
          start_host_text: stage.start_host_text,
          end_host_text: stage.end_host_text,
          max_speech_seconds: Number(stage.parameters.max_speech_seconds ?? 60),
          starting_side: stage.parameters.starting_side === 'NEGATIVE' ? 'NEGATIVE' : 'AFFIRMATIVE',
          actions: stage.actions.map((action) => ({
            side: action.side,
            seat_no: action.seat_no,
            duration_seconds: action.duration_seconds,
          })),
        })),
      );
      setMessage(`已载入 ${rule.name} v${rule.version}，保存后将生成同一规则的新版本。`);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (error: unknown) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  async function deleteRule(rule: Rule) {
    setBusy(true);
    try {
      await requestJson(`/api/admin/rules/${rule.id}`, { method: 'DELETE' });
      await load();
      setMessage(`规则“${rule.name} v${rule.version}”已删除。`);
    } catch (error: unknown) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
      setDeleteTarget(null);
    }
  }

  async function ruleAction(rule: Rule, action: 'review-audio' | 'enable' | 'disable') {
    setBusy(true);
    setMessage(
      action === 'review-audio'
        ? '正在确认主持音频…'
        : action === 'enable'
          ? '正在启用规则…'
          : '正在停用规则…',
    );
    try {
      await requestJson(`/api/admin/rules/${rule.id}/${action}`, { method: 'POST', body: '{}' });
      await load();
      setMessage(
        action === 'review-audio'
          ? '主持音频已审核'
          : action === 'enable'
            ? '规则已启用，可用于创建房间'
            : '规则已停用，已有比赛不受影响',
      );
    } catch (error: unknown) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <AdminPageHeader
        eyebrow="LINEAR RULE BUILDER"
        title="赛制规则"
        description="按顺序编排固定发言、自由辩论或准备阶段；结束阶段由系统自动补齐。"
        actions={
          <button
            className="rounded-xl bg-slate-950 px-4 py-3 text-sm font-black !text-white"
            onClick={() => {
              setName('4v4 正式辩论赛');
              setDescription('标准四对四赛制：立论、二三辩陈词、双方各三分钟自由辩论、四辩总结。');
              setSideSize(4);
              setStages(formal4v4Stages());
              setMessage('已载入 4v4 正式辩论赛模板，可继续修改后创建。');
            }}
          >
            新建 4v4 正式赛规则
          </button>
        }
      />
      {message ? <AdminFeedback message={message} /> : null}

      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-sm font-bold">
            规则名称
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              className={`mt-2 ${fieldClass}`}
              placeholder="例如：新国辩 2v2"
            />
          </label>
          <label className="text-sm font-bold">
            规则标识（版本共享）
            <input
              value={ruleKey}
              onChange={(event) => setRuleKey(event.target.value)}
              className={`mt-2 ${fieldClass}`}
              placeholder="可留空自动生成"
            />
          </label>
          <label className="text-sm font-bold">
            单方人数
            <select
              value={sideSize}
              onChange={(event) => setSideSize(Number(event.target.value))}
              className={`mt-2 ${fieldClass}`}
            >
              {[1, 2, 3, 4, 5].map((size) => (
                <option key={size} value={size}>
                  {size}V{size}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm font-bold">
            主持音色
            <select
              value={hostVoiceId}
              onChange={(event) => setHostVoiceId(event.target.value)}
              className={`mt-2 ${fieldClass}`}
            >
              <option value="">请选择</option>
              {catalog.voices
                .filter((voice) => voice.kind === 'HOST')
                .map((voice) => (
                  <option key={voice.id} value={voice.id}>
                    {voice.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="text-sm font-bold">
            说明
            <input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className={`mt-2 ${fieldClass}`}
              placeholder="可选"
            />
          </label>
        </div>

        <div className="mt-6 space-y-4">
          {stages.map((stage, index) => (
            <StageEditor
              key={stage.id}
              stage={stage}
              index={index}
              sideSize={sideSize}
              onChange={(patch) => updateStage(stage.id, patch)}
              onMove={(direction) => moveStage(index, direction)}
              onDelete={() =>
                setStages((current) => current.filter((item) => item.id !== stage.id))
              }
            />
          ))}
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            onClick={() => setStages((current) => [...current, newStage('FIXED_SPEECH')])}
            className="rounded-xl bg-slate-100 px-4 py-2.5 text-sm font-bold"
          >
            ＋ 固定发言
          </button>
          <button
            onClick={() => setStages((current) => [...current, newStage('FREE_DEBATE')])}
            className="rounded-xl bg-slate-100 px-4 py-2.5 text-sm font-bold"
          >
            ＋ 自由辩论
          </button>
          <button
            onClick={() => setStages((current) => [...current, newStage('PREPARATION')])}
            className="rounded-xl bg-slate-100 px-4 py-2.5 text-sm font-bold"
          >
            ＋ 准备阶段
          </button>
          <button
            disabled={busy}
            onClick={() => void createRule()}
            className="jx-disabled-command ml-auto rounded-xl border border-blue-600 bg-blue-600 px-5 py-2.5 text-sm font-bold text-white"
          >
            {busy ? '处理中…' : '创建规则并生成音频'}
          </button>
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-xl font-black">已有规则 · {catalog.rules.length}</h2>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {catalog.rules.map((rule) => (
            <article key={rule.id} className="rounded-2xl bg-slate-50 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-black">{rule.name}</h3>
                  <p className="mt-1 text-xs text-slate-500">
                    {rule.side_size}V{rule.side_size} · 约 {Math.ceil(rule.estimated_seconds / 60)}{' '}
                    分钟
                  </p>
                </div>
                <StatusBadge status={rule.status} />
              </div>
              <div className="mt-4 flex gap-2">
                <AdminButton disabled={busy} onClick={() => void copyRule(rule)} size="sm">
                  编辑为新版本
                </AdminButton>
                <AdminButton
                  disabled={busy}
                  onClick={() => setDeleteTarget(rule)}
                  size="sm"
                  tone="danger"
                >
                  删除
                </AdminButton>
                {(rule.status === 'READY' || rule.status === 'GENERATING_AUDIO') &&
                  !rule.audio_reviewed_at && (
                    <button
                      disabled={busy}
                      onClick={() => void ruleAction(rule, 'review-audio')}
                      className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold"
                    >
                      审核音频
                    </button>
                  )}
                {(rule.status === 'READY' || rule.status === 'DISABLED') &&
                  rule.audio_reviewed_at && (
                    <button
                      disabled={busy}
                      onClick={() => void ruleAction(rule, 'enable')}
                      className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white"
                    >
                      启用规则
                    </button>
                  )}
                {rule.status === 'ENABLED' && (
                  <button
                    disabled={busy}
                    onClick={() => void ruleAction(rule, 'disable')}
                    className="rounded-lg bg-slate-200 px-3 py-2 text-xs font-bold text-slate-700"
                  >
                    停用规则
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>
      <AdminConfirmDialog
        confirmLabel="删除规则"
        description={
          deleteTarget
            ? `将删除“${deleteTarget.name} v${deleteTarget.version}”及其主持音频资产。已有房间引用的规则无法删除。`
            : ''
        }
        loading={busy}
        onConfirm={() => {
          if (deleteTarget) void deleteRule(deleteTarget);
        }}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleteTarget(null);
        }}
        open={deleteTarget !== null}
        title="确认删除赛制规则？"
      />
    </div>
  );
}

function StageEditor({
  stage,
  index,
  sideSize,
  onChange,
  onMove,
  onDelete,
}: {
  stage: Stage;
  index: number;
  sideSize: number;
  onChange: (patch: Partial<Stage>) => void;
  onMove: (direction: -1 | 1) => void;
  onDelete: () => void;
}) {
  const updateAction = (actionIndex: number, patch: Partial<FixedAction>) =>
    onChange({
      actions: stage.actions.map((action, currentIndex) =>
        currentIndex === actionIndex ? { ...action, ...patch } : action,
      ),
    });
  return (
    <article className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid size-8 place-items-center rounded-full bg-slate-950 text-xs font-black text-white">
            {index + 1}
          </span>
          <select
            value={stage.stage_kind}
            onChange={(event) =>
              onChange(newStage(event.target.value as Stage['stage_kind'], stage.id))
            }
            className={fieldClass}
          >
            <option value="FIXED_SPEECH">固定发言</option>
            <option value="FREE_DEBATE">自由辩论</option>
            <option value="PREPARATION">准备阶段</option>
          </select>
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => onMove(-1)}
            className="rounded-lg bg-white px-3 py-2 text-xs font-bold"
          >
            上移
          </button>
          <button
            onClick={() => onMove(1)}
            className="rounded-lg bg-white px-3 py-2 text-xs font-bold"
          >
            下移
          </button>
          <button
            onClick={onDelete}
            className="rounded-lg bg-red-50 px-3 py-2 text-xs font-bold text-red-700"
          >
            删除
          </button>
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <label className="text-xs font-bold text-slate-600">
          阶段名称
          <input
            value={stage.name}
            onChange={(event) => onChange({ name: event.target.value })}
            className={`mt-1 ${fieldClass}`}
          />
        </label>
        <label className="text-xs font-bold text-slate-600">
          开始主持词
          <input
            value={stage.start_host_text}
            onChange={(event) => onChange({ start_host_text: event.target.value })}
            className={`mt-1 ${fieldClass}`}
            placeholder="可留空"
          />
        </label>
        <label className="text-xs font-bold text-slate-600">
          结束主持词
          <input
            value={stage.end_host_text}
            onChange={(event) => onChange({ end_host_text: event.target.value })}
            className={`mt-1 ${fieldClass}`}
            placeholder="可留空"
          />
        </label>
      </div>
      {stage.stage_kind === 'FIXED_SPEECH' ? (
        <div className="mt-4 space-y-2">
          {stage.actions.map((action, actionIndex) => (
            <div
              key={actionIndex}
              className="grid gap-2 rounded-xl bg-white p-3 sm:grid-cols-[1fr_1fr_1fr_auto]"
            >
              <select
                value={action.side}
                onChange={(event) =>
                  updateAction(actionIndex, { side: event.target.value as FixedAction['side'] })
                }
                className={fieldClass}
              >
                <option value="AFFIRMATIVE">正方</option>
                <option value="NEGATIVE">反方</option>
              </select>
              <select
                value={action.seat_no}
                onChange={(event) =>
                  updateAction(actionIndex, { seat_no: Number(event.target.value) })
                }
                className={fieldClass}
              >
                {Array.from({ length: sideSize }, (_, seat) => (
                  <option key={seat + 1} value={seat + 1}>
                    {seat + 1} 辩
                  </option>
                ))}
              </select>
              <input
                aria-label="允许发言秒数"
                value={action.duration_seconds}
                onChange={(event) =>
                  updateAction(actionIndex, { duration_seconds: Number(event.target.value) })
                }
                type="number"
                min="1"
                max="180"
                className={fieldClass}
              />
              <button
                onClick={() =>
                  onChange({
                    actions: stage.actions.filter(
                      (_, currentIndex) => currentIndex !== actionIndex,
                    ),
                  })
                }
                className="rounded-lg px-3 text-xs font-bold text-red-600"
              >
                移除
              </button>
            </div>
          ))}
          <button
            onClick={() =>
              onChange({
                actions: [
                  ...stage.actions,
                  { side: 'AFFIRMATIVE', seat_no: 1, duration_seconds: 180 },
                ],
              })
            }
            className="rounded-lg bg-white px-3 py-2 text-xs font-bold"
          >
            ＋ 添加发言动作
          </button>
        </div>
      ) : (
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <label className="text-xs font-bold text-slate-600">
            阶段时长（秒）
            <input
              value={stage.duration_seconds}
              onChange={(event) => onChange({ duration_seconds: Number(event.target.value) })}
              type="number"
              min="1"
              max="900"
              className={`mt-1 ${fieldClass}`}
            />
          </label>
          {stage.stage_kind === 'FREE_DEBATE' && (
            <>
              <label className="text-xs font-bold text-slate-600">
                单次发言上限（秒）
                <input
                  value={stage.max_speech_seconds}
                  onChange={(event) => onChange({ max_speech_seconds: Number(event.target.value) })}
                  type="number"
                  min="1"
                  max="180"
                  className={`mt-1 ${fieldClass}`}
                />
              </label>
              <label className="text-xs font-bold text-slate-600">
                起始方
                <select
                  value={stage.starting_side}
                  onChange={(event) =>
                    onChange({ starting_side: event.target.value as Stage['starting_side'] })
                  }
                  className={`mt-1 ${fieldClass}`}
                >
                  <option value="AFFIRMATIVE">正方</option>
                  <option value="NEGATIVE">反方</option>
                </select>
              </label>
            </>
          )}
        </div>
      )}
    </article>
  );
}
