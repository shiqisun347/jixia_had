import Link from 'next/link';
import Image from 'next/image';
import type React from 'react';
import {
  ArrowRight,
  AudioLines,
  Bot,
  Box,
  ChevronRight,
  CircleDot,
  Clock3,
  DoorOpen,
  Radio,
  TicketCheck,
  Trophy,
  UserRound,
  UsersRound,
} from 'lucide-react';

import {
  getHomePrototypeFixture,
  type HomePrototypeFixture,
  type HomePrototypeScenario,
  type HomeRoomFixture,
  type RankingFixture,
} from '../prototype-fixtures/home';
import { SiteHeader } from '@/components/layout/site-header';

export interface HomePrototypeProps {
  readonly scenario?: HomePrototypeScenario;
  readonly fixture?: HomePrototypeFixture;
  readonly authNavigation?: React.ReactNode;
  readonly loading?: boolean;
  readonly roomSyncIssue?: boolean;
  readonly onRetryRooms?: () => void;
}

const steps = [
  {
    number: '01',
    title: '创建房间',
    description: '选择赛制与辩题',
    icon: Box,
  },
  {
    number: '02',
    title: '邀请参与',
    description: '人类与 Agent 共同入席',
    icon: UsersRound,
  },
  {
    number: '03',
    title: '开始辩论',
    description: '在实时语音中交锋',
    icon: AudioLines,
  },
] as const;

function SignalNetwork() {
  const nodes = [
    { side: 'human', className: 'left-[8%] top-[22%]', label: '人类辩手一' },
    { side: 'human', className: 'left-[18%] top-[60%]', label: '人类辩手二' },
    { side: 'agent', className: 'right-[9%] top-[20%]', label: 'Agent 辩手一' },
    { side: 'agent', className: 'right-[19%] top-[63%]', label: 'Agent 辩手二' },
  ] as const;

  return (
    <div
      className="relative min-h-[300px] overflow-hidden rounded-[2rem] border border-blue-100/80 bg-white/55 shadow-[0_32px_80px_rgba(33,80,155,0.12)]"
      aria-hidden="true"
    >
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(190,242,0,0.14),transparent_31%),linear-gradient(90deg,rgba(239,68,68,0.04),transparent_40%,transparent_60%,rgba(37,99,235,0.05))]" />
      <div className="absolute inset-6 rounded-full border border-blue-100/70" />
      <div className="absolute inset-[18%] rounded-full border border-dashed border-slate-200/80" />

      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 640 360" fill="none">
        <defs>
          <linearGradient id="home-signal-line" x1="70" y1="180" x2="570" y2="180">
            <stop stopColor="#ef4444" stopOpacity="0.25" />
            <stop offset="0.5" stopColor="#bef200" stopOpacity="0.9" />
            <stop offset="1" stopColor="#2563eb" stopOpacity="0.25" />
          </linearGradient>
        </defs>
        <path d="M100 90L270 174" stroke="#ef4444" strokeOpacity=".34" strokeWidth="2" />
        <path d="M145 265L270 186" stroke="#ef4444" strokeOpacity=".34" strokeWidth="2" />
        <path d="M540 90L370 174" stroke="#2563eb" strokeOpacity=".34" strokeWidth="2" />
        <path d="M495 265L370 186" stroke="#2563eb" strokeOpacity=".34" strokeWidth="2" />
        <path d="M58 180H582" stroke="url(#home-signal-line)" strokeWidth="2" />
        <path
          className="motion-safe:animate-pulse"
          d="M205 180H244L252 168L260 196L269 154L278 210L287 136L296 198L305 161L314 216L323 126L332 203L341 151L350 196L359 165L368 190L377 180H435"
          stroke="#b7ed00"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="4"
        />
      </svg>

      <div className="absolute left-1/2 top-1/2 grid size-28 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-lime-300/70 bg-white/90 shadow-[0_0_72px_rgba(183,237,0,0.38)]">
        <AudioLines className="size-11 text-slate-900" strokeWidth={1.8} />
      </div>

      {nodes.map((node) => {
        const human = node.side === 'human';
        const Icon = human ? UserRound : Bot;

        return (
          <div
            key={node.label}
            className={`absolute ${node.className} grid size-16 place-items-center rounded-full border bg-white/90 shadow-lg ${
              human
                ? 'border-red-200 text-red-500 shadow-red-100/70'
                : 'border-blue-200 text-blue-600 shadow-blue-100/70'
            }`}
          >
            <Icon className="size-7" strokeWidth={2} />
          </div>
        );
      })}

      <div className="absolute bottom-5 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-white/80 bg-white/80 px-4 py-2 text-xs font-semibold tracking-[0.16em] text-slate-500 shadow-sm">
        <Radio className="size-4 text-lime-600" />
        LIVE ARGUMENT FIELD
      </div>
    </div>
  );
}

