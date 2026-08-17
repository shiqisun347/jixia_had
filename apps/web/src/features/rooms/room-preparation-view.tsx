'use client';

import { Bot, Check, LoaderCircle, Sparkles, UserRound } from 'lucide-react';
import Image from 'next/image';
import { memo, useState } from 'react';

import { avatarAssetUrl } from '@/lib/avatar-catalog';
import type { RoomSnapshot } from '@/lib/rooms-api';

import type { PreparationFlow, PreparationStep } from './room-experience';

const STEP_LABELS: ReadonlyArray<{ id: PreparationStep; label: string; hint: string }> = [
  { id: 1, label: '选择身份', hint: '辩手或观众' },
  { id: 2, label: '选择席位', hint: '确定立场与辩位' },
  { id: 3, label: '检测设备', hint: '约 3 秒完成' },
  { id: 4, label: '准备完成', hint: '等待比赛开始' },
];

export function PreparationProgress({ flow }: Readonly<{ flow: PreparationFlow }>) {
  return (
    <section
      aria-label="入场进度"
      className="border-b border-blue-100 bg-gradient-to-r from-blue-50/80 via-white to-violet-50/70 px-5 py-5 sm:px-7"
    >
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-[10px] font-black tracking-[0.18em] text-blue-600">ENTRY PASS</p>
          <h2 className="mt-1 text-lg font-black text-slate-950">入场进度</h2>
        </div>
        <p className="text-sm font-bold text-slate-600">下一步：{flow.nextAction}</p>
      </div>
      <ol className="grid gap-2 md:grid-cols-4">
        {STEP_LABELS.map((step) => {
          const completed = flow.completedSteps.includes(step.id);
          const active = flow.activeStep === step.id;
          const skipped = flow.isSpectator && (step.id === 2 || step.id === 3);
          return (
            <li
              aria-current={active ? 'step' : undefined}
              className={`relative flex min-h-16 items-center gap-3 rounded-2xl border px-3.5 py-3 transition-colors ${
                active
                  ? 'border-blue-400 bg-white shadow-[0_10px_28px_rgba(57,123,255,0.14)]'
                  : completed
                    ? 'border-lime-300/70 bg-lime-50/75'
                    : 'border-slate-200 bg-white/60'
              }`}
              key={step.id}
            >
              <span
                className={`grid size-8 shrink-0 place-items-center rounded-xl text-xs font-black ${
                  completed
                    ? 'bg-lime-300 text-slate-950'
                    : active
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-100 text-slate-500'
                }`}
              >
                {completed ? <Check className="size-4" aria-hidden="true" /> : step.id}
              </span>
              <span className="min-w-0">
                <strong className="block text-sm text-slate-900">{step.label}</strong>
                <small className="mt-0.5 block truncate text-[11px] text-slate-500">
                  {skipped ? '观众无需操作' : step.hint}
                </small>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function SeatAvatar({
  seat,
  side,
}: Readonly<{
  seat: RoomSnapshot['seats'][number];
  side: 'AFFIRMATIVE' | 'NEGATIVE';
}>) {
  const [failed, setFailed] = useState(false);
  const human = seat.occupant_type === 'HUMAN';
  const agent = seat.occupant_type === 'AGENT';
  const source = human
    ? seat.user_id
      ? `/api/users/${seat.user_id}/avatar?v=${seat.occupant_avatar_version ?? 0}`
      : null
    : agent && seat.occupant_avatar_key
      ? avatarAssetUrl(seat.occupant_avatar_key)
      : null;
  const Icon = human ? UserRound : Bot;
  const sideRing = side === 'AFFIRMATIVE' ? 'ring-red-100' : 'ring-blue-100';

  return (
    <span
      className={`jx-identity-avatar relative grid size-14 shrink-0 place-items-center overflow-hidden border-2 bg-white ring-4 ${sideRing} ${
        human
          ? 'rounded-full border-red-200'
          : agent
            ? 'rounded-full border-violet-200'
            : 'rounded-full border-dashed border-slate-300'
      }`}
    >
      {source && !failed ? (
        <Image
          alt={seat.occupant_name ? `${seat.occupant_name}的头像` : '席位头像'}
          className="size-full object-cover"
          height={56}
          onError={() => setFailed(true)}
          src={source}
          unoptimized
          width={56}
        />
      ) : seat.occupant_type === 'EMPTY' ? (
        <span className="text-xl font-light text-slate-400">+</span>
      ) : (
        <Icon className={agent ? 'size-6 text-violet-500' : 'size-6 text-red-500'} />
      )}
      {agent ? (
        <span className="absolute right-0.5 top-0.5 grid size-4 place-items-center rounded-full bg-violet-600 text-white ring-2 ring-white">
          <Sparkles className="size-2.5" aria-hidden="true" />
        </span>
      ) : null}
    </span>
  );
}

export const RoomSeatCard = memo(function RoomSeatCard({
  room,
  side,
  seatNo,
  currentUserId,
  onSelect,
  onRequestSwap,
  disabled,
  loading,
}: Readonly<{
  room: RoomSnapshot;
  side: 'AFFIRMATIVE' | 'NEGATIVE';
  seatNo: number;
  currentUserId?: string;
  onSelect: () => void;
  onRequestSwap: (targetUserId: string) => void;
  disabled: boolean;
  loading: boolean;
}>) {
  const seat = room.seats.find((item) => item.side === side && item.seat_no === seatNo);
  if (!seat) return null;
  const own = seat.user_id === currentUserId;
  const human = seat.occupant_type === 'HUMAN';
  const agent = seat.occupant_type === 'AGENT';
  const actionLabel = own ? '我的席位' : human ? '申请交换席位' : '选择此席位';
  const sideName = side === 'AFFIRMATIVE' ? '正方' : '反方';
  const unavailable = disabled || (human && !seat.user_id);
  const actionTone = own
    ? 'border-lime-400 bg-lime-300 text-slate-950'
    : unavailable
      ? 'border-slate-300 bg-slate-200 text-slate-500'
      : human
        ? 'border-amber-500 bg-amber-500 text-white'
        : side === 'AFFIRMATIVE'
          ? 'border-red-600 bg-red-600 text-white'
          : 'border-blue-600 bg-blue-600 text-white';

  return (
    <button
      aria-label={`${sideName} ${seatNo} 辩，${actionLabel}`}
      aria-pressed={own}
      className={`group grid h-[8.75rem] w-full grid-cols-[3.5rem_minmax(0,1fr)] gap-3 rounded-2xl border p-3.5 text-left transition-[border-color,background-color,box-shadow,transform] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-200 active:translate-y-px ${
        own
          ? 'border-lime-400 bg-lime-50 shadow-[0_0_0_3px_rgba(183,237,0,0.14)]'
          : side === 'AFFIRMATIVE'
            ? 'border-red-100 bg-red-50/45 hover:-translate-y-0.5 hover:border-red-300'
            : 'border-blue-100 bg-blue-50/45 hover:-translate-y-0.5 hover:border-blue-300'
      } disabled:cursor-not-allowed disabled:transform-none disabled:border-slate-300 disabled:bg-slate-100 disabled:text-slate-400 disabled:shadow-none`}
      disabled={unavailable || own || loading}
      onClick={() => (human && seat.user_id ? onRequestSwap(seat.user_id) : onSelect())}
      type="button"
    >
      <SeatAvatar seat={seat} side={side} />
      <span className="flex min-w-0 flex-col">
        <span className="flex min-h-6 items-center gap-1.5">
          <strong className="truncate text-sm text-slate-950">
            {seat.occupant_name ?? `${sideName}${seatNo}辩`}
          </strong>
          {human ? (
            <small className="shrink-0 rounded-md bg-red-100 px-1.5 py-0.5 text-[9px] font-black text-red-700">
              真人
            </small>
          ) : agent ? (
            <small className="shrink-0 rounded-md bg-violet-100 px-1.5 py-0.5 text-[9px] font-black text-violet-700">
              AI
            </small>
          ) : null}
        </span>
        <small className="mt-1 text-[11px] font-bold text-slate-500">
          {sideName} · {seatNo} 辩
        </small>
        <span
          className={`mt-auto inline-flex min-h-7 items-center justify-center rounded-lg border px-2 text-[11px] font-black shadow-sm ${actionTone}`}
        >
          {loading ? <LoaderCircle className="mr-1 size-3 animate-spin" /> : null}
          {own ? <Check className="mr-1 size-3" /> : null}
          {loading ? '正在切换' : actionLabel}
        </span>
      </span>
    </button>
  );
});
