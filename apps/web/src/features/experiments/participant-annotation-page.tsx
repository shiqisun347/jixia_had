'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Check, LoaderCircle, LockKeyhole, Save } from 'lucide-react';
import Link from 'next/link';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast-provider';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { ApiClientError } from '@/lib/auth-api';
import {
  experimentsApi,
  type ParticipantAnnotationItem,
  type ParticipantAnnotationTask,
} from '@/lib/experiments-api';
import { LocalizedTextBoundary } from '@/i18n/localized-text';

import { AudioWaveform } from './audio-waveform';

const HUMAN_PRIMARY_REASONS = [
  '自己更适合处理这个问题。',
  '担心AI 不适合或者处理不好这个问题。',
  '没人回答或者其他担忧被迫回答',
  '其他。',
];
const HUMAN_GOALS = [
  ['回应对手', '对手刚提出了需要处理的攻击、质疑或追问'],
  ['接续队友', '队友的内容需要补充、澄清或继续推进'],
  ['补足缺口', '我注意到己方有一个重要问题还没人处理'],
  ['主动推进', '我认为应该推进新的或已有的己方论证'],
  ['调整方向', '我认为当前讨论需要转向更重要的问题'],
  ['其他', ''],
] as const;
const AI_APPROPRIATENESS = [
  '不合适，比如有此时其他人更合适发言：',
  '发言不发言都合适：',
  '很适合 AI 发言',
  '无法判断：',
];
const AI_MATCH = [
  '很好，处理了当时团队真正需要处理的问题',
  '一般，内容有点冗余，重复表达，新增价值很小',
  '一般，有点跑偏或者钻牛角尖，内容可能有价值，但不是当时最需要处理的问题',
  '不好，为后续带来了额外的修复负担',
  '其他不好或者一般的原因',
  '无法判断',
];
const QUESTIONNAIRE = [
  {
    text: '这场比赛里，AI 更像一个会和队友配合的辩手，还是只顾自己对抗的辩手？',
    options: [
      '完全只顾自己，几乎不管队友',
      '大多只顾自己，偶尔配合队友',
      '两方面差不多',
      '大多能配合队友',
      '很像人类队友，会主动补充、接续和配合',
    ],
  },
  {
    text: 'AI 的发言通常有没有接住队友刚才说的内容和场上的情况？',
    options: [
      '几乎没有，常常各说各的',
      '很少接得上',
      '有时接得上，有时接不上',
      '大多数时候接得上',
      '几乎总能接住并继续推进',
    ],
  },
  {
    text: 'AI 的发言对我们团队有多大帮助？',
    options: ['明显没帮助，甚至添乱', '帮助很少', '有一点帮助', '比较有帮助', '帮助很大'],
  },
  {
    text: '这场比赛里，AI 有没有给你带来额外负担？比如它说得不清楚、有漏洞、和队友重复，你还要花力气去理解、补充或修正。',
    options: ['几乎没有', '很少', '有一点', '不少', '很多'],
  },
  {
    text: '如果下一场还要和这个 AI 一起辩，你愿意继续把它当作队友吗？',
    options: ['完全不愿意', '不太愿意', '说不上', '比较愿意', '非常愿意'],
  },
];

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '保存失败，请稍后重试。';
}

function nextVersion(item: ParticipantAnnotationItem, stage: 1 | 2) {
  return (
    Math.max(
      0,
      ...Object.entries(item.answer_versions)
        .filter(([key]) => key.startsWith(`stage${stage}.`))
        .map(([, value]) => value),
    ) + 1
  );
}

