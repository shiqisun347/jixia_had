'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, LoaderCircle, Save } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast-provider';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { ApiClientError } from '@/lib/auth-api';
import { experimentsApi, type ExpertAnnotationTask } from '@/lib/experiments-api';
import { LocalizedTextBoundary } from '@/i18n/localized-text';

import { AudioWaveform } from './audio-waveform';

const Q1 = ['无明显需求', '有事项但无明确优先', '较明确优先', '明显且紧迫', '无法判断'] as const;
const ACTIONS = [
  '回应对手',
  '接续队友',
  '补足缺口',
  '主动推进',
  '调整方向',
  '让出机会',
  '其他',
] as const;
const NECESSITY = ['不合适', '可接受但非优先', '较有必要', '应优先尽快', '无法判断'] as const;
const Q3 = ['更应让给人类', 'AI 或人类都合理', '更应 AI 介入', '无法判断'] as const;

function message(error: unknown) {
  return error instanceof ApiClientError ? error.message : '保存失败，请稍后重试。';
}

type ExpertItem = ExpertAnnotationTask['items'][number];

function FrozenState({
  payload,
  taskId,
}: Readonly<{ payload: Record<string, unknown>; taskId: string }>) {
  const context =
    payload.context && typeof payload.context === 'object' && !Array.isArray(payload.context)
      ? (payload.context as Record<string, unknown>)
      : {};
  const history = Array.isArray(context.history)
    ? (context.history.filter(
        (value): value is Record<string, unknown> =>
          Boolean(value) && typeof value === 'object' && !Array.isArray(value),
      ) as Record<string, unknown>[])
    : [];
  const remainingText = (value: unknown) => {
    if (typeof value !== 'number') return '未知';
    return `${Math.floor(value / 60_000)}:${String(Math.floor((value % 60_000) / 1000)).padStart(2, '0')}`;
  };
  const metadata = [
    ['机会序号', String(payload.sequence_no ?? '-')],
    ['候选方', payload.side === 'AFFIRMATIVE' ? '正方' : '反方'],
    [
      '形成来源',
      payload.trigger_kind === 'HUMAN_SPEECH'
        ? '真人发言'
        : payload.trigger_kind === 'AGENT_SPEECH'
          ? 'AI 发言'
          : '自由辩论开始',
    ],
    ['正方剩余', remainingText(context.affirmative_remaining_ms)],
    ['反方剩余', remainingText(context.negative_remaining_ms)],
  ];
  return (
    <LocalizedTextBoundary><div className="mt-5 space-y-5 text-sm leading-7 text-slate-700">
      <div className="grid gap-3 border-b border-slate-100 pb-5 sm:grid-cols-2">
        {metadata.map(([key, value]) => (
          <div key={key}>
            <p className="text-xs font-black text-slate-500">{key}</p>
            <p className="mt-1 whitespace-pre-wrap break-words">
              {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
            </p>
          </div>
        ))}
      </div>
      <div>
        <h3 className="text-sm font-black text-slate-900">此前辩论记录</h3>
        {history.length ? (
          <div className="mt-3 divide-y divide-slate-100">
            {history.map((speech, index) => {
              const speechId = String(speech.speech_id ?? '');
              const side = speech.side === 'AFFIRMATIVE' ? '正方' : '反方';
              return (
                <article className="py-4" key={speechId || index}>
                  <p className="text-xs font-black text-blue-700">
                    {side} · {String(speech.seat_no ?? '?')} 辩 ·{' '}
                    {speech.speaker_kind === 'AGENT' ? 'AI' : '真人'}
                  </p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-7">
                    {String(speech.text ?? '') || '（无可用文字）'}
                  </p>
                  {speechId ? (
                    <AudioWaveform
                      className="mt-3"
                      url={`/api/experiments/expert-tasks/${taskId}/audio/${speechId}`}
                    />
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="mt-3 text-slate-500">该机会之前没有已完成发言。</p>
        )}
      </div>
    </div></LocalizedTextBoundary>
  );
}

function ExpertQuestionPanel({
  task,
  item,
  onUpdated,
}: Readonly<{
  task: ExpertAnnotationTask;
  item: ExpertItem;
  onUpdated: (next: ExpertAnnotationTask) => void;
}>) {
  const { showToast } = useToast();
  const [q1, setQ1] = useState<(typeof Q1)[number] | ''>(
    (item.q1 as (typeof Q1)[number] | null) ?? '',
  );
  const [q2, setQ2] = useState<Record<string, (typeof NECESSITY)[number]>>(
    (item.q2 as Record<string, (typeof NECESSITY)[number]> | null) ?? {},
  );
  const [q3, setQ3] = useState<(typeof Q3)[number] | ''>(
    (item.q3 as (typeof Q3)[number] | null) ?? '',
  );
  const save = useMutation({
    mutationFn: (lock: boolean) =>
      experimentsApi.saveExpertItem(task.id, item.opportunity_id, {
        q1: q1 as (typeof Q1)[number],
        q2,
        q3: q3 as (typeof Q3)[number],
        client_version: (item.client_version ?? 0) + 1,
        submit: lock,
      }),
    onSuccess: (next) => {
      onUpdated(next);
      showToast({ message: '当前机会已保存。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const valid = Boolean(q1 && q3 && ACTIONS.every((action) => q2[action]));
  const locked = Boolean(item.submitted_at) || task.status === 'SUBMITTED';

  return (
    <LocalizedTextBoundary><>
      <fieldset disabled={locked}>
        <legend className="text-sm font-black">Q1 当前是否存在明确的团队行动需求？</legend>
        <div className="mt-3 grid gap-2">
          {Q1.map((value) => (
            <label
              className={`rounded-lg border px-3 py-2 text-sm font-bold ${q1 === value ? 'border-blue-600 bg-blue-50 text-blue-800' : 'border-slate-200'}`}
              key={value}
            >
              <input
                checked={q1 === value}
                className="mr-2"
                onChange={() => setQ1(value)}
                type="radio"
              />
              {value}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="mt-7" disabled={locked}>
        <legend className="text-sm font-black">Q2 各行动的必要程度</legend>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[38rem] border-collapse text-xs">
            <thead>
              <tr>
                <th className="p-2 text-left">行动</th>
                {NECESSITY.map((value) => (
                  <th className="p-2 font-bold" key={value}>
                    {value}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ACTIONS.map((action) => (
                <tr className="border-t border-slate-100" key={action}>
                  <th className="p-2 text-left font-black">{action}</th>
                  {NECESSITY.map((value) => (
                    <td className="p-2 text-center" key={value}>
                      <input
                        aria-label={`${action}-${value}`}
                        checked={q2[action] === value}
                        onChange={() => setQ2((current) => ({ ...current, [action]: value }))}
                        type="radio"
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </fieldset>
      <fieldset className="mt-7" disabled={locked}>
        <legend className="text-sm font-black">Q3 这个机会更适合谁介入？</legend>
        <div className="mt-3 grid gap-2">
          {Q3.map((value) => (
            <label
              className={`rounded-lg border px-3 py-2 text-sm font-bold ${q3 === value ? 'border-blue-600 bg-blue-50 text-blue-800' : 'border-slate-200'}`}
              key={value}
            >
              <input
                checked={q3 === value}
                className="mr-2"
                onChange={() => setQ3(value)}
                type="radio"
              />
              {value}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mt-7 flex flex-wrap gap-3">
        <Button
          disabled={!valid || save.isPending || locked}
          onClick={() => save.mutate(false)}
          variant="secondary"
        >
          <Save className="size-4" /> 保存
        </Button>
        <Button disabled={!valid || save.isPending || locked} onClick={() => save.mutate(true)}>
          <Check className="size-4" /> 锁定本机会
        </Button>
      </div>
    </></LocalizedTextBoundary>
  );
}

function ExpertWorkspaceContent({ taskId }: Readonly<{ taskId: string }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [index, setIndex] = useState(0);
  const tasks = useQuery({
    queryKey: ['experiments', 'expert-tasks'],
    queryFn: experimentsApi.expertTasks,
  });
  const task = tasks.data?.find((candidate) => candidate.id === taskId);
  const item = task?.items[index];
  const update = (next: ExpertAnnotationTask) =>
    queryClient.setQueryData<ExpertAnnotationTask[]>(
      ['experiments', 'expert-tasks'],
      (current) => current?.map((value) => (value.id === next.id ? next : value)) ?? [next],
    );
  const finalSubmit = useMutation({
    mutationFn: () => experimentsApi.submitExpertTask(taskId),
    onSuccess: (next) => {
      update(next);
      showToast({ message: '专家任务已最终提交。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });

  if (tasks.isPending)
    return (
      <main className="jx-page-viewport grid place-items-center">
        <LoaderCircle className="size-8 animate-spin text-blue-600" />
      </main>
    );
  if (!task || !item)
    return (
      <main className="jx-page-viewport grid place-items-center">
        <p className="font-bold text-slate-600">当前没有可标注的机会。</p>
      </main>
    );
  return (
    <LocalizedTextBoundary><main className="jx-page-viewport bg-[#f7faff] p-4 text-slate-950 md:p-7">
      <div className="mx-auto grid min-h-[calc(100vh-7rem)] max-w-[1500px] grid-cols-1 overflow-hidden bg-white shadow-sm lg:grid-cols-[15rem_minmax(0,1fr)_minmax(25rem,.85fr)]">
        <aside className="border-b border-slate-100 p-4 lg:border-b-0 lg:border-r">
          <Link className="text-xs font-black text-blue-700" href="/experiments">
            返回实验安排
          </Link>
          <h1 className="mt-3 text-xl font-black">专家机会标注</h1>
          <p className="mt-1 text-xs text-slate-500">{task.items.length} 个机会</p>
          <div className="mt-5 grid grid-cols-6 gap-1 lg:grid-cols-4">
            {task.items.map((candidate, candidateIndex) => (
              <button
                aria-label={`机会 ${candidateIndex + 1}`}
                className={`h-9 rounded-md text-xs font-black ${candidateIndex === index ? 'bg-blue-600 text-white' : candidate.submitted_at ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}
                key={candidate.opportunity_id}
                onClick={() => setIndex(candidateIndex)}
                type="button"
              >
                {candidateIndex + 1}
              </button>
            ))}
          </div>
        </aside>

        <section className="min-h-0 overflow-y-auto border-b border-slate-100 p-5 lg:border-b-0 lg:border-r lg:p-7">
          <p className="text-xs font-black text-blue-700">机会 {index + 1} · 仅显示此前信息</p>
          <h2 className="mt-2 text-xl font-black">冻结场上状态</h2>
          <FrozenState payload={item.frozen_payload} taskId={task.id} />
        </section>

        <section className="min-h-0 overflow-y-auto p-5 lg:p-7">
          <ExpertQuestionPanel
            item={item}
            key={item.opportunity_id}
            onUpdated={update}
            task={task}
          />
          <div className="mt-3">
            <Button
              disabled={finalSubmit.isPending || task.status === 'SUBMITTED'}
              onClick={() => finalSubmit.mutate()}
              variant="danger"
            >
              最终提交全部
            </Button>
          </div>
        </section>
      </div>
    </main></LocalizedTextBoundary>
  );
}

export function ExpertWorkspace({ taskId }: Readonly<{ taskId: string }>) {
  return (
    <ProtectedUserPage returnTo={`/experiments/expert/${taskId}`}>
      <ExpertWorkspaceContent taskId={taskId} />
    </ProtectedUserPage>
  );
}
