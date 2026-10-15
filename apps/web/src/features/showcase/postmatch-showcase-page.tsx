'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast-provider';
import { ApiClientError } from '@/lib/auth-api';
import { showcaseApi, type Showcase } from '@/lib/showcase-api';

type Mode = 'judges' | 'vote' | 'awards' | 'admin';

function errorText(error: unknown) {
  return error instanceof ApiClientError ? error.message : '提交失败，请刷新后重试。';
}

function sideLabel(side: string) {
  return side === 'AFFIRMATIVE' ? '正方' : side === 'NEGATIVE' ? '反方' : '平局';
}

function Shell({
  matchId,
  title,
  children,
}: Readonly<{ matchId: string; title: string; children: ReactNode }>) {
  return (
    <main className="jx-page-grid min-h-[calc(100dvh-3.85rem)] px-4 py-6 sm:px-8">
      <div className="mx-auto w-full max-w-5xl">
        <header className="rounded-[1.5rem] border border-blue-100 bg-white/90 px-5 py-5 shadow-[0_18px_55px_rgba(42,83,130,0.09)] sm:px-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[10px] font-black tracking-[0.2em] text-blue-600">
                POSTMATCH SHOWCASE
              </p>
              <h1 className="mt-2 text-2xl font-black tracking-[-0.04em] text-slate-950 sm:text-3xl">
                {title}
              </h1>
              <p className="mt-2 text-sm text-slate-600">
                比赛结束后的文字展示页，不包含比赛计时。
              </p>
            </div>
            <Link
              className="text-sm font-bold text-blue-700 hover:text-blue-900"
              href={`/matches/${matchId}`}
            >
              返回赛后记录
            </Link>
          </div>
          <nav className="mt-5 flex flex-wrap gap-2 text-xs font-bold">
            {[
              ['judges', '评委点评'],
              ['vote', '观众投票'],
              ['awards', '颁奖展示'],
              ['admin', '后台控制'],
            ].map(([key, label]) => (
              <Link
                key={key}
                className="rounded-full border border-blue-100 bg-blue-50 px-3 py-1.5 text-blue-800"
                href={`/matches/${matchId}/showcase/${key}`}
              >
                {label}
              </Link>
            ))}
          </nav>
        </header>
        {children}
      </div>
    </main>
  );
}

function useShowcase(matchId: string) {
  return useQuery({
    queryKey: ['matches', matchId, 'showcase'],
    queryFn: () => showcaseApi.get(matchId),
    refetchInterval: 2_000,
  });
}

function LoadingOrError({ loading, error }: Readonly<{ loading: boolean; error: unknown }>) {
  if (loading)
    return (
      <p className="mt-6 rounded-2xl border border-blue-100 bg-white/80 p-6 text-sm font-bold text-slate-600">
        正在读取赛后展示…
      </p>
    );
  if (error)
    return (
      <p className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-6 text-sm font-bold text-red-700">
        {errorText(error)}
      </p>
    );
  return null;
}

