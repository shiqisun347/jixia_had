'use client';

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';

import { buttonVariants } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast-provider';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { ApiClientError, authApi, requestJson } from '@/lib/auth-api';
import { matchesApi } from '@/lib/matches-api';
import {
  judgeFailureText,
  judgeStatusText,
  replayStatusText,
} from '@/features/postmatch/terminal-copy';
import { useSingleFlight } from '@/hooks/use-single-flight';

type Speech = {
  id: string;
  speaker_kind: string;
  side: string;
  seat_no: number;
  display_text: string | null;
  asr_raw_final_text: string | null;
  user_id: string | null;
};

type Participant = {
  id: string;
  kind: string;
  display_name: string;
  side: string;
  seat_no: number;
};

type Postmatch = {
  match_id: string;
  status: string;
  title: string;
  label: string;
  display_topic: string;
  admin_note: string | null;
  context_version: number;
  speeches: Speech[];
  participants: Participant[];
  submissions: Array<{ user_id: string; submitted_at: string }>;
  files: Array<{
    id: string;
    file_kind: string;
    status: string;
    owner_user_id: string | null;
    duration_ms: number | null;
    byte_count: number;
    error_code?: string | null;
    download_url: string;
  }>;
  judge: {
    status: string;
    result: {
      winner?: string;
      team_scores?: Record<string, Record<string, number>>;
      participants?: Array<{ participant_id: string; score: number; comment?: string }>;
      team_comments?: Record<string, string>;
    } | null;
    error_code?: string | null;
  } | null;
  can_retry_judge: boolean;
};

type JudgeDraft = {
  winner: 'AFFIRMATIVE' | 'NEGATIVE' | 'DRAW';
  team_scores: Record<string, Record<string, number>>;
  participants: Array<{ participant_id: string; score: number; comment?: string }>;
  team_comments: Record<string, string>;
};

export async function commitJudgeResult<T>(
  patch: () => Promise<unknown>,
  refresh: () => Promise<T>,
): Promise<{ refreshed: true; data: T } | { refreshed: false }> {
  await patch();
  try {
    return { refreshed: true, data: await refresh() };
  } catch {
    return { refreshed: false };
  }
}

const sideName = (side: string) => (side === 'AFFIRMATIVE' ? '正方' : '反方');

const dimensionNames: Record<string, string> = {
  argument: '立论',
  rebuttal: '反驳',
  evidence: '事实与证据',
  teamwork: '团队协作',
  expression: '表达与规则',
};

const judgeDimensions = ['argument', 'rebuttal', 'evidence', 'teamwork', 'expression'];

function toJudgeDraft(result: NonNullable<NonNullable<Postmatch['judge']>['result']>): JudgeDraft {
  return {
    winner: (result.winner as JudgeDraft['winner']) || 'DRAW',
    team_scores: result.team_scores || {},
    participants: result.participants || [],
    team_comments: result.team_comments || {},
  };
}

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiClientError) return error.message;
  return fallback;
}