function ContextView({ context }: Readonly<{ context: Record<string, unknown> }>) {
  const history = Array.isArray(context.history)
    ? context.history.filter(
        (value): value is Record<string, unknown> =>
          Boolean(value) && typeof value === 'object' && !Array.isArray(value),
      )
    : [];
  const remaining = [
    ['正方剩余', context.affirmative_remaining_ms],
    ['反方剩余', context.negative_remaining_ms],
  ].filter((entry): entry is [string, number] => typeof entry[1] === 'number');
  return (
    <LocalizedTextBoundary><div className="text-sm leading-7 text-slate-700">
      {remaining.length ? (
        <div className="grid grid-cols-2 gap-3 border-b border-slate-100 pb-4">
          {remaining.map(([label, milliseconds]) => (
            <div className="rounded-lg bg-slate-50 px-3 py-2" key={label}>
              <p className="text-xs font-black text-slate-500">{label}</p>
              <p className="mt-1 font-black text-slate-900">
                {Math.floor(milliseconds / 60_000)}:
                {String(Math.floor((milliseconds % 60_000) / 1000)).padStart(2, '0')}
              </p>
            </div>
          ))}
        </div>
      ) : null}
      {history.length ? (
        <div className="divide-y divide-slate-100">
          {history.map((speech, index) => (
            <article className="py-4" key={String(speech.speech_id ?? index)}>
              <p className="text-xs font-black text-blue-700">
                {speech.side === 'AFFIRMATIVE' ? '正方' : '反方'} · {String(speech.seat_no ?? '?')}{' '}
                辩 · {speech.speaker_kind === 'AGENT' ? 'AI' : '真人'}
              </p>
              <p className="mt-1 whitespace-pre-wrap break-words">
                {String(speech.text ?? '') || '（无可用文字）'}
              </p>
            </article>
          ))}
        </div>
      ) : (
        <p className="pt-4 text-slate-500">该时点没有更早的辩论记录。</p>
      )}
    </div></LocalizedTextBoundary>
  );
}

function ChoiceGroup({
  label,
  options,
  value,
  disabled,
  onChange,
}: Readonly<{
  label: string;
  options: string[];
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}>) {
  return (
    <LocalizedTextBoundary><fieldset disabled={disabled}>
      <legend className="text-sm font-black text-slate-900">{label}</legend>
      <div className="mt-3 flex flex-wrap gap-2">
        {options.map((option, index) => (
          <label
            className={`cursor-pointer rounded-lg border px-3 py-2 text-sm font-bold ${value === option ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 bg-white text-slate-700'}`}
            key={option}
          >
            <input
              checked={value === option}
              className="sr-only"
              name={label}
              onChange={() => onChange(option)}
              type="radio"
              value={option}
            />
            {String.fromCharCode(65 + index)}. {option}
          </label>
        ))}
      </div>
    </fieldset></LocalizedTextBoundary>
  );
}