function JudgeView({ matchId, data }: Readonly<{ matchId: string; data: Showcase }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [comments, setComments] = useState(data.judge?.comments ?? '');
  const [winner, setWinner] = useState(data.judge?.winner_side ?? 'AFFIRMATIVE');
  const [best, setBest] = useState(
    data.judge?.best_participant_id ?? data.participants[0]?.id ?? '',
  );
  const mutation = useMutation({
    mutationFn: (published: boolean) =>
      showcaseApi.judge(matchId, {
        comments,
        winner_side: winner,
        best_participant_id: best,
        published,
      }),
    onSuccess: (next) => {
      queryClient.setQueryData(['matches', matchId, 'showcase'], next);
      showToast({ message: '评委结果已保存。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const canEdit = data.can_manage;
  return (
    <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_0.8fr]">
      <div className="rounded-[1.5rem] border border-blue-100 bg-white/90 p-5 shadow-sm sm:p-7">
        <h2 className="text-lg font-black">评委点评</h2>
        {canEdit ? (
          <textarea
            className="mt-4 min-h-48 w-full rounded-xl border border-slate-200 p-3 text-sm outline-none focus:border-blue-500"
            value={comments}
            onChange={(event) => setComments(event.target.value)}
            placeholder="记录本场比赛的核心点评…"
          />
        ) : (
          <p className="mt-4 whitespace-pre-wrap rounded-xl bg-slate-50 p-4 text-sm leading-7 text-slate-700">
            {data.judge?.comments || '评委点评尚未发布。'}
          </p>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-sm font-bold text-slate-700">
            最终胜方
            <select
              className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2"
              disabled={!canEdit}
              value={winner}
              onChange={(event) => setWinner(event.target.value)}
            >
              <option value="AFFIRMATIVE">正方</option>
              <option value="NEGATIVE">反方</option>
              <option value="DRAW">平局</option>
            </select>
          </label>
          <label className="text-sm font-bold text-slate-700">
            最佳辩手
            <select
              className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2"
              disabled={!canEdit}
              value={best}
              onChange={(event) => setBest(event.target.value)}
            >
              {data.participants.map((participant) => (
                <option key={participant.id} value={participant.id}>
                  {participant.display_name} · {sideLabel(participant.side)} {participant.seat_no}
                </option>
              ))}
            </select>
          </label>
        </div>
        {canEdit ? (
          <div className="mt-5 flex flex-wrap gap-2">
            <Button
              disabled={mutation.isPending || !best}
              onClick={() => mutation.mutate(false)}
              variant="secondary"
            >
              保存草稿
            </Button>
            <Button
              disabled={mutation.isPending || !best}
              onClick={() => mutation.mutate(true)}
              variant="primary"
            >
              保存并发布
            </Button>
          </div>
        ) : null}
      </div>
      <ParticipantList participants={data.participants} />
    </section>
  );
}

function ParticipantList({ participants }: Readonly<{ participants: Showcase['participants'] }>) {
  return (
    <div className="rounded-[1.5rem] border border-blue-100 bg-white/90 p-5 shadow-sm sm:p-7">
      <h2 className="text-lg font-black">本场八位辩手</h2>
      <ol className="mt-4 grid gap-2">
        {participants.map((participant) => (
          <li
            key={participant.id}
            className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm"
          >
            <span className="font-bold text-slate-900">{participant.display_name}</span>
            <span className="text-xs font-bold text-slate-500">
              {sideLabel(participant.side)} · {participant.seat_no}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function VoteView({ matchId, data }: Readonly<{ matchId: string; data: Showcase }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [winner, setWinner] = useState('AFFIRMATIVE');
  const [ranking, setRanking] = useState(() =>
    data.participants.map((participant) => participant.id),
  );
  const mutation = useMutation({
    mutationFn: () => showcaseApi.vote(matchId, { winner_side: winner, ranking }),
    onSuccess: (next) => {
      queryClient.setQueryData(['matches', matchId, 'showcase'], next);
      showToast({ message: '感谢投票，本场每个账号只能提交一次。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const updateRank = (index: number, value: string) =>
    setRanking((current) => {
      const next = [...current];
      const other = next.indexOf(value);
      if (other >= 0) [next[index], next[other]] = [next[other], next[index]];
      return next;
    });
  const closed = data.phase !== 'AUDIENCE_VOTE' || !data.published || data.has_voted;
  return (
    <section className="mt-6 rounded-[1.5rem] border border-blue-100 bg-white/90 p-5 shadow-sm sm:p-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-black">观众投票</h2>
          <p className="mt-1 text-sm text-slate-600">选择胜方，并为八位辩手排出完整名次。</p>
        </div>
        <span className="rounded-full bg-blue-50 px-3 py-1.5 text-xs font-black text-blue-800">
          {data.has_voted
            ? '已投票'
            : data.phase === 'AUDIENCE_VOTE' && data.published
              ? '投票进行中'
              : '尚未开放'}
        </span>
      </div>
      <fieldset className="mt-5 flex flex-wrap gap-2" disabled={closed}>
        <legend className="sr-only">胜方</legend>
        {['AFFIRMATIVE', 'NEGATIVE', 'DRAW'].map((side) => (
          <label
            key={side}
            className={`cursor-pointer rounded-xl border px-4 py-2 text-sm font-black ${winner === side ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-slate-200'}`}
          >
            <input
              className="sr-only"
              type="radio"
              checked={winner === side}
              onChange={() => setWinner(side)}
            />
            {sideLabel(side)}
          </label>
        ))}
      </fieldset>
      <div className="mt-5 grid gap-2">
        {ranking.map((participantId, index) => {
          const participant = data.participants.find((candidate) => candidate.id === participantId);
          return (
            <label
              key={participantId}
              className="flex items-center gap-3 rounded-xl border border-slate-100 bg-slate-50 p-3 text-sm"
            >
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-white text-xs font-black text-blue-700">
                {index + 1}
              </span>
              <select
                aria-label={`第 ${index + 1} 名`}
                className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white p-2 font-bold"
                disabled={closed}
                value={participantId}
                onChange={(event) => updateRank(index, event.target.value)}
              >
                {data.participants.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.display_name} · {sideLabel(option.side)} {option.seat_no}
                  </option>
                ))}
              </select>
              {participant ? (
                <span className="hidden text-xs text-slate-500 sm:inline">
                  {participant.kind === 'AGENT' ? 'Agent' : '真人'}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
      <Button
        className="mt-5 w-full sm:w-auto"
        disabled={closed || mutation.isPending || ranking.length !== 8}
        onClick={() => mutation.mutate()}
        variant="primary"
      >
        提交本场投票
      </Button>
    </section>
  );
}

function AwardsView({ data }: Readonly<{ data: Showcase }>) {
  const top = data.audience.ranking.slice(0, 3);
  return (
    <section className="mt-6 grid gap-5">
      <div className="rounded-[1.5rem] border border-blue-100 bg-white/90 p-5 shadow-sm sm:p-7">
        <p className="text-xs font-black tracking-[0.18em] text-blue-600">AUDIENCE AWARDS</p>
        <h2 className="mt-2 text-2xl font-black">观众票选颁奖</h2>
        <p className="mt-2 text-sm text-slate-600">
          共 {data.audience.total_votes} 份有效投票 · 胜方票数：正方{' '}
          {data.audience.winner_counts.AFFIRMATIVE ?? 0} / 反方{' '}
          {data.audience.winner_counts.NEGATIVE ?? 0} / 平局 {data.audience.winner_counts.DRAW ?? 0}
        </p>
        <div className="mt-6 grid gap-3 md:grid-cols-3">
          {top.map((entry) => (
            <div
              key={entry.participant_id}
              className={`rounded-2xl border p-5 ${entry.rank === 1 ? 'border-amber-300 bg-amber-50' : 'border-blue-100 bg-blue-50/60'}`}
            >
              <span className="text-xs font-black text-slate-500">第 {entry.rank} 名</span>
              <h3 className="mt-2 text-lg font-black">{entry.display_name}</h3>
              <p className="mt-1 text-sm font-bold text-slate-600">
                {sideLabel(entry.side)} · {entry.points} 分
              </p>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-[1.5rem] border border-blue-100 bg-white/90 p-5 shadow-sm sm:p-7">
        <h2 className="text-lg font-black">完整排名</h2>
        <ol className="mt-4 grid gap-2">
          {data.audience.ranking.map((entry) => (
            <li
              key={entry.participant_id}
              className="flex items-center justify-between rounded-xl border border-slate-100 px-3 py-3 text-sm"
            >
              <span className="flex items-center gap-3">
                <b className="grid size-7 place-items-center rounded-full bg-slate-100 text-xs">
                  {entry.rank}
                </b>
                <span className="font-bold">{entry.display_name}</span>
              </span>
              <span className="font-black text-blue-700">{entry.points} 分</span>
            </li>
          ))}
        </ol>
      </div>
      {data.judge ? (
        <div className="rounded-[1.5rem] border border-lime-200 bg-lime-50/70 p-5">
          <h2 className="text-lg font-black">评委结果</h2>
          <p className="mt-2 text-sm font-bold">
            胜方：{sideLabel(data.judge.winner_side)} · 最佳辩手：
            {data.participants.find(
              (participant) => participant.id === data.judge?.best_participant_id,
            )?.display_name ?? '—'}
          </p>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-700">
            {data.judge.comments}
          </p>
        </div>
      ) : null}
    </section>
  );
}

function AdminView({ matchId, data }: Readonly<{ matchId: string; data: Showcase }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const phaseMutation = useMutation({
    mutationFn: (phase: Showcase['phase']) =>
      showcaseApi.phase(matchId, { phase, published: true }),
    onSuccess: (next) => {
      queryClient.setQueryData(['matches', matchId, 'showcase'], next);
      showToast({ message: '展示阶段已切换。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  return (
    <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_1.2fr]">
      <div className="rounded-[1.5rem] border border-amber-200 bg-amber-50/70 p-5 shadow-sm sm:p-7">
        <p className="text-xs font-black tracking-[0.18em] text-amber-700">HOST WORKBENCH</p>
        <h2 className="mt-2 text-xl font-black">赛后展示控制</h2>
        <p className="mt-2 text-sm leading-6 text-slate-700">
          当前阶段：<b>{data.phase}</b> · {data.published ? '已发布' : '草稿'}
        </p>
        <div className="mt-5 grid gap-2">
          {(['JUDGE_REVIEW', 'AUDIENCE_VOTE', 'AUDIENCE_RESULTS', 'AWARDS', 'CLOSED'] as const).map(
            (phase) => (
              <Button
                key={phase}
                disabled={phaseMutation.isPending}
                onClick={() => phaseMutation.mutate(phase)}
                variant={data.phase === phase ? 'primary' : 'secondary'}
              >
                {phase}
              </Button>
            ),
          )}
        </div>
      </div>
      <div>
        <JudgeView matchId={matchId} data={data} />
        <div className="mt-5">
          <AwardsView data={data} />
        </div>
      </div>
    </section>
  );
}

export function PostmatchShowcasePage({ mode }: Readonly<{ mode: Mode }>) {
  const params = useParams<{ matchId: string }>();
  const matchId = params.matchId;
  const query = useShowcase(matchId);
  const title =
    mode === 'judges'
      ? '评委点评'
      : mode === 'vote'
        ? '观众投票'
        : mode === 'awards'
          ? '颁奖展示'
          : '赛后后台';
  return (
    <Shell matchId={matchId} title={title}>
      <LoadingOrError error={query.error} loading={query.isPending} />
      {query.data ? (
        mode === 'judges' ? (
          <JudgeView matchId={matchId} data={query.data} />
        ) : mode === 'vote' ? (
          <VoteView matchId={matchId} data={query.data} />
        ) : mode === 'awards' ? (
          <AwardsView data={query.data} />
        ) : (
          <AdminView matchId={matchId} data={query.data} />
        )
      ) : null}
    </Shell>
  );
}