function AudioArchive({
  replay,
  rawFiles,
  replayMessage,
}: {
  replay: Postmatch['files'][number] | undefined;
  rawFiles: Postmatch['files'];
  replayMessage: ReturnType<typeof replayStatusText>;
}) {
  return (
    <section className="rounded-3xl border border-blue-100 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-black tracking-[0.16em] text-blue-600">AUDIO ARCHIVE</p>
          <h2 className="mt-1 text-xl font-black">回放与原始音轨</h2>
        </div>
        <span className="text-xs text-slate-500">音频按钮仅对参赛者和管理员显示</span>
      </div>
      {replayMessage ? (
        <p
          className={`mt-5 rounded-xl p-4 text-sm ${
            replayMessage.tone === 'info'
              ? 'bg-blue-50 text-blue-700'
              : replayMessage.tone === 'warning'
                ? 'bg-amber-50 text-amber-700'
                : 'bg-slate-50 text-slate-500'
          }`}
        >
          {replayMessage.text}
        </p>
      ) : replay ? (
        <div className="mt-5 rounded-2xl bg-slate-50 p-4">
          <audio className="w-full" controls preload="metadata" src={replay.download_url} />
          <a
            href={replay.download_url}
            className="mt-3 inline-flex rounded-lg bg-slate-950 px-4 py-2 text-xs font-bold text-white"
          >
            下载整场 Opus 回放
          </a>
        </div>
      ) : null}
      {!!rawFiles.length && (
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          {rawFiles.map((file) => (
            <div
              key={file.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-slate-100 p-4"
            >
              <div>
                <p className="text-sm font-bold">
                  {file.file_kind === 'HUMAN_RAW' ? '人类原始音轨' : 'Agent 原始音轨'}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {file.duration_ms ? `${(file.duration_ms / 1000).toFixed(1)} 秒` : '时长未知'} ·{' '}
                  {file.status}
                </p>
              </div>
              {file.status === 'READY' && (
                <a
                  href={file.download_url}
                  className="rounded-lg bg-blue-50 px-3 py-2 text-xs font-bold text-blue-700"
                >
                  下载
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default function PostmatchPage() {
  const params = useParams<{ matchId: string }>();
  return (
    <ProtectedUserPage returnTo={`/matches/${params.matchId}`}>
      <PostmatchContent />
    </ProtectedUserPage>
  );
}

export function PostmatchContent({
  matchId: matchIdOverride,
}: Readonly<{ matchId?: string }> = {}) {
  const params = useParams<{ matchId: string }>();
  const matchId = matchIdOverride ?? params.matchId;
  const [data, setData] = useState<Postmatch | null>(null);
  const [viewerId, setViewerId] = useState('');
  const [viewerRole, setViewerRole] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reloadNonce, setReloadNonce] = useState(0);
  const { showToast } = useToast();
  const { isPending: saving, run: runTranscriptWrite } = useSingleFlight();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [judgeDraft, setJudgeDraft] = useState<JudgeDraft | null>(null);
  const [judgeSaving, setJudgeSaving] = useState(false);
  const [judgeRetrying, setJudgeRetrying] = useState(false);
  const judgeSavingRef = useRef(false);
  const [metadataSaving, setMetadataSaving] = useState(false);
  const [metadataLabel, setMetadataLabel] = useState('');
  const [metadataTopic, setMetadataTopic] = useState('');
  const [metadataNote, setMetadataNote] = useState('');
  const postmatchStatus = data?.status;
  const judgeStatus = data?.judge?.status;

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<Postmatch>(`/api/matches/${matchId}/postmatch`),
      authApi.currentUser(),
    ])
      .then(([postmatch, auth]) => {
        if (!active) return;
        setData(postmatch);
        setViewerId(String(auth.user.id));
        setViewerRole(auth.user.role);
        setMetadataLabel(postmatch.label);
        setMetadataTopic(postmatch.display_topic);
        setMetadataNote(postmatch.admin_note ?? '');
        if (postmatch.judge?.result) setJudgeDraft(toJudgeDraft(postmatch.judge.result));
      })
      .catch((loadError: unknown) => {
        if (!active) return;
        const message = errorMessage(loadError, '无法加载赛后记录');
        setError(message);
        showToast({ message, tone: 'error' });
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [matchId, reloadNonce, showToast]);

  useEffect(() => {
    if (postmatchStatus !== 'FINISHED' || judgeStatus === 'SUCCEEDED' || judgeStatus === 'FAILED')
      return;
    let active = true;
    const startedAt = Date.now();
    const poll = async () => {
      if (Date.now() - startedAt >= 120_000) return;
      try {
        const latest = await requestJson<Postmatch>(`/api/matches/${matchId}/postmatch`);
        if (active) setData(latest);
      } catch {
        // The existing snapshot remains visible; the next interval may recover.
      }
    };
    const timer = window.setInterval(() => void poll(), 2_000);
    const deadline = window.setTimeout(() => window.clearInterval(timer), 120_000);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.clearTimeout(deadline);
    };
  }, [judgeStatus, matchId, postmatchStatus]);

  const participantBySeat = useMemo(
    () =>
      new Map(
        (data?.participants ?? []).map((participant) => [
          `${participant.side}:${participant.seat_no}`,
          participant,
        ]),
      ),
    [data?.participants],
  );
  const participantById = useMemo(
    () => new Map((data?.participants ?? []).map((participant) => [participant.id, participant])),
    [data?.participants],
  );

  if (error)
    return (
      <main className="jx-page-viewport grid place-items-center bg-[#f7faff] px-6 text-slate-700">
        <div className="max-w-md rounded-2xl border border-amber-200 bg-white px-6 py-5 text-center shadow-sm">
          <p>赛后记录暂时无法加载，请稍后重试。</p>
          <button
            className={buttonVariants({ variant: 'primary', size: 'sm' }) + ' mt-4'}
            onClick={() => {
              setError('');
              setLoading(true);
              setReloadNonce((value) => value + 1);
            }}
            type="button"
          >
            重新加载
          </button>
          <Link
            className={buttonVariants({ variant: 'secondary', size: 'sm' }) + ' mt-4'}
            href="/lobby"
          >
            <ArrowLeft className="size-4" /> 返回大厅
          </Link>
        </div>
      </main>
    );
  if (loading || !data)
    return (
      <main className="jx-page-viewport grid place-items-center bg-[#f7faff] text-slate-500">
        正在加载赛后记录…
      </main>
    );

  const ownSpeeches = data.speeches.filter(
    (speech) => speech.user_id === viewerId && speech.speaker_kind === 'HUMAN',
  );
  const canSubmit = data.status === 'FINISHED' && ownSpeeches.length > 0;
  const hasSubmitted = data.submissions.some((submission) => submission.user_id === viewerId);
  const replay = data.files.find((file) => file.file_kind === 'MATCH_REPLAY');
  const rawFiles = data.files.filter((file) => file.file_kind !== 'MATCH_REPLAY');
  const replayMessage = replayStatusText(replay, data.speeches.length);

  async function saveSpeech(speechId: string) {
    const displayText = draft.trim();
    const ownerId = data?.speeches.find((speech) => speech.id === speechId)?.user_id;
    if (!displayText) {
      showToast({ message: '文字不能为空', tone: 'error' });
      return;
    }
    try {
      const result = await runTranscriptWrite(() =>
        matchesApi.updateDisplayText(matchId, speechId, displayText),
      );
      if (!result.started) return;
      const transcript = result.value;
      setData((current) =>
        current
          ? {
              ...current,
              context_version: transcript.context_version,
              submissions: current.submissions.filter(
                (submission) => submission.user_id !== (ownerId ?? viewerId),
              ),
              speeches: current.speeches.map((speech) =>
                speech.id === speechId ? { ...speech, display_text: displayText } : speech,
              ),
            }
          : current,
      );
      setEditingId(null);
      showToast({ message: '修改已保存，之后的上下文已切换到最新文字', tone: 'success' });
    } catch (saveError: unknown) {
      showToast({ message: errorMessage(saveError, '保存失败，请稍后重试'), tone: 'error' });
    }
  }

  async function submitOwnTranscript() {
    try {
      const result = await runTranscriptWrite(() =>
        requestJson<Postmatch>(`/api/matches/${matchId}/transcripts/submit`, {
          method: 'POST',
          body: '{}',
        }),
      );
      if (!result.started) return;
      setData(result.value);
      showToast({ message: '你的文字已经提交', tone: 'success' });
    } catch (submitError: unknown) {
      showToast({
        message: errorMessage(submitError, '提交失败，请稍后重试'),
        tone: 'error',
      });
    }
  }

  return (
    <main className="jx-page-viewport bg-[#f7faff] px-6 py-10 text-slate-950">
      <div className="mx-auto max-w-6xl">
        <div className="mb-6">
          <Link className={buttonVariants({ variant: 'ghost', size: 'sm' })} href="/lobby">
            <ArrowLeft className="size-4" /> 返回大厅
          </Link>
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-black tracking-[0.18em] text-blue-600">POST-MATCH REVIEW</p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <h1 className="text-4xl font-black tracking-tight">{data.title}</h1>
              <span className="rounded-full bg-blue-100 px-3 py-1 text-xs font-black text-blue-700">
                {data.label}
              </span>
            </div>
            <p className="mt-2 text-base font-bold text-slate-700">{data.display_topic}</p>
            <p className="mt-2 text-sm text-slate-500">
              上下文版本 {data.context_version} ·{' '}
              {viewerRole === 'ADMIN'
                ? '管理员可修正已完成的正式文字'
                : '你只能修改本人已经完成的发言'}
            </p>
          </div>
          {canSubmit && (
            <button
              disabled={saving || hasSubmitted || editingId !== null}
              onClick={() => void submitOwnTranscript()}
              className="jx-disabled-command rounded-xl border border-slate-950 bg-slate-950 px-5 py-3 text-sm font-bold text-white transition hover:border-blue-700 hover:bg-blue-700"
            >
              {hasSubmitted
                ? '我的文字已提交'
                : saving
                  ? '处理中…'
                  : editingId !== null
                    ? '请先保存或取消编辑'
                    : '确认并提交我的文字'}
            </button>
          )}
          <a
            href={`/api/matches/${matchId}/downloads/transcript`}
            className="rounded-xl border border-slate-200 bg-white px-5 py-3 text-sm font-bold text-slate-700 transition hover:border-blue-300 hover:text-blue-700"
          >
            下载文字与评分
          </a>
        </div>
        {viewerRole === 'ADMIN' && (
          <section className="mt-5 rounded-3xl border border-violet-100 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-black tracking-[0.16em] text-violet-600">
                  MATCH METADATA
                </p>
                <h2 className="mt-1 text-lg font-black">管理员展示信息</h2>
              </div>
              <span className="text-xs text-slate-500">不修改题库、原始记录和实际时间</span>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-[12rem_1fr]">
              <label className="text-xs font-bold text-slate-600">
                比赛标签
                <input
                  value={metadataLabel}
                  onChange={(event) => setMetadataLabel(event.target.value)}
                  maxLength={32}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm"
                />
              </label>
              <label className="text-xs font-bold text-slate-600">
                展示辩题
                <input
                  value={metadataTopic}
                  onChange={(event) => setMetadataTopic(event.target.value)}
                  maxLength={500}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm"
                />
              </label>
            </div>
            <label className="mt-3 block text-xs font-bold text-slate-600">
              管理员备注（普通用户不可见）
              <textarea
                value={metadataNote}
                onChange={(event) => setMetadataNote(event.target.value)}
                maxLength={2000}
                className="mt-1 min-h-20 w-full rounded-xl border border-slate-200 p-3 text-sm"
              />
            </label>
            <button
              disabled={metadataSaving || !metadataLabel.trim() || !metadataTopic.trim()}
              onClick={() => {
                setMetadataSaving(true);
                void requestJson(`/api/admin/matches/${matchId}/metadata`, {
                  method: 'PATCH',
                  body: JSON.stringify({
                    label: metadataLabel.trim(),
                    display_topic: metadataTopic.trim(),
                    admin_note: metadataNote.trim(),
                  }),
                })
                  .then(() => {
                    setData((current) =>
                      current
                        ? {
                            ...current,
                            label: metadataLabel.trim(),
                            display_topic: metadataTopic.trim(),
                            admin_note: metadataNote.trim(),
                          }
                        : current,
                    );
                    showToast({ message: '比赛标签、展示辩题和备注已保存', tone: 'success' });
                  })
                  .catch((saveError: unknown) =>
                    showToast({
                      message: errorMessage(saveError, '比赛展示信息保存失败'),
                      tone: 'error',
                    }),
                  )
                  .finally(() => setMetadataSaving(false));
              }}
              className="jx-disabled-command mt-4 rounded-xl border border-violet-600 bg-violet-600 px-4 py-2.5 text-sm font-bold text-white"
            >
              {metadataSaving ? '保存中…' : '保存展示信息'}
            </button>
          </section>
        )}

        <section className="mt-8 grid items-start gap-5 lg:grid-cols-[1.35fr_0.65fr]">
          <div className="space-y-5">
            <div className="rounded-3xl border border-blue-100 bg-white p-6 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-black">完整文字记录</h2>
                <span className="text-xs text-slate-500">共 {data.speeches.length} 次发言</span>
              </div>
              <div className="mt-5 space-y-4">
                {data.speeches.map((speech) => {
                  const participant = participantBySeat.get(`${speech.side}:${speech.seat_no}`);
                  const editable =
                    (viewerRole === 'ADMIN' ||
                      (speech.user_id === viewerId &&
                        speech.speaker_kind === 'HUMAN' &&
                        speech.asr_raw_final_text !== null)) &&
                    (data.status === 'FINISHED' ||
                      (viewerRole === 'ADMIN' && data.status === 'TERMINATED'));
                  const isEditing = editingId === speech.id;
                  return (
                    <article key={speech.id} className="rounded-2xl bg-slate-50 p-4">
                      <div className="flex items-center justify-between gap-4">
                        <p className="text-xs font-bold text-slate-500">
                          {sideName(speech.side)} ·{' '}
                          {participant?.display_name ?? `${speech.seat_no} 辩`}
                          {' · '}
                          {speech.speaker_kind === 'AGENT' ? 'Agent' : '人类'}
                        </p>
                        {editable && !isEditing && (
                          <button
                            onClick={() => {
                              setEditingId(speech.id);
                              setDraft(speech.display_text ?? '');
                            }}
                            className="rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-bold text-blue-700 transition hover:bg-blue-50"
                          >
                            {viewerRole === 'ADMIN' && speech.user_id !== viewerId
                              ? '管理员修正'
                              : '修改我的文字'}
                          </button>
                        )}
                      </div>
                      {isEditing ? (
                        <div className="mt-3">
                          <textarea
                            value={draft}
                            onChange={(event) => setDraft(event.target.value)}
                            maxLength={20_000}
                            className="min-h-36 w-full rounded-xl border border-blue-200 bg-white p-3 leading-7 outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                            aria-label="修改本人发言文字"
                          />
                          <div className="mt-3 flex items-center justify-between gap-3">
                            <span className="text-xs text-slate-500">{draft.length} / 20000</span>
                            <div className="flex gap-2">
                              <button
                                disabled={saving}
                                onClick={() => setEditingId(null)}
                                className="rounded-lg px-3 py-2 text-xs font-bold text-slate-500"
                              >
                                取消
                              </button>
                              <button
                                disabled={saving || !draft.trim()}
                                onClick={() => void saveSpeech(speech.id)}
                                className="jx-disabled-command rounded-lg border border-blue-600 bg-blue-600 px-4 py-2 text-xs font-bold text-white"
                              >
                                {saving ? '保存中…' : '保存修改'}
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <p className="mt-2 whitespace-pre-wrap leading-7 text-slate-800">
                          {speech.display_text || '（无文字）'}
                        </p>
                      )}
                    </article>
                  );
                })}
              </div>
            </div>
            <AudioArchive replay={replay} rawFiles={rawFiles} replayMessage={replayMessage} />
          </div>

          <aside className="rounded-3xl border border-blue-100 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-black">AI 裁判</h2>
            {!data.judge ? (
              <p className="mt-5 text-sm text-slate-500">{judgeStatusText(data.status, false)}</p>
            ) : data.judge.status !== 'SUCCEEDED' ? (
              <div className="mt-5 rounded-xl bg-amber-50 p-4 text-sm text-amber-700">
                <p>
                  {data.judge.status === 'FAILED'
                    ? judgeFailureText(data.judge.error_code)
                    : 'AI 裁判正在评分…'}
                </p>
                {data.judge.status === 'FAILED' && data.can_retry_judge && (
                  <button
                    type="button"
                    disabled={judgeRetrying}
                    className="jx-disabled-command mt-3 rounded-lg border border-amber-700 bg-amber-700 px-3 py-2 text-xs font-bold text-white transition hover:bg-amber-800"
                    onClick={() => {
                      if (judgeRetrying) return;
                      setJudgeRetrying(true);
                      void requestJson<Postmatch>(`/api/matches/${matchId}/judge/retry`, {
                        method: 'POST',
                        body: '{}',
                      })
                        .then((latest) => {
                          setData(latest);
                          showToast({ message: '已重新开始 AI 评分', tone: 'success' });
                        })
                        .catch((retryError: unknown) =>
                          showToast({
                            message: errorMessage(retryError, '重新评分失败，请稍后重试'),
                            tone: 'error',
                          }),
                        )
                        .finally(() => setJudgeRetrying(false));
                    }}
                  >
                    {judgeRetrying ? '重新评分中…' : '重新评分'}
                  </button>
                )}
              </div>
            ) : (
              <>
                <p className="mt-5 text-3xl font-black text-lime-700">
                  {data.judge.result?.winner === 'DRAW'
                    ? '平局'
                    : data.judge.result?.winner === 'AFFIRMATIVE'
                      ? '正方获胜'
                      : '反方获胜'}
                </p>
                <div className="mt-5 space-y-4">
                  {Object.entries(data.judge.result?.team_scores || {}).map(([side, scores]) => (
                    <div key={side} className="rounded-2xl bg-slate-50 p-4">
                      <div className="flex items-center justify-between">
                        <p className="font-black">{sideName(side)}</p>
                        <p className="text-lg font-black text-blue-700">
                          {Object.values(scores).reduce((total, score) => total + score, 0)} / 100
                        </p>
                      </div>
                      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                        {Object.entries(scores).map(([dimension, score]) => (
                          <div
                            key={dimension}
                            className="flex justify-between gap-2 text-slate-600"
                          >
                            <dt>{dimensionNames[dimension] ?? dimension}</dt>
                            <dd className="font-bold text-slate-900">{score}</dd>
                          </div>
                        ))}
                      </dl>
                      {data.judge?.result?.team_comments?.[side] && (
                        <p className="mt-3 border-t border-slate-200 pt-3 text-sm leading-6 text-slate-600">
                          {data.judge.result.team_comments[side]}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
                {!!data.judge.result?.participants?.length && (
                  <div className="mt-6 border-t border-slate-100 pt-5">
                    <h3 className="text-sm font-black">个人评分</h3>
                    <div className="mt-3 space-y-3">
                      {data.judge.result.participants.map((score) => {
                        const participant = participantById.get(score.participant_id);
                        return (
                          <div key={score.participant_id} className="rounded-xl bg-slate-50 p-3">
                            <div className="flex items-center justify-between gap-3">
                              <span className="text-sm font-bold">
                                {participant?.display_name ?? '参赛辩手'}
                              </span>
                              <span className="font-black text-blue-700">{score.score} / 20</span>
                            </div>
                            {score.comment && (
                              <p className="mt-2 text-xs leading-5 text-slate-500">
                                {score.comment}
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {viewerRole === 'ADMIN' && judgeDraft && (
                  <div className="mt-6 border-t border-slate-100 pt-5">
                    <h3 className="text-sm font-black">管理员修正裁判结果</h3>
                    <label className="mt-3 block text-xs font-bold text-slate-600">
                      获胜方
                      <select
                        value={judgeDraft.winner}
                        onChange={(event) =>
                          setJudgeDraft({
                            ...judgeDraft,
                            winner: event.target.value as JudgeDraft['winner'],
                          })
                        }
                        className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2"
                      >
                        <option value="AFFIRMATIVE">正方</option>
                        <option value="NEGATIVE">反方</option>
                        <option value="DRAW">平局</option>
                      </select>
                    </label>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      {(['AFFIRMATIVE', 'NEGATIVE'] as const).map((side) => (
                        <div key={side} className="rounded-xl bg-slate-50 p-3">
                          <p className="text-xs font-black">{sideName(side)}</p>
                          <div className="mt-2 grid grid-cols-2 gap-2">
                            {judgeDimensions.map((dimension) => (
                              <label key={dimension} className="text-[11px] text-slate-500">
                                {dimensionNames[dimension]}
                                <input
                                  type="number"
                                  min={0}
                                  max={
                                    dimension === 'argument'
                                      ? 30
                                      : dimension === 'rebuttal'
                                        ? 25
                                        : dimension === 'evidence'
                                          ? 20
                                          : dimension === 'teamwork'
                                            ? 15
                                            : 10
                                  }
                                  value={judgeDraft.team_scores[side]?.[dimension] ?? 0}
                                  onChange={(event) =>
                                    setJudgeDraft({
                                      ...judgeDraft,
                                      team_scores: {
                                        ...judgeDraft.team_scores,
                                        [side]: {
                                          ...judgeDraft.team_scores[side],
                                          [dimension]: Number(event.target.value),
                                        },
                                      },
                                    })
                                  }
                                  className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs"
                                />
                              </label>
                            ))}
                          </div>
                          <textarea
                            value={judgeDraft.team_comments[side] ?? ''}
                            onChange={(event) =>
                              setJudgeDraft({
                                ...judgeDraft,
                                team_comments: {
                                  ...judgeDraft.team_comments,
                                  [side]: event.target.value,
                                },
                              })
                            }
                            className="mt-2 min-h-16 w-full rounded-lg border border-slate-200 bg-white p-2 text-xs"
                            placeholder={`${sideName(side)}简评`}
                          />
                        </div>
                      ))}
                    </div>
                    <div className="mt-3 space-y-2">
                      {judgeDraft.participants.map((participant, index) => (
                        <label
                          key={participant.participant_id}
                          className="flex items-center justify-between gap-3 text-xs"
                        >
                          <span>
                            {participantById.get(participant.participant_id)?.display_name ??
                              '参赛辩手'}
                          </span>
                          <input
                            type="number"
                            min={0}
                            max={20}
                            value={participant.score}
                            onChange={(event) =>
                              setJudgeDraft({
                                ...judgeDraft,
                                participants: judgeDraft.participants.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, score: Number(event.target.value) }
                                    : item,
                                ),
                              })
                            }
                            className="w-20 rounded-lg border border-slate-200 px-2 py-1.5 text-right"
                          />
                        </label>
                      ))}
                    </div>
                    <button
                      disabled={judgeSaving}
                      onClick={() => {
                        if (judgeSavingRef.current) return;
                        judgeSavingRef.current = true;
                        setJudgeSaving(true);
                        void commitJudgeResult(
                          () =>
                            requestJson(`/api/admin/matches/${matchId}/judge-result`, {
                              method: 'PATCH',
                              body: JSON.stringify(judgeDraft),
                            }),
                          () => requestJson<Postmatch>(`/api/matches/${matchId}/postmatch`),
                        )
                          .then((result) => {
                            setData((current) =>
                              current
                                ? {
                                    ...current,
                                    judge: {
                                      ...(current.judge ?? { error_code: null }),
                                      status: 'SUCCEEDED',
                                      result: judgeDraft,
                                      error_code: null,
                                    },
                                  }
                                : current,
                            );
                            if (result.refreshed) setData(result.data);
                            showToast({
                              message: result.refreshed
                                ? '裁判结果已修正，排行榜重算任务已排队'
                                : '裁判结果已保存，但页面刷新失败；请刷新页面查看最新结果',
                              tone: result.refreshed ? 'success' : 'info',
                            });
                          })
                          .catch((saveError: unknown) =>
                            showToast({
                              message: errorMessage(
                                saveError,
                                '裁判结果保存失败，请检查每方总分是否符合规则',
                              ),
                              tone: 'error',
                            }),
                          )
                          .finally(() => {
                            judgeSavingRef.current = false;
                            setJudgeSaving(false);
                          });
                      }}
                      className="jx-disabled-command mt-4 rounded-lg border border-violet-600 bg-violet-600 px-4 py-2 text-xs font-bold text-white"
                    >
                      {judgeSaving ? '保存中…' : '保存裁判修正'}
                    </button>
                  </div>
                )}
              </>
            )}
          </aside>
        </section>
      </div>
    </main>
  );
}