function ItemForm({
  task,
  item,
  onSaved,
}: Readonly<{
  task: ParticipantAnnotationTask;
  item: ParticipantAnnotationItem;
  onSaved: (task: ParticipantAnnotationTask) => void;
}>) {
  const { showToast } = useToast();
  const [primary, setPrimary] = useState(String(item.answers['stage1.primary_reason'] ?? ''));
  const [goals, setGoals] = useState<string[]>(
    Array.isArray(item.answers['stage1.goals'])
      ? (item.answers['stage1.goals'] as string[])
      : Array.isArray(item.answers['stage1.reasons'])
        ? (item.answers['stage1.reasons'] as string[])
        : [],
  );
  const [teamNeedMatch, setTeamNeedMatch] = useState(
    String(
      item.answers['stage2.team_need_match'] ??
        (Array.isArray(item.answers['stage2.match_categories'])
          ? item.answers['stage2.match_categories'][0]
          : ''),
    ),
  );
  const [audioPlayCount, setAudioPlayCount] = useState(0);
  const save = useMutation({
    mutationFn: (payload: Parameters<typeof experimentsApi.saveParticipantItem>[2]) =>
      experimentsApi.saveParticipantItem(task.id, item.id, payload),
    onSuccess: (next) => {
      onSaved(next);
      showToast({ message: '本题已保存。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const locked = task.status === 'SUBMITTED';
  const saveHuman = (nextPrimary: string, nextReasons: string[]) => {
    if (!nextPrimary || nextReasons.length === 0) return;
    save.mutate({
      stage: 1,
      answers: { primary_reason: nextPrimary, goals: nextReasons },
      client_version: nextVersion(item, 1),
      audio_play_count: audioPlayCount,
      lock_stage: false,
    });
  };
  const saveAiMatch = (nextValue: string) => {
    if (!nextValue) return;
    save.mutate({
      stage: 2,
      answers: { team_need_match: nextValue },
      client_version: nextVersion(item, 2),
      audio_play_count: audioPlayCount,
      lock_stage: false,
    });
  };

  return (
    <LocalizedTextBoundary><div className="grid min-h-0 gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(20rem,.85fr)]">
      <section className="min-h-0 overflow-y-auto border-r-0 border-slate-100 pr-0 lg:border-r lg:pr-6">
        <p className="text-xs font-black text-blue-700">
          {item.subject_kind === 'HUMAN_SELF' ? '我的实际发言' : '本方 AI 发言'}
        </p>
        <h2 className="mt-2 text-xl font-black">发言前状态</h2>
        <div className="mt-5">
          <ContextView context={item.frozen_context} />
        </div>
        {item.revealed && item.speech_text !== null ? (
          <div className="mt-6 border-t border-blue-100 pt-5">
            <p className="text-xs font-black text-blue-700">实际发言</p>
            <AudioWaveform
              onPlay={() => setAudioPlayCount((count) => count + 1)}
              url={`/api/experiments/tasks/${task.id}/audio/${item.speech_id}`}
            />
            <p className="mt-4 text-xs font-black text-slate-500">文字记录</p>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-800">
              {item.speech_text || '（本次发言没有可用文字）'}
            </p>
          </div>
        ) : null}
      </section>

      <section className="min-h-0 overflow-y-auto">
        {item.subject_kind === 'HUMAN_SELF' ? (
          <div className="space-y-7">
            <ChoiceGroup
              disabled={locked || save.isPending}
              label="Q1 当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？请选择最主要的原因。"
              onChange={(value) => {
                setPrimary(value);
                saveHuman(value, goals);
              }}
              options={HUMAN_PRIMARY_REASONS}
              value={primary}
            />
            <fieldset disabled={locked || save.isPending}>
              <legend className="text-sm font-black text-slate-900">
                Q2 你这次发言最主要想完成什么？
              </legend>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {HUMAN_GOALS.map(([goal, description], index) => (
                  <label
                    className={`cursor-pointer rounded-lg border px-3 py-2 text-sm font-bold ${goals.includes(goal) ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-slate-200 text-slate-700'}`}
                    key={goal}
                  >
                    <input
                      checked={goals.includes(goal)}
                      className="mr-2"
                      onChange={() => {
                        const next = goals.includes(goal)
                          ? goals.filter((value) => value !== goal)
                          : [...goals, goal];
                        setGoals(next);
                        saveHuman(primary, next);
                      }}
                      type="checkbox"
                    />
                    <span>
                      {String.fromCharCode(65 + index)}. {goal}
                    </span>
                    {description ? (
                      <span className="mt-1 block text-xs font-normal leading-5 opacity-80">
                        ：{description}
                      </span>
                    ) : null}
                  </label>
                ))}
              </div>
            </fieldset>
            <Button
              disabled={!primary || goals.length === 0 || save.isPending || locked}
              onClick={() => saveHuman(primary, goals)}
            >
              {save.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
              保存本题
            </Button>
          </div>
        ) : !item.stage1_locked ? (
          <div>
            <p className="text-sm font-black text-slate-900">
              Q1 在发言之前，当时你觉得 AI 这个时候发言合适吗
            </p>
            <div className="mt-4 grid gap-2">
              {AI_APPROPRIATENESS.map((option, index) => (
                <Button
                  disabled={save.isPending || locked}
                  key={option}
                  onClick={() =>
                    save.mutate({
                      stage: 1,
                      answers: { appropriateness: option },
                      client_version: nextVersion(item, 1),
                      audio_play_count: 0,
                      lock_stage: true,
                    })
                  }
                  variant="secondary"
                >
                  <LockKeyhole className="size-4" /> {String.fromCharCode(65 + index)}. {option}
                </Button>
              ))}
            </div>
            <p className="mt-4 text-xs leading-6 text-slate-500">
              锁定后才会显示 AI 的实际发言，且不能修改这一判断。
            </p>
          </div>
        ) : (
          <div>
            <p className="flex items-center gap-2 text-xs font-black text-emerald-700">
              <Check className="size-4" /> 事前判断已锁定
            </p>
            <fieldset className="mt-5" disabled={locked || save.isPending}>
              <legend className="text-sm font-black text-slate-900">
                Q2 看完 AI 的实际发言后，你认为这次发言与当时团队需要的匹配程度如何？（不仅看内容）
              </legend>
              <div className="mt-3 grid gap-2">
                {AI_MATCH.map((option, index) => (
                  <label
                    className={`cursor-pointer rounded-lg border px-3 py-2 text-sm font-bold ${teamNeedMatch === option ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-slate-200 text-slate-700'}`}
                    key={option}
                  >
                    <input
                      checked={teamNeedMatch === option}
                      className="mr-2"
                      onChange={() => {
                        setTeamNeedMatch(option);
                        saveAiMatch(option);
                      }}
                      name={`team-need-${item.id}`}
                      type="radio"
                    />
                    {String.fromCharCode(65 + index)}. {option}
                  </label>
                ))}
              </div>
            </fieldset>
            <Button
              className="mt-5"
              disabled={!teamNeedMatch || save.isPending || locked}
              onClick={() => saveAiMatch(teamNeedMatch)}
            >
              {save.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
              保存本题
            </Button>
          </div>
        )}
      </section>
    </div></LocalizedTextBoundary>
  );
}

function Questionnaire({
  task,
  onSaved,
}: Readonly<{
  task: ParticipantAnnotationTask;
  onSaved: (task: ParticipantAnnotationTask) => void;
}>) {
  const { showToast } = useToast();
  const [values, setValues] = useState<(number | null)[]>(() =>
    task.questionnaire
      ? [
          task.questionnaire.q1,
          task.questionnaire.q2,
          task.questionnaire.q3,
          task.questionnaire.q4,
          task.questionnaire.q5,
        ]
      : [null, null, null, null, null],
  );
  const save = useMutation({
    mutationFn: (nextValues: (number | null)[] = values) =>
      experimentsApi.saveQuestionnaire(task.id, {
        q1: nextValues[0] as number,
        q2: nextValues[1] as number,
        q3: nextValues[2] as number,
        q4: nextValues[3] as number,
        q5: nextValues[4] as number,
      }),
    onSuccess: (next) => {
      onSaved(next);
      showToast({ message: '赛后辩手体验问卷已保存。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const locked = task.status === 'SUBMITTED';
  return (
    <LocalizedTextBoundary><section className="mx-auto max-w-3xl">
      <h2 className="text-2xl font-black">赛后辩手体验问卷</h2>
      <p className="mt-2 text-sm text-slate-500">
        请根据刚才这场比赛的整体感受，选择最符合你想法的答案。
      </p>
      <div className="mt-6 divide-y divide-slate-100">
        {QUESTIONNAIRE.map((question, index) => (
          <fieldset className="py-5" disabled={locked || save.isPending} key={question.text}>
            <legend className="text-sm font-black text-slate-900">
              {index + 1}. {question.text}
            </legend>
            <div className="mt-3 grid gap-2">
              {question.options.map((option, optionIndex) => {
                const score = optionIndex + 1;
                return (
                  <label
                    className={`cursor-pointer rounded-lg border px-3 py-2 text-sm font-bold ${values[index] === score ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 text-slate-600'}`}
                    key={score}
                  >
                    <input
                      checked={values[index] === score}
                      className="sr-only"
                      name={`q${index + 1}`}
                      onChange={() => {
                        const next = values.map((value, itemIndex) =>
                          itemIndex === index ? score : value,
                        );
                        setValues(next);
                        if (next.every((value) => value !== null)) save.mutate(next);
                      }}
                      type="radio"
                    />
                    {score} {option}
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
      <Button
        className="mt-5"
        disabled={values.some((value) => value === null) || save.isPending || locked}
        onClick={() => save.mutate(values)}
      >
        {save.isPending ? (
          <LoaderCircle className="size-4 animate-spin" />
        ) : (
          <Save className="size-4" />
        )}
        保存赛后辩手体验问卷
      </Button>
    </section></LocalizedTextBoundary>
  );
}

function ParticipantAnnotationContent({ taskId }: Readonly<{ taskId: string }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [position, setPosition] = useState(0);
  const tasks = useQuery({
    queryKey: ['experiments', 'participant-tasks'],
    queryFn: experimentsApi.participantTasks,
  });
  const task = tasks.data?.find((candidate) => candidate.id === taskId);
  const steps = (task?.items.length ?? 0) + 1;
  const currentItem = task?.items[position];
  const updateTask = (next: ParticipantAnnotationTask) => {
    queryClient.setQueryData<ParticipantAnnotationTask[]>(
      ['experiments', 'participant-tasks'],
      (current) => current?.map((value) => (value.id === next.id ? next : value)) ?? [next],
    );
  };
  const submit = useMutation({
    mutationFn: () => experimentsApi.submitParticipantTask(taskId),
    onSuccess: (next) => {
      updateTask(next);
      showToast({ message: '本场标注已提交，答案已锁定。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const completedItems = useMemo(
    () =>
      task?.items.filter((item) =>
        item.subject_kind === 'HUMAN_SELF'
          ? Boolean(
              item.answers['stage1.primary_reason'] &&
              (item.answers['stage1.goals'] || item.answers['stage1.reasons']),
            )
          : Boolean(
              item.stage1_locked &&
              (item.answers['stage2.team_need_match'] || item.answers['stage2.match_categories']),
            ),
      ).length ?? 0,
    [task],
  );

  if (tasks.isPending) {
    return (
      <main className="jx-page-viewport grid place-items-center">
        <LoaderCircle className="size-8 animate-spin text-blue-600" />
      </main>
    );
  }
  if (!task) {
    return (
      <main className="jx-page-viewport grid place-items-center">
        <p className="font-bold text-slate-600">没有找到该标注任务。</p>
      </main>
    );
  }
  const atQuestionnaire = position >= task.items.length;
  return (
    <LocalizedTextBoundary><main className="jx-page-viewport bg-[#f7faff] px-4 py-6 text-slate-950 md:px-7">
      <div className="mx-auto flex min-h-[calc(100vh-7rem)] max-w-7xl flex-col bg-white shadow-sm">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            <Link
              className="inline-flex items-center gap-1 text-xs font-black text-blue-700"
              href="/experiments"
            >
              <ArrowLeft className="size-3.5" /> 返回实验安排
            </Link>
            <h1 className="mt-2 text-xl font-black">事件标注与赛后辩手体验问卷</h1>
          </div>
          <span className="text-sm font-bold text-slate-500">
            事件 {completedItems}/{task.items.length} · 问卷{' '}
            {task.questionnaire ? '已保存' : '待填写'}
          </span>
        </header>
        <nav
          className="flex gap-1 overflow-x-auto border-b border-slate-100 px-5 py-3"
          aria-label="标注步骤"
        >
          {Array.from({ length: steps }, (_, index) => (
            <button
              aria-label={index < task.items.length ? `事件 ${index + 1}` : '赛后辩手体验问卷'}
              className={`h-2 min-w-10 flex-1 rounded-full ${index === position ? 'bg-blue-600' : index < completedItems ? 'bg-emerald-400' : 'bg-slate-200'}`}
              key={index}
              onClick={() => setPosition(index)}
              type="button"
            />
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto p-5 md:p-7">
          {atQuestionnaire ? (
            <Questionnaire onSaved={updateTask} task={task} />
          ) : currentItem ? (
            <ItemForm
              item={currentItem}
              key={`${currentItem.id}-${currentItem.stage1_locked}`}
              onSaved={updateTask}
              task={task}
            />
          ) : null}
        </div>
        <footer className="flex items-center justify-between border-t border-slate-100 px-5 py-4">
          <Button
            disabled={position === 0}
            onClick={() => setPosition((value) => Math.max(0, value - 1))}
            variant="secondary"
          >
            <ArrowLeft className="size-4" /> 上一项
          </Button>
          {atQuestionnaire ? (
            <Button
              disabled={submit.isPending || task.status === 'SUBMITTED'}
              onClick={() => submit.mutate()}
            >
              {submit.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Check className="size-4" />
              )}
              {task.status === 'SUBMITTED' ? '已提交' : '提交全部标注'}
            </Button>
          ) : (
            <Button
              onClick={() => setPosition((value) => Math.min(steps - 1, value + 1))}
              variant="secondary"
            >
              下一项 <ArrowRight className="size-4" />
            </Button>
          )}
        </footer>
      </div>
    </main></LocalizedTextBoundary>
  );
}

export function ParticipantAnnotationPage({ taskId }: Readonly<{ taskId: string }>) {
  return (
    <ProtectedUserPage returnTo={`/experiments/annotations/${taskId}`}>
      <ParticipantAnnotationContent taskId={taskId} />
    </ProtectedUserPage>
  );
}
