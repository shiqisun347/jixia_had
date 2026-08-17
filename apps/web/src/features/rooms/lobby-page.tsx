'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CircleDot,
  LoaderCircle,
  Plus,
  Radio,
  TicketCheck,
  UsersRound,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { type FormEvent, useEffect, useRef, useState } from 'react';

import { useToast } from '@/components/ui/toast-provider';
import { ApiClientError } from '@/lib/auth-api';
import { roomsApi, type LobbyRoom } from '@/lib/rooms-api';

import { resolveRoomEntry } from './room-entry';

const lobbyQueryKey = ['rooms', 'lobby'] as const;

export function LobbySyncStatus({
  isFetching,
  isError,
  onRetry,
}: Readonly<{ isFetching: boolean; isError: boolean; onRetry: () => void }>) {
  if (isError && !isFetching) {
    return (
      <button
        className="inline-flex min-h-9 items-center gap-2 rounded-full border border-amber-300 bg-amber-50 px-3.5 py-2 text-xs font-black text-amber-800 transition hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
        onClick={onRetry}
        type="button"
      >
        <AlertTriangle className="size-4" aria-hidden="true" />
        同步中断 · 重新同步
      </button>
    );
  }
  return (
    <div className="flex min-h-9 items-center gap-2 rounded-full border border-lime-200 bg-lime-50 px-3.5 py-2 text-xs font-bold text-lime-800">
      {isFetching ? (
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
      ) : (
        <Radio className="size-4" aria-hidden="true" />
      )}
      {isFetching ? '正在同步' : '已同步'}
    </div>
  );
}

