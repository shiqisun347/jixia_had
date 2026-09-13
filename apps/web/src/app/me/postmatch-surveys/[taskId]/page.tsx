'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  LoaderCircle,
  RotateCcw,
} from 'lucide-react';
import Link from 'next/link';
import { use, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { ApiClientError } from '@/lib/auth-api';
import {
  surveyApi,
  type PostmatchSurveyItem,
  type PostmatchSurveyTask,
  type SurveyChoiceQuestion,
} from '@/lib/experiments-api';
import { LocalizedTextBoundary, useLocalizedText } from '@/i18n/localized-text';

type Answer = { q1?: string | string[]; q2?: string | string[] };
type Answers = { speeches?: Record<string, Answer> };
type SaveRequest = {
  speechId: string;
  answer: Answer;
  confirm: boolean;
  expectedRevision: number;
};

const sideLabel = (item: PostmatchSurveyItem) => (item.side === 'AFFIRMATIVE' ? '正方' : '反方');
const speakerKind = (item: PostmatchSurveyItem) =>
  item.subject_kind.includes('AI') ? 'AI' : '真人';

function Choice({
  question,
  value,
  name,
  onChange,
  disabled,
}: {
  question: SurveyChoiceQuestion;
  value: string | string[] | undefined;
  name: string;
  onChange: (value: string | string[]) => void;
  disabled: boolean;
}) {
  const localize = useLocalizedText();
  const selected = Array.isArray(value) ? value : value ? [value] : [];
  return (
    <fieldset disabled={disabled}>
      <legend className="text-sm font-black leading-6 text-slate-950">{localize(question.text)}</legend>
      <div className="mt-3 grid gap-2">
        {question.options.map((option, index) => {
          const checked = selected.includes(option);
          const description = question.descriptions?.[option];
          return (
            <label
              key={option}
              className={`cursor-pointer rounded-lg border px-3 py-2.5 text-sm leading-6 transition-colors ${checked ? 'border-blue-600 bg-blue-50 font-bold text-blue-950' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'}`}
            >
              <input
                className="mr-2 align-middle"
                type={question.type === 'single_choice' ? 'radio' : 'checkbox'}
                name={name}
                checked={checked}
                    onChange={() =>
                      question.type === 'single_choice'
                        ? onChange(option)
                        : !checked && question.max_selections != null && selected.length >= question.max_selections
                          ? undefined
                        : onChange(
                        checked
                          ? selected.filter((item) => item !== option)
                          : [...selected, option],
                      )
                }
              />
              <span>
                {String.fromCharCode(65 + index)}. {localize(option)}
              </span>
              {description ? <span className="text-slate-500">: {localize(description)}</span> : null}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function SpeechBubble({
  item,
  active,
  onEdit,
  bubbleRef,
}: {
  item: PostmatchSurveyItem;
  active: boolean;
  onEdit: () => void;
  bubbleRef?: (node: HTMLButtonElement | null) => void;
}) {
  const localize = useLocalizedText();
  const affirmative = item.side === 'AFFIRMATIVE';
  const editable = item.annotatable === true && item.review_state === 'COMPLETE';
  return (
    <div className={`flex ${affirmative ? 'justify-start' : 'justify-end'}`}>
      <button
        ref={bubbleRef}
        type="button"
        disabled={!editable}
        onClick={onEdit}
        className={`max-w-[88%] text-left disabled:cursor-default md:max-w-[76%] ${active ? 'scroll-mt-28' : ''}`}
        aria-label={editable ? `修改第 ${item.sequence} 条标注` : undefined}
      >
        <span
          className={`mb-1.5 flex items-center gap-2 text-xs font-bold text-slate-500 ${affirmative ? 'justify-start' : 'justify-end'}`}
        >
          <span>
            {localize(sideLabel(item))} · {item.speaker_name || localize('辩手')}
          </span>
          <span>
            {item.seat_no ? localize(`${item.seat_no} 辩`) : ''} · {localize(speakerKind(item))}
          </span>
          {item.started_at ? (
            <span className="inline-flex items-center gap-1 text-slate-400">
              <Clock3 className="size-3" />
              {new Date(item.started_at).toLocaleTimeString(undefined, {
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
          ) : null}
        </span>
        <span
          className={`block rounded-xl border px-4 py-3 text-sm leading-7 shadow-sm ${active ? 'border-blue-500 ring-2 ring-blue-100' : affirmative ? 'border-blue-100 bg-blue-50 text-slate-800' : 'border-rose-100 bg-rose-50 text-slate-800'}`}
        >
          {item.text ? (
            <span className="whitespace-pre-wrap">{item.text}</span>
          ) : (
            <span className="text-slate-500">{localize('完成右侧判断后显示本段发言')}</span>
          )}
          {editable ? (
            <span className="mt-2 block text-xs font-bold text-blue-700">{localize('点击气泡可修改标注')}</span>
          ) : null}
        </span>
      </button>
    </div>
  );
}

export function Workspace({ initial }: { initial: PostmatchSurveyTask }) {
  const localize = useLocalizedText();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [task, setTask] = useState(initial);
  const [answers, setAnswers] = useState<Answers>(initial.answers as Answers);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [retryRequest, setRetryRequest] = useState<SaveRequest | null>(null);
  const [mobilePanelOpen, setMobilePanelOpen] = useState(true);
  const [leaveConfirmationOpen, setLeaveConfirmationOpen] = useState(false);
  const currentRef = useRef<HTMLButtonElement | null>(null);
  const backLinkRef = useRef<HTMLAnchorElement | null>(null);
  const leaveConfirmedRef = useRef(false);

  const items = useMemo(() => task.items ?? [], [task.items]);
  const targets = useMemo(() => items.filter((item) => item.annotatable), [items]);
  const current = items.find((item) =>
    ['CURRENT_PRE', 'CURRENT_POST'].includes(item.review_state ?? ''),
  );
  const editingItem = editingId
    ? items.find((item) => item.speech_id === editingId && item.review_state === 'COMPLETE')
    : undefined;
  const active = editingItem ?? current;
  const speechAnswers = answers.speeches ?? {};
  const answer = active ? (speechAnswers[active.speech_id] ?? {}) : {};
  const questions =
    active?.subject_kind === 'TEAM_AI'
      ? task.questionnaire?.team_ai
      : task.questionnaire?.human_self;
  const completed =
    task.confirmed_total ?? targets.filter((item) => item.review_state === 'COMPLETE').length;
  const targetTotal = task.target_total ?? targets.length;
  const allComplete = current == null && completed === targetTotal;

  useEffect(() => {
    if (!editingId && typeof currentRef.current?.scrollIntoView === 'function') {
      currentRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [current?.speech_id, editingId]);

  const applyTask = (next: PostmatchSurveyTask) => {
    setTask(next);
    setAnswers(next.answers as Answers);
    setRetryRequest(null);
    void queryClient.invalidateQueries({ queryKey: ['survey', 'postmatch'] });
  };
  const save = useMutation({
    mutationFn: (request: SaveRequest) =>
      surveyApi.savePostmatchSpeech(
        task.id,
        request.speechId,
        request.answer as Record<string, unknown>,
        request.confirm,
        request.expectedRevision,
      ),
    scope: { id: `postmatch-${task.id}` },
    onSuccess: applyTask,
    onError: (error, request) => {
      setRetryRequest(request);
      showToast({
        message: error instanceof ApiClientError ? error.message : localize('自动保存失败，请重试。'),
        tone: 'error',
      });
    },
  });
  const hasUnsavedSelection = save.isPending || retryRequest != null;

  useEffect(() => {
    if (!hasUnsavedSelection) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [hasUnsavedSelection]);
  const reloadLatest = async () => {
    try {
      const latest = await surveyApi.postmatchTask(task.id);
      applyTask(latest);
      showToast({ message: localize('已加载最新问卷进度。'), tone: 'success' });
    } catch (error) {
      showToast({
        message: error instanceof ApiClientError ? error.message : localize('重新加载失败，请稍后重试。'),
        tone: 'error',
      });
    }
  };
  const submit = useMutation({
    mutationFn: () => surveyApi.submitPostmatch(task.id),
    onSuccess: (next) => {
      applyTask(next);
      showToast({ message: localize('问卷已提交，下一场资格已解除。'), tone: 'success' });
    },
    onError: (error) =>
      showToast({
        message: error instanceof ApiClientError ? error.message : localize('提交失败，请稍后重试。'),
        tone: 'error',
      }),
  });


  const persistAnswer = (key: 'q1' | 'q2', value: string | string[]) => {
    if (!active) return;
    const nextAnswer = { ...answer, [key]: value };
    setAnswers((previous) => ({
      ...previous,
      speeches: { ...(previous.speeches ?? {}), [active.speech_id]: nextAnswer },
    }));
    const confirm = !editingItem && active.subject_kind === 'TEAM_AI' && key === 'q2';
    save.mutate({
      speechId: active.speech_id,
      answer: nextAnswer,
      confirm,
      expectedRevision: task.workflow_revision ?? 0,
    });
  };
  const confirmHuman = () => {
    if (active?.subject_kind === 'HUMAN_SELF')
      save.mutate({
        speechId: active.speech_id,
        answer,
        confirm: true,
        expectedRevision: task.workflow_revision ?? 0,
      });
  };
  const stageGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; items: PostmatchSurveyItem[] }> = [];
    for (const item of items) {
      const key = `${item.stage_position ?? 'unknown'}:${item.stage_name ?? '辩论记录'}`;
      const previous = groups.at(-1);
      if (previous?.key === key) previous.items.push(item);
      else groups.push({ key, label: item.stage_name || '辩论记录', items: [item] });
    }
    return groups;
  }, [items]);

  const renderQuestions = () => {
    if (task.status === 'SUBMITTED' && !editingItem)
      return <p className="text-sm leading-6 text-emerald-700">{localize('本场问卷已经提交。')}</p>;
    if (!active || !questions)
      return allComplete ? (
        <div>
          <p className="font-black text-slate-950">{localize('自由辩论标注已完成')}</p>
          <p className="mt-2 text-sm leading-6 text-slate-500">{localize('确认无误后提交本场问卷。')}</p>
          <Button
            className="mt-5 w-full"
            disabled={submit.isPending}
            onClick={() => submit.mutate()}
          >
            {submit.isPending ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Check className="size-4" />
            )}
            {localize('提交本场问卷')}
          </Button>
        </div>
      ) : (
        <p className="text-sm leading-6 text-slate-500">{localize('本场没有需要标注的自由辩论发言。')}</p>
      );
    const editing = Boolean(editingItem);
    const showQ1 = editing || answer.q1 == null;
    const showQ2 = editing || answer.q1 != null;
    return (
      <div>
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-black text-blue-700">
              {editing ? localize('修改标注') : localize(`当前第 ${active.sequence} 条`)}
            </p>
            <h2 className="mt-1 text-lg font-black text-slate-950">
              {active.speaker_name || localize('本方发言')} · {localize(speakerKind(active))}
            </h2>
          </div>
          <span className="text-xs font-bold text-slate-500">
            {save.isPending
              ? localize('正在保存…')
              : retryRequest
                ? localize('保存失败')
                : answer.q1 == null
                  ? localize('待作答')
                  : localize('已保存')}
          </span>
        </div>
        <div className="mt-5 space-y-6">
          {showQ1 ? (
            <Choice
              question={questions.q1}
              value={answer.q1}
              name={`${active.speech_id}-q1`}
              onChange={(value) => persistAnswer('q1', value)}
              disabled={save.isPending}
            />
          ) : null}
          {showQ2 ? (
            <Choice
              question={questions.q2}
              value={answer.q2}
              name={`${active.speech_id}-q2`}
              onChange={(value) => persistAnswer('q2', value)}
              disabled={save.isPending}
            />
          ) : null}
        </div>
        {retryRequest ? (
          <div className="mt-5 grid gap-2 sm:grid-cols-2">
            <Button
              variant="secondary"
              disabled={save.isPending}
              onClick={() => save.mutate(retryRequest)}
            >
              <RotateCcw className="size-4" />
              {localize('重试保存')}
            </Button>
            <Button variant="secondary" disabled={save.isPending} onClick={() => void reloadLatest()}>
              {localize('加载最新进度')}
            </Button>
          </div>
        ) : null}
        {!editing && active.subject_kind === 'HUMAN_SELF' && answer.q2 != null ? (
          <Button
            className="mt-5 w-full"
            disabled={save.isPending || !Array.isArray(answer.q2) || answer.q2.length === 0}
            onClick={confirmHuman}
          >
            {save.isPending ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Check className="size-4" />
            )}
            {localize('完成并继续')}
          </Button>
        ) : null}
        {editing ? (
          <Button
            className="mt-5 w-full"
            disabled={save.isPending || retryRequest != null}
            onClick={() => setEditingId(null)}
          >
            <Check className="size-4" />
            {localize('完成修改')}
          </Button>
        ) : null}
      </div>
    );
  };

  return (
    <LocalizedTextBoundary><main className="min-h-screen bg-[#f5f8fc] px-3 py-5 text-slate-950 md:px-8">
      <div className="mx-auto max-w-7xl">
        <Link
          ref={backLinkRef}
          href="/me/postmatch-surveys"
          onClick={(event) => {
            if (hasUnsavedSelection && !leaveConfirmedRef.current) {
              event.preventDefault();
              setLeaveConfirmationOpen(true);
            }
          }}
          className="inline-flex items-center gap-2 text-sm font-bold text-blue-700"
        >
          <ArrowLeft className="size-4" />
          返回问卷列表
        </Link>
        <header className="mt-4 border-b border-slate-200 pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-black text-blue-700">单场赛后发言问卷</p>
              <h1 className="mt-1 text-2xl font-black">
                {task.room_title || `比赛 ${task.match_id.slice(0, 8)}`}
              </h1>
              {task.topic ? (
                <p className="mt-2 text-sm text-slate-600">辩题：{task.topic}</p>
              ) : null}
            </div>
            <span
              className={`rounded-md px-3 py-1.5 text-xs font-black ${task.status === 'SUBMITTED' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}
            >
              {task.status === 'SUBMITTED' ? '已完成' : '填写中'}
            </span>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full rounded-full bg-blue-600 transition-[width]"
                style={{ width: `${targetTotal ? (completed / targetTotal) * 100 : 100}%` }}
              />
            </div>
            <span className="text-xs font-bold text-slate-600">
              {completed}/{targetTotal} 条已标注
            </span>
          </div>
        </header>
        <div className="mt-5 grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(340px,420px)]">
          <section
            className="h-[calc(100dvh-17rem)] min-h-[430px] overflow-y-auto rounded-lg border border-slate-200 bg-white px-3 py-5 shadow-sm md:px-6 lg:h-[calc(100vh-15rem)]"
            aria-label="完整辩论记录"
            tabIndex={0}
          >
            <div className="space-y-6 pb-[48vh] lg:pb-4">
              {stageGroups.map((group) => (
                <div key={group.key} className="space-y-5">
                  <div className="flex items-center gap-3" role="separator">
                    <span className="h-px flex-1 bg-slate-200" />
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-600">
                      {group.label}
                    </span>
                    <span className="h-px flex-1 bg-slate-200" />
                  </div>
                  {group.items.map((item) => (
                    <SpeechBubble
                      key={item.speech_id}
                      item={item}
                      active={item.speech_id === active?.speech_id}
                      onEdit={() => setEditingId(item.speech_id)}
                      bubbleRef={
                        item.speech_id === current?.speech_id
                          ? (node) => {
                              currentRef.current = node;
                            }
                          : undefined
                      }
                    />
                  ))}
                </div>
              ))}
            </div>
          </section>
          <aside className="fixed inset-x-3 bottom-3 z-30 max-h-[56dvh] overflow-y-auto rounded-lg border border-blue-200 bg-white p-4 shadow-xl lg:sticky lg:inset-auto lg:top-5 lg:max-h-[calc(100vh-2.5rem)] lg:p-5">
            <button
              type="button"
              className="mb-3 flex w-full items-center justify-between text-left lg:hidden"
              onClick={() => setMobilePanelOpen((open) => !open)}
              aria-expanded={mobilePanelOpen}
            >
              <span className="text-sm font-black">当前标注</span>
              {mobilePanelOpen ? (
                <ChevronDown className="size-5" />
              ) : (
                <ChevronUp className="size-5" />
              )}
            </button>
            <div className={mobilePanelOpen ? 'block' : 'hidden lg:block'}>{renderQuestions()}</div>
          </aside>
        </div>
      </div>
      <ConfirmDialog
        open={leaveConfirmationOpen}
        onOpenChange={setLeaveConfirmationOpen}
        title="离开当前问卷？"
        description="当前选择尚未保存，离开后可能需要重新填写。"
        confirmLabel="确认离开"
        onConfirm={() => {
          leaveConfirmedRef.current = true;
          setLeaveConfirmationOpen(false);
          queueMicrotask(() => backLinkRef.current?.click());
        }}
      />
    </main></LocalizedTextBoundary>
  );
}

function Content({ taskId }: { taskId: string }) {
  const query = useQuery({
    queryKey: ['survey', 'postmatch', taskId],
    queryFn: () => surveyApi.postmatchTask(taskId),
  });
  if (query.isLoading)
    return (
      <div className="p-10 text-center text-sm text-slate-500">
        <LoaderCircle className="mx-auto size-5 animate-spin" />
      </div>
    );
  if (query.isError || !query.data)
    return <div className="p-10 text-center text-sm text-red-700">问卷不存在或暂时无法访问。</div>;
  return <Workspace initial={query.data} />;
}

export default function PostmatchTaskPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = use(params);
  return (
    <ProtectedUserPage returnTo={`/me/postmatch-surveys/${taskId}`}>
      <Content taskId={taskId} />
    </ProtectedUserPage>
  );
}