function RoomCard({
  room,
  spectatorCapacityFull,
}: Readonly<{
  room: HomeRoomFixture;
  spectatorCapacityFull: boolean;
}>) {
  const sideIsRed = room.leadingSide === 'affirmative';
  const isFull = spectatorCapacityFull || room.spectatorRemaining === 0;
  const status =
    room.status === 'PAUSED'
      ? { label: '已暂停', className: 'bg-amber-100 text-amber-800' }
      : room.status === 'START_PENDING_RUNTIME'
        ? { label: '启动中', className: 'bg-blue-100 text-blue-800' }
        : { label: 'LIVE', className: 'bg-red-500 text-white' };

  return (
    <article className="group relative min-w-0 overflow-hidden rounded-2xl border border-slate-200/80 bg-white/90 p-5 shadow-[0_14px_40px_rgba(29,54,101,0.08)] transition-transform duration-200 motion-safe:hover:-translate-y-1">
      <div
        className={`absolute inset-x-0 top-0 h-0.5 ${sideIsRed ? 'bg-red-400' : 'bg-blue-500'}`}
        aria-hidden="true"
      />
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-black tracking-wide ${status.className}`}
          >
            <CircleDot className="size-3" /> {status.label}
          </span>
          <p className="truncate text-xs font-medium text-slate-500">
            {room.ruleName} · {room.format}
          </p>
        </div>
        <AudioLines
          className={sideIsRed ? 'size-7 shrink-0 text-red-400' : 'size-7 shrink-0 text-blue-500'}
          aria-hidden="true"
        />
      </div>

      <h3 className="mt-4 line-clamp-1 text-lg font-bold text-slate-950">{room.title}</h3>
      <p className="mt-2 line-clamp-2 min-h-10 text-sm leading-5 text-slate-600">{room.topic}</p>

      <div className="mt-5 flex items-center justify-between gap-3 border-t border-slate-100 pt-4 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <UsersRound className="size-4" aria-hidden="true" />
          <span className="sr-only">席位：</span>
          {room.occupiedSeats}/{room.totalSeats}
        </span>
        <span
          className={`rounded-full px-2.5 py-1 font-semibold ${
            isFull ? 'bg-amber-50 text-amber-800' : 'bg-lime-50 text-lime-800'
          }`}
        >
          {isFull ? '观战席已满' : `剩余 ${room.spectatorRemaining} 个观战席`}
        </span>
      </div>

      <a
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
        href={room.href ?? `/rooms/${encodeURIComponent(room.id)}`}
        aria-label={`查看比赛：${room.title}`}
      />
    </article>
  );
}

function RankingPanel({
  ranking,
  kind,
}: Readonly<{ ranking: RankingFixture; kind: 'human' | 'agent' }>) {
  const human = kind === 'human';
  const entries = ranking.entries.slice(0, 3);
  const podiumSlots = [
    { entry: entries[1], rank: 2, height: 'h-[3.75rem]', tone: 'silver' },
    { entry: entries[0], rank: 1, height: 'h-[5.5rem]', tone: 'gold' },
    { entry: entries[2], rank: 3, height: 'h-[3rem]', tone: 'bronze' },
  ] as const;

  return (
    <section
      className={`min-w-0 overflow-hidden rounded-2xl border bg-white/92 shadow-[0_20px_58px_rgba(29,54,101,0.08)] ${
        human ? 'border-red-100' : 'border-blue-100'
      }`}
      aria-labelledby={`${kind}-ranking-title`}
    >
      <header className="flex min-h-14 items-center justify-between gap-4 border-b border-slate-100 px-5 py-3 xl:px-6">
        <div className="flex items-center gap-2">
          <span
            className={`grid size-9 shrink-0 place-items-center rounded-full ${
              human ? 'bg-red-50 text-red-500' : 'bg-blue-50 text-blue-600'
            }`}
          >
            {human ? (
              <Trophy className="size-5" aria-hidden="true" />
            ) : (
              <Bot className="size-5" aria-hidden="true" />
            )}
          </span>
          <h2
            id={`${kind}-ranking-title`}
            className={`text-base font-black xl:text-lg ${human ? 'text-red-600' : 'text-blue-700'}`}
          >
            {ranking.title}
          </h2>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-slate-500">
          <Clock3 className="size-3.5" aria-hidden="true" />
          更新于 {ranking.updatedAt}
        </span>
      </header>

      {entries.length === 0 ? (
        <div className="grid min-h-[16.5rem] place-items-center px-6 text-center">
          <div>
            <Trophy className="mx-auto size-9 text-slate-200" aria-hidden="true" />
            <p className="mt-3 text-sm font-bold text-slate-500">完成首场比赛后将在这里上榜</p>
          </div>
        </div>
      ) : (
        <ol
          className="grid min-h-[16.5rem] grid-cols-3 items-end gap-2 px-3 pb-0 pt-3 sm:gap-3 sm:px-5"
          data-testid={`${kind}-podium`}
        >
          {podiumSlots.map(({ entry, rank, height, tone }) => {
            const palette =
              tone === 'gold'
                ? {
                    medal: 'bg-amber-400 text-white shadow-amber-200',
                    ring: 'border-amber-300 bg-amber-50 shadow-amber-100/80',
                    stage: 'from-amber-100 to-amber-50 text-amber-700',
                  }
                : tone === 'silver'
                  ? {
                      medal: 'bg-slate-300 text-white shadow-slate-200',
                      ring: 'border-slate-200 bg-slate-50 shadow-slate-100',
                      stage: 'from-slate-100 to-slate-50 text-slate-700',
                    }
                  : {
                      medal: 'bg-orange-300 text-white shadow-orange-100',
                      ring: 'border-orange-200 bg-orange-50 shadow-orange-100/80',
                      stage: 'from-orange-100 to-orange-50 text-orange-700',
                    };

            if (!entry) {
              return <li key={rank} aria-hidden="true" className="min-w-0" />;
            }

            return (
              <li key={entry.id} className="flex min-w-0 flex-col items-center text-center">
                <div className="flex min-h-[10.25rem] w-full flex-col items-center justify-end">
                  <span
                    className={`relative z-10 grid size-8 shrink-0 place-items-center rounded-full text-sm font-black shadow-lg ${palette.medal}`}
                    aria-label={`第 ${rank} 名`}
                  >
                    {rank}
                  </span>
                  <div
                    className={`relative -mt-1 grid overflow-hidden rounded-full border-4 text-lg font-black shadow-xl ${palette.ring} ${rank === 1 ? 'size-[5rem]' : 'size-[4.25rem]'}`}
                  >
                    <span className="absolute inset-0 grid place-items-center" aria-hidden="true">
                      {human ? entry.initials : <Bot className="size-8" />}
                    </span>
                    {entry.avatarSrc ? (
                      <Image
                        alt=""
                        className="jx-identity-avatar relative size-full rounded-full object-cover"
                        height={rank === 1 ? 80 : 68}
                        src={entry.avatarSrc}
                        unoptimized
                        width={rank === 1 ? 80 : 68}
                        onError={(event) => {
                          event.currentTarget.hidden = true;
                        }}
                      />
                    ) : null}
                  </div>
                  <p className="mt-2 w-full truncate px-1 text-sm font-black text-slate-950 xl:text-base">
                    {entry.displayName}
                  </p>
                  <p className="mt-1 whitespace-nowrap text-[11px] text-slate-500 xl:text-xs">
                    {entry.matches} 场 · 胜率 {entry.winRate}%
                  </p>
                </div>
                <div
                  className={`mt-2 flex w-full flex-col items-center justify-center rounded-t-2xl bg-gradient-to-b ${height} ${palette.stage}`}
                >
                  <span
                    className={`${rank === 1 ? 'text-3xl' : 'text-2xl'} font-black tabular-nums`}
                  >
                    {entry.score}
                  </span>
                  <span className="mt-1 text-xs font-bold opacity-80">
                    均分 {entry.averagePersonalScore.toFixed(1)}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <div className="flex min-h-11 items-center justify-end border-t border-slate-100 px-6 py-2">
        <Link
          className={`text-sm font-bold ${human ? 'text-red-600 hover:text-red-800' : 'text-blue-700 hover:text-blue-900'}`}
          href="/leaderboard"
        >
          查看完整榜单 <ChevronRight className="ml-1 inline size-4" aria-hidden="true" />
        </Link>
      </div>
    </section>
  );
}

export function HomePrototype({
  scenario = 'default',
  fixture,
  authNavigation,
  loading = false,
  roomSyncIssue = false,
  onRetryRooms,
}: HomePrototypeProps) {
  const data = fixture ?? getHomePrototypeFixture(scenario);

  return (
    <main id="home" className="min-h-screen overflow-x-hidden bg-[#f7faff] text-slate-950">
      <div
        className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_10%_0%,rgba(37,99,235,0.08),transparent_30rem),radial-gradient(circle_at_90%_25%,rgba(190,242,0,0.08),transparent_28rem)]"
        aria-hidden="true"
      />

      <SiteHeader
        authNavigation={
          authNavigation ?? (
            <div className="flex items-center gap-2">
              <Link
                className="rounded-xl border border-blue-100 bg-white px-4 py-2.5 text-sm font-bold text-slate-700"
                href="/login"
                prefetch={false}
              >
                登录
              </Link>
              <Link
                className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-black text-white"
                href="/register"
                prefetch={false}
              >
                注册
              </Link>
            </div>
          )
        }
      />

      <div className="relative z-10 mx-auto max-w-[1672px] px-6 pb-10 xl:px-10">
        <section
          className="grid min-h-[430px] items-center gap-10 py-12 lg:grid-cols-[0.88fr_1.12fr] xl:gap-16 xl:py-16"
          aria-labelledby="hero-title"
        >
          <div className="max-w-2xl">
            <h1
              id="hero-title"
              className="text-[clamp(2.7rem,4.3vw,4.9rem)] font-black leading-[1.08] tracking-[-0.055em] text-slate-950"
            >
              让人类与 Agent，
              <span className="mt-2 block">
                在声音中<span className="text-lime-600">交锋与共创</span>
              </span>
            </h1>
            <p className="mt-6 max-w-xl text-base leading-8 text-slate-600 xl:text-lg">
              让规则、发言权与实时语音在同一个可控空间里闭环，组织一场自然、稳定、可复盘的人机辩论。
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                className="inline-flex min-h-12 items-center gap-3 rounded-xl bg-lime-300 px-6 py-3 text-sm font-black text-slate-950 shadow-[0_12px_34px_rgba(183,237,0,0.3)] transition-transform motion-safe:hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-950 focus-visible:ring-offset-2"
                href="/rooms/create"
                prefetch={false}
              >
                <DoorOpen className="size-5" aria-hidden="true" /> 创建房间
                <ChevronRight className="size-4" aria-hidden="true" />
              </Link>
              <Link
                className="inline-flex min-h-12 items-center gap-2 rounded-xl px-4 py-3 text-sm font-bold text-blue-700 transition-colors hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                href="/lobby?join=1"
                prefetch={false}
              >
                <TicketCheck className="size-4" aria-hidden="true" /> 输入房间号
              </Link>
              <Link
                className="inline-flex min-h-12 items-center gap-3 rounded-xl border border-blue-200 bg-white px-6 py-3 text-sm font-bold text-slate-800 shadow-sm transition-colors hover:border-blue-300 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                href="/lobby"
                prefetch={false}
              >
                <Radio className="size-5 text-blue-600" aria-hidden="true" /> 进入公开大厅
                <ChevronRight className="size-4" aria-hidden="true" />
              </Link>
            </div>
          </div>

          <SignalNetwork />
        </section>

        <section
          id="live-rooms"
          className="scroll-mt-24 rounded-[1.75rem] border border-blue-100/80 bg-white/55 p-5 shadow-[0_24px_70px_rgba(40,76,142,0.08)] xl:p-7"
          aria-labelledby="live-rooms-title"
        >
          <div className="mb-5 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span
                className="size-2.5 rounded-full bg-lime-400 shadow-[0_0_0_6px_rgba(183,237,0,0.14)]"
                aria-hidden="true"
              />
              <h2 id="live-rooms-title" className="text-xl font-black">
                正在进行
              </h2>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600">
                {loading ? '同步中' : `${data.rooms.length} 场`}
              </span>
            </div>
            <Link
              className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-sm font-bold text-slate-600 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
              href="/lobby"
              prefetch={false}
            >
              查看全部 <ChevronRight className="size-4" aria-hidden="true" />
            </Link>
          </div>

          {!loading && data.spectatorCapacityFull ? (
            <div
              className="mb-5 flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
              role="status"
            >
              <UsersRound className="size-5 shrink-0" aria-hidden="true" />
              <span>
                <strong>观战席已满。</strong> 当前全平台观众已达上限，请稍后重试。
              </span>
            </div>
          ) : null}

          {!loading && roomSyncIssue ? (
            <div
              className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
              role="status"
            >
              <span>比赛信息暂时无法同步，当前显示最近一次成功结果。</span>
              <button
                className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-black text-amber-900 transition hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
                type="button"
                onClick={onRetryRooms}
              >
                重新同步
              </button>
            </div>
          ) : null}

          {loading ? (
            <div
              className="grid min-h-52 place-items-center rounded-2xl border border-blue-100 bg-white/70 px-6 text-center"
              role="status"
            >
              <div>
                <Radio className="mx-auto size-8 animate-pulse text-blue-500" aria-hidden="true" />
                <p className="mt-4 text-sm font-bold text-slate-600">正在同步比赛信息…</p>
              </div>
            </div>
          ) : data.rooms.length > 0 ? (
            <div className="grid gap-4 lg:grid-cols-3">
              {data.rooms.map((room) => (
                <RoomCard
                  key={room.id}
                  room={room}
                  spectatorCapacityFull={data.spectatorCapacityFull}
                />
              ))}
            </div>
          ) : roomSyncIssue ? (
            <div className="grid min-h-52 place-items-center rounded-2xl border border-dashed border-amber-200 bg-amber-50/60 px-6 text-center">
              <div>
                <Radio className="mx-auto size-8 text-amber-600" aria-hidden="true" />
                <h3 className="mt-4 text-lg font-black">暂时无法获取比赛列表</h3>
                <p className="mt-2 text-sm text-slate-600">请重新同步，或稍后再试。</p>
                <button
                  className="mt-5 inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                  type="button"
                  onClick={onRetryRooms}
                >
                  重新同步 <ArrowRight className="size-4" aria-hidden="true" />
                </button>
              </div>
            </div>
          ) : (
            <div className="grid min-h-52 place-items-center rounded-2xl border border-dashed border-blue-200 bg-white/70 px-6 text-center">
              <div>
                <Radio className="mx-auto size-8 text-blue-500" aria-hidden="true" />
                <h3 className="mt-4 text-lg font-black">目前没有进行中的比赛</h3>
                <p className="mt-2 text-sm text-slate-600">
                  可以先了解创建流程，准备下一场人机辩论。
                </p>
                <a
                  className="mt-5 inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                  href="#how-it-works"
                >
                  了解创建流程 <ArrowRight className="size-4" aria-hidden="true" />
                </a>
              </div>
            </div>
          )}
        </section>

        <section
          id="how-it-works"
          className="scroll-mt-24 py-7"
          aria-labelledby="how-it-works-title"
        >
          <h2 id="how-it-works-title" className="sr-only">
            三步开始辩论
          </h2>
          <ol className="grid overflow-hidden rounded-[1.75rem] border border-blue-100 bg-white/80 shadow-[0_20px_58px_rgba(40,76,142,0.07)] lg:grid-cols-3 lg:divide-x lg:divide-blue-100">
            {steps.map((step) => {
              const Icon = step.icon;
              return (
                <li key={step.number} className="relative flex items-center gap-5 px-7 py-6">
                  <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-lime-200 bg-lime-50 text-slate-950 shadow-[0_8px_24px_rgba(183,237,0,0.14)]">
                    <Icon className="size-6" aria-hidden="true" />
                  </span>
                  <span>
                    <span className="block text-xs font-black tracking-[0.12em] text-lime-700">
                      {step.number}
                    </span>
                    <span className="mt-1 block text-base font-black">{step.title}</span>
                    <span className="mt-1 block text-sm text-slate-500">{step.description}</span>
                  </span>
                  {step.number !== '03' ? (
                    <ChevronRight
                      className="ml-auto hidden size-5 text-slate-300 xl:block"
                      aria-hidden="true"
                    />
                  ) : null}
                </li>
              );
            })}
          </ol>
        </section>

        <section id="leaderboards" className="scroll-mt-24 py-7" aria-label="排行榜">
          <div className="grid gap-5 xl:grid-cols-2">
            <RankingPanel ranking={data.humanRanking} kind="human" />
            <RankingPanel ranking={data.agentRanking} kind="agent" />
          </div>
        </section>
      </div>
    </main>
  );
}