function StatusLabel({ status }: Readonly<{ status: string }>) {
  const running = status === 'RUNNING';
  const paused = status === 'PAUSED';
  const starting = status === 'START_PENDING_RUNTIME';
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-black tracking-[0.08em] ${
        running
          ? 'bg-lime-100 text-lime-800'
          : paused
            ? 'bg-amber-100 text-amber-800'
            : 'bg-slate-100 text-slate-600'
      }`}
    >
      <CircleDot className="size-3" aria-hidden="true" />
      {running ? '进行中' : paused ? '已暂停' : starting ? '正在开赛' : '准备中'}
    </span>
  );
}

function RoomCard({ room }: Readonly<{ room: LobbyRoom }>) {
  const entry = resolveRoomEntry(room);
  return (
    <article className="group relative overflow-hidden rounded-[1.5rem] border border-blue-100/90 bg-white/90 p-5 shadow-[0_18px_52px_rgba(40,76,142,0.09)] transition duration-200 motion-safe:hover:-translate-y-1">
      <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-red-400 via-lime-300 to-blue-500" />
      <div className="flex items-start justify-between gap-3">
        <StatusLabel status={room.status} />
        <span className="font-mono text-xs font-bold tracking-[0.18em] text-slate-500">
          {room.code}
        </span>
      </div>
      <h2 className="mt-5 line-clamp-1 text-lg font-black tracking-[-0.03em] text-slate-950">
        {room.title}
      </h2>
      <p className="mt-2 line-clamp-2 min-h-12 text-sm leading-6 text-slate-600">
        {room.topic_title}
      </p>
      <div className="mt-5 flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-700">
        <span className="rounded-lg bg-blue-50 px-2.5 py-1.5 text-blue-700">{room.rule_name}</span>
        <span className="rounded-lg bg-slate-100 px-2.5 py-1.5">{room.label}</span>
        <span className="inline-flex items-center gap-1 rounded-lg bg-slate-100 px-2.5 py-1.5">
          <UsersRound className="size-3.5" aria-hidden="true" /> {room.occupied_seats}/
          {room.side_size * 2} 席
        </span>
        <span
          className={`rounded-lg px-2.5 py-1.5 ${room.spectator_capacity_full ? 'bg-amber-50 text-amber-800' : 'bg-lime-50 text-lime-800'}`}
        >
          {room.spectator_capacity_full ? '观战席已满' : `观战余量 ${room.spectator_remaining}`}
        </span>
      </div>
      <Link
        className="mt-6 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-black !text-white transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
        href={entry.href}
      >
        {entry.label} <ArrowRight className="size-4" aria-hidden="true" />
      </Link>
    </article>
  );
}

function LobbyContent() {
  const router = useRouter();
  const { showToast } = useToast();
  const codeInputRef = useRef<HTMLInputElement>(null);
  const [roomCode, setRoomCode] = useState('');
  const query = useQuery({
    queryKey: lobbyQueryKey,
    queryFn: roomsApi.lobby,
    refetchInterval: 5_000,
    placeholderData: (previous) => previous,
    staleTime: 4_000,
  });
  const errorMessage =
    query.error instanceof ApiClientError ? query.error.message : '大厅暂时不可用，请稍后重试。';
  const rooms = query.data ?? [];
  const lookupMutation = useMutation({
    mutationFn: () => roomsApi.lookup(roomCode),
    onSuccess: (result) => router.push(`/rooms/${result.room_id}`),
    onError: (error) =>
      showToast({
        message: error instanceof ApiClientError ? error.message : '房间号查询失败，请稍后重试。',
        tone: 'error',
      }),
  });

  useEffect(() => {
    if (query.isError) {
      showToast({ message: errorMessage, tone: 'error' });
    }
  }, [errorMessage, query.isError, showToast]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('join') === '1') {
      codeInputRef.current?.focus();
    }
  }, []);

  function submitRoomCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (lookupMutation.isPending) return;
    lookupMutation.mutate();
  }

  return (
    <main className="jx-page-grid jx-page-viewport px-6 py-7 sm:px-10">
      <div className="mx-auto w-full max-w-7xl">
        <section className="relative mt-8 overflow-hidden rounded-[2rem] border border-blue-100/90 bg-white/70 p-7 shadow-[0_30px_90px_rgba(40,76,142,0.1)] backdrop-blur-xl sm:p-10">
          <div className="pointer-events-none absolute -right-24 -top-32 size-80 rounded-full bg-blue-200/35 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-40 left-1/3 size-96 rounded-full bg-lime-200/30 blur-3xl" />
          <div className="relative flex flex-wrap items-end justify-between gap-6">
            <div>
              <h1 className="text-4xl font-black tracking-[-0.06em] text-slate-950 sm:text-5xl">
                公开大厅
              </h1>
              <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-600 sm:text-base">
                登录后选择一场正在准备或进行中的比赛。观战席有全平台 10
                人上限，房间状态以服务端为准。
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Link
                className="hidden min-h-11 items-center gap-2 rounded-xl border border-blue-200 bg-white px-4 py-2.5 text-sm font-black text-blue-700 hover:bg-blue-50 sm:inline-flex"
                href="/rooms/create"
              >
                <Plus className="size-4" />
                创建房间
              </Link>
              <LobbySyncStatus
                isError={query.isError}
                isFetching={query.isFetching}
                onRetry={() => void query.refetch()}
              />
            </div>
          </div>
        </section>

        <section className="relative mt-6 overflow-hidden rounded-[1.5rem] border border-blue-200 bg-[#10233e] px-6 py-5 text-white shadow-[0_18px_52px_rgba(25,56,96,0.16)]">
          <div className="pointer-events-none absolute right-0 top-0 h-full w-48 bg-[radial-gradient(circle_at_right,rgba(183,239,0,0.2),transparent_66%)]" />
          <div className="relative flex flex-wrap items-center justify-between gap-5">
            <div className="flex items-center gap-3">
              <span className="grid size-11 place-items-center rounded-xl bg-lime-300 text-slate-950">
                <TicketCheck className="size-5" />
              </span>
              <div>
                <h2 className="font-black">使用房间号加入</h2>
                <p className="mt-1 text-xs text-slate-300">输入邀请中的 6 位数字房间号。</p>
              </div>
            </div>
            <form className="flex w-full max-w-md gap-2" onSubmit={submitRoomCode}>
              <label className="sr-only" htmlFor="room-code-input">
                房间号
              </label>
              <input
                ref={codeInputRef}
                autoCapitalize="characters"
                autoComplete="off"
                className="min-h-11 min-w-0 flex-1 rounded-xl border border-white/15 bg-white/10 px-4 font-mono text-sm font-black uppercase tracking-[0.18em] text-white outline-none placeholder:text-slate-400 focus:border-lime-300 focus:ring-4 focus:ring-lime-300/15"
                id="room-code-input"
                maxLength={12}
                onChange={(event) => {
                  setRoomCode(event.target.value.toUpperCase());
                  lookupMutation.reset();
                }}
                placeholder="输入 6 位数字房间号"
                value={roomCode}
              />
              <button
                className="jx-disabled-command inline-flex min-h-11 items-center gap-2 rounded-xl border border-lime-300 bg-lime-300 px-4 text-sm font-black text-slate-950"
                disabled={!roomCode.trim() || lookupMutation.isPending}
                type="submit"
              >
                {lookupMutation.isPending ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <ArrowRight className="size-4" />
                )}
                加入
              </button>
            </form>
          </div>
        </section>

        {query.isError && rooms.length === 0 ? (
          <div className="mt-6 flex items-start gap-3 rounded-2xl border border-blue-200 bg-blue-50/70 p-5 text-sm text-blue-800">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-blue-600" aria-hidden="true" />
            <div className="flex-1">
              <strong>大厅暂时没有加载出来</strong>
              <p className="mt-1 text-blue-700">请重新同步或稍后再试。</p>
              <button
                className="jx-disabled-command mt-3 rounded-xl border border-blue-700 bg-blue-700 px-4 py-2 text-xs font-black text-white hover:border-blue-800 hover:bg-blue-800"
                disabled={query.isFetching}
                onClick={() => void query.refetch()}
                type="button"
              >
                {query.isFetching ? '正在同步' : '重新同步'}
              </button>
            </div>
          </div>
        ) : null}

        {query.isError && rooms.length > 0 ? (
          <p className="mt-4 text-xs font-bold text-amber-700" role="status">
            当前展示上次同步结果，恢复连接后会自动更新。
          </p>
        ) : null}

        <section className="mt-7" aria-live="polite">
          {rooms.length ? (
            <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              {rooms.map((room) => (
                <RoomCard key={room.id} room={room} />
              ))}
            </div>
          ) : query.isPending ? (
            <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3].map((item) => (
                <div className="h-72 animate-pulse rounded-[1.5rem] bg-white/70" key={item} />
              ))}
            </div>
          ) : (
            <div className="grid min-h-64 place-items-center rounded-[1.5rem] border border-dashed border-blue-200 bg-white/60 px-6 text-center">
              <div>
                <Bot className="mx-auto size-9 text-blue-500" aria-hidden="true" />
                <h2 className="mt-4 text-xl font-black">暂时没有公开房间</h2>
                <p className="mt-2 text-sm text-slate-600">创建第一场人机辩论，邀请辩手入席。</p>
                <Link
                  className="mt-5 inline-flex rounded-xl bg-lime-300 px-4 py-2.5 text-sm font-black text-slate-950"
                  href="/rooms/create"
                >
                  创建房间
                </Link>
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

export function LobbyPage() {
  return <LobbyContent />;
}
