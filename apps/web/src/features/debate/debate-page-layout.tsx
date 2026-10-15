'use client';

import {
  ArrowLeft,
  Bot,
  Check,
  CircleAlert,
  Clock3,
  FileText,
  Flag,
  LoaderCircle,
  Maximize2,
  Hand,
  Mic,
  Megaphone,
  Pause,
  Play,
  Radio,
  RefreshCcw,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  Square,
  UserRound,
  UsersRound,
  Volume2,
  VolumeX,
  Wifi,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import { Dialog, DropdownMenu } from 'radix-ui';

import { Button, buttonVariants } from '@/components/ui/button';
import { JixiaLogo } from '@/components/brand/jixia-logo';
import { useAppLocale, useAppTranslations } from '@/i18n';
import { avatarAssetUrl } from '@/lib/avatar-catalog';
import type { MatchSnapshot, MatchTranscript } from '@/lib/matches-api';
import type { RoomSnapshot } from '@/lib/rooms-api';

import {
  resolveCenterMode,
  showsFreeDebateBudget,
  type DebateCenterMode,
} from './match-presentation';

type CommandType =
  | 'speech.start'
  | 'speech.finish'
  | 'speech.reset'
  | 'hand.raise'
  | 'hand.cancel'
  | 'match.pause'
  | 'match.resume'
  | 'match.terminate';

type Presentation = {
  eyebrow: string;
  title: string;
  detail: string;
};

type RuntimeView = {
  socketStatus: 'connecting' | 'open' | 'closed' | 'error';
  socketError: string | null;
  interimText: string;
  resumeReasons: string[];
  commandReady: boolean;
  commandErrorCode?: string | null;
};
type NetworkStats = {
  rttMs: number | null;
  packetLossPercent: number | null;
  sampledAt: number | null;
};

type Seat = RoomSnapshot['seats'][number];
type Translator = (key: string, values?: Record<string, string | number | Date>) => string;
type AgentSeatStatus = 'SELECTED' | 'FALLBACK' | 'WAITING' | 'DECIDING' | 'RAISE' | 'SKIP' | null;

type DebatePageLayoutProps = {
  matchId: string;
  room: RoomSnapshot;
  snapshot: MatchSnapshot;
  runtime: RuntimeView;
  presentation: Presentation;
  transcript: MatchTranscript | undefined;
  transcriptLoading: boolean;
  transcriptError: boolean;
  onRetryTranscript: () => void;
  currentUserId: string;
  currentSeat: Seat | undefined;
  isCurrentSpeaker: boolean;
  isOrganizer: boolean;
  isDebater: boolean;
  handQueue: string[];
  agentHandQueue?: string[];
  myHandIndex: number;
  canRaiseHand: boolean | undefined;
  audioStatus: 'connecting' | 'ready' | 'error';
  playbackStatus: 'ready' | 'blocked';
  audioError: string | null;
  outputMuted: boolean;
  mutedHumanUserIds: string[];
  commandPending: boolean;
  leaving: boolean;
  editingSpeechId: string | null;
  draftText: string;
  savingSpeechId: string | null;
  drawerOpen: boolean;
  networkOpen?: boolean;
  networkStats?: NetworkStats;
  onCommand: (type: CommandType) => void;
  onStartSpeech: () => void;
  onFinishSpeech: () => void;
  onResetSpeech: () => void;
  onPause: () => void;
  onTerminate: () => void;
  onLeave: () => void;
  onEnableAudio: () => void;
  onToggleOutputMuted: () => void;
  onToggleAllHumanOutputMuted: () => void;
  onToggleHumanOutputMuted: (userId: string) => void;
  onOpenDrawer: () => void;
  onCloseDrawer: () => void;
  emergencyOpen?: boolean;
  emergencyControls?: ReactNode;
  onOpenEmergency?: () => void;
  onCloseEmergency?: () => void;
  onStartRuntime?: () => void;
  runtimeStartPending?: boolean;
  onEditSpeech: (speechId: string, text: string) => void;
  onSaveSpeech: (speechId: string) => void;
  onDraftTextChange: Dispatch<SetStateAction<string>>;
  onOpenNetwork?: () => void;
  onCloseNetwork?: () => void;
  presentationMode?: boolean;
  onTogglePresentation?: () => void;
};

const sideLabel = (side: string, t?: Translator) =>
  side === 'AFFIRMATIVE' ? (t?.('affirmative') ?? '正方') : (t?.('negative') ?? '反方');
const sideTone = (side: string) => (side === 'AFFIRMATIVE' ? 'red' : 'blue');
export const canPauseMatch = (status: string, isDebater: boolean, isOrganizer: boolean) =>
  status === 'RUNNING' && (isDebater || isOrganizer);
const matchStatusLabel = (status: string, actionState?: string, t?: Translator) => {
  if (actionState === 'RESUME_COUNTDOWN') return t?.('status.resumeCountdown') ?? '恢复倒计时';
  return (
    {
      START_PENDING_RUNTIME: t?.('status.starting') ?? '比赛启动中',
      START_COUNTDOWN: t?.('status.countdown') ?? '开始倒计时',
      RUNNING: t?.('status.running') ?? '进行中',
      PAUSED: t?.('status.paused') ?? '已暂停',
      SYSTEM_RECOVERY: t?.('status.recovery') ?? '恢复保护',
      ERROR: t?.('status.error') ?? '服务异常',
      FINISHED: t?.('status.finished') ?? '已结束',
      TERMINATED: t?.('status.terminated') ?? '已终止',
    }[status] ?? status
  );
};

export const runtimeErrorLabel = (code: string | null | undefined, t?: Translator) =>
  (
    ({
      PLAYER_OFFLINE_TIMEOUT:
        t?.('errors.playerOffline') ?? '人类辩手连续离线超过 60 秒，比赛已暂停。',
      HUMAN_START_TIMEOUT:
        t?.('errors.humanStart') ??
        '当前辩手 60 秒内未开始发言，比赛已暂停；确认设备后可申请恢复。',
      HUMAN_WAIT_TIMEOUT:
        t?.('errors.humanWait') ?? '60 秒内无人举手，比赛已暂停；恢复后将重新开始完整等待。',
      llm_capacity_full: t?.('errors.llmCapacity') ?? '模型并发已满，比赛已暂停，请稍后申请恢复。',
      llm_first_token_timeout:
        t?.('errors.llmFirstTokenTimeout') ??
        'Agent 10 秒内没有返回首段文字，已重试仍失败，比赛已暂停，请申请恢复。',
      llm_stream_stalled:
        t?.('errors.llmStreamStalled') ??
        'Agent 文本流中断，已重试仍失败，比赛已暂停，请申请恢复。',
      llm_provider_failed:
        t?.('errors.llmProviderFailed') ??
        'Agent 服务暂时没有返回有效结果，已重试仍失败，比赛已暂停，请申请恢复。',
      tts_stream_interrupted:
        t?.('errors.ttsInterrupted') ?? '语音合成连续失败，比赛已暂停，请申请恢复。',
      tts_parameter_invalid:
        t?.('errors.ttsParameterInvalid') ??
        '语音服务拒绝了当前音色或参数，比赛已暂停，请联系管理员检查音色配置后申请恢复。',
      tts_task_timeout:
        t?.('errors.ttsTaskTimeout') ?? '语音合成超过时限，比赛已暂停，请申请恢复。',
      tts_audio_too_large:
        t?.('errors.ttsAudioTooLarge') ?? '语音输出超过大小限制，比赛已暂停，请申请恢复。',
      tts_provider_failed:
        t?.('errors.ttsInterrupted') ?? '语音合成连续失败，比赛已暂停，请申请恢复。',
      asr_stream_interrupted:
        t?.('errors.asrInterrupted') ?? '语音识别连续失败，比赛已暂停，请申请恢复。',
      llm_stream_interrupted:
        t?.('errors.llmInterrupted') ?? 'Agent 文本生成连续失败，比赛已暂停，请申请恢复。',
      llm_output_too_large:
        t?.('errors.llmOutputTooLarge') ?? 'Agent 文本输出超过大小限制，比赛已暂停，请申请恢复。',
    }) as Record<string, string>
  )[code ?? ''] ??
  t?.('errors.default') ??
  '实时服务发生异常，比赛已暂停，请检查设备后申请恢复。';

export const shouldShowRuntimeFault = (status: string, errorCode: string | null | undefined) =>
  ['ERROR', 'SYSTEM_RECOVERY'].includes(status) || (status === 'PAUSED' && Boolean(errorCode));

function formatRemaining(milliseconds: number | null | undefined) {
  if (milliseconds === null || milliseconds === undefined) return '--:--';
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  return `${Math.floor(totalSeconds / 60)
    .toString()
    .padStart(2, '0')}:${(totalSeconds % 60).toString().padStart(2, '0')}`;
}

const ENGLISH_STANDARD_STAGE_NAMES: Record<string, string> = {
  正方一辩立论: 'First Affirmative Constructive',
  反方一辩立论: 'First Negative Constructive',
  正方二辩陈词: 'Second Affirmative Speech',
  反方二辩陈词: 'Second Negative Speech',
  正方三辩陈词: 'Third Affirmative Speech',
  反方三辩陈词: 'Third Negative Speech',
  自由辩论: 'Free Debate',
  反方四辩总结: 'Negative Closing Summary',
  正方四辩总结: 'Affirmative Closing Summary',
  比赛结束: 'Match Complete',
};

export function localizedStageName(name: string, locale?: string) {
  return locale === 'en' ? (ENGLISH_STANDARD_STAGE_NAMES[name] ?? name) : name;
}

function stageNameForSpeech(
  room: RoomSnapshot,
  actionKey: string,
  t?: Translator,
  locale?: string,
) {
  const stagePosition = Number(actionKey.split(':')[0]);
  const stages = Array.isArray(room.rule?.stages) ? room.rule.stages : [];
  const stage = stages.find((candidate) => Number(candidate?.position) === stagePosition);
  return typeof stage?.name === 'string' && stage.name.trim()
    ? localizedStageName(stage.name, locale)
    : Number.isFinite(stagePosition) && stagePosition > 0
      ? (t?.('detail.stageNumber', { position: stagePosition }) ?? `第 ${stagePosition} 阶段`)
      : (t?.('speech') ?? '比赛发言');
}

function currentStageName(
  room: RoomSnapshot,
  snapshot: MatchSnapshot,
  t?: Translator,
  locale?: string,
) {
  if (snapshot.status === 'FINISHED') return t?.('status.finished') ?? '比赛结束';
  if (snapshot.status === 'TERMINATED') return t?.('status.terminated') ?? '比赛终止';
  const stagePosition = snapshot.current_action?.stage_position;
  if (!stagePosition) {
    if (snapshot.status === 'FINISHED') return t?.('status.finished') ?? '比赛结束';
    if (snapshot.status === 'TERMINATED') return t?.('status.terminated') ?? '比赛终止';
    return t?.('status.starting') ?? '比赛准备';
  }
  return stageNameForSpeech(room, `${stagePosition}:0`, t, locale);
}

function transcriptText(
  room: RoomSnapshot,
  transcript: MatchTranscript | undefined,
  t?: Translator,
  locale?: string,
) {
  return (transcript?.speeches ?? [])
    .map((speech) => {
      const speaker = `${sideLabel(speech.side, t)} ${speech.seat_no}`;
      return `${stageNameForSpeech(room, speech.action_key, t, locale)}\n${speaker}\n${speech.display_text || (t?.('emptySpeech') ?? '（空发言）')}`;
    })
    .join('\n\n');
}

function initials(label: string | null | undefined) {
  const value = label?.trim() || '空';
  return value.slice(0, 1);
}

function SpeakerMark({
  seat,
  active = false,
  large = false,
  online,
}: {
  seat?: Seat;
  active?: boolean;
  large?: boolean;
  online?: boolean;
}) {
  const t = useAppTranslations('Debate');
  const tone = sideTone(seat?.side ?? 'AFFIRMATIVE');
  const avatarUrl =
    seat?.occupant_type === 'HUMAN' && seat.user_id
      ? `/api/users/${seat.user_id}/avatar?v=${seat.occupant_avatar_version ?? 0}`
      : seat?.occupant_type === 'AGENT' && seat.occupant_avatar_key
        ? avatarAssetUrl(seat.occupant_avatar_key)
        : null;
  return (
    <span
      className={`debate-speaker-mark relative block shrink-0 ${large ? 'debate-speaker-mark--large size-28 xl:size-32' : 'size-14 xl:size-16'}`}
    >
      <span
        className={`relative grid size-full place-items-center overflow-hidden border ${
          large
            ? 'size-28 rounded-full text-4xl xl:size-32'
            : 'size-14 rounded-full text-2xl xl:size-16'
        } ${
          active
            ? 'jx-current-speaker-shadow border-[#b7ef00] bg-white text-slate-950'
            : tone === 'red'
              ? 'border-red-200 bg-red-50 text-red-600'
              : 'border-blue-200 bg-blue-50 text-blue-700'
        }`}
        aria-hidden="true"
      >
        {avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- authenticated user avatars are not static image sources.
          <img
            alt=""
            className="absolute inset-0 size-full object-cover"
            loading="eager"
            src={avatarUrl}
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : seat?.occupant_type === 'AGENT' ? (
          <Bot className={large ? 'size-14' : 'size-8'} />
        ) : seat?.occupant_name ? (
          <span className="font-black">{initials(seat.occupant_name)}</span>
        ) : (
          <UserRound className={large ? 'size-14' : 'size-8'} />
        )}
      </span>
      {seat?.occupant_type === 'HUMAN' && online !== undefined ? (
        <span
          aria-label={online ? t('online') : t('offline')}
          className={`absolute -bottom-0.5 -right-0.5 z-10 rounded-full border-2 border-white ${large ? 'size-4' : 'size-3'} ${online ? 'bg-emerald-400' : 'bg-slate-400'}`}
          data-testid="participant-presence"
          role="img"
        />
      ) : null}
    </span>
  );
}

function Waveform({
  tone = 'lime',
  compact = false,
}: {
  tone?: 'lime' | 'red' | 'blue';
  compact?: boolean;
}) {
  const bars = [12, 22, 9, 30, 16, 42, 20, 56, 28, 72, 34, 48, 19, 62, 28, 45, 15, 32, 12];
  const visibleBars = compact ? bars.slice(6, 13) : bars;
  const color = tone === 'red' ? 'bg-red-400' : tone === 'blue' ? 'bg-blue-400' : 'bg-[#b7ef00]';
  return (
    <div
      className={`flex shrink-0 items-center justify-center ${compact ? 'h-8 w-14 gap-0.5 overflow-hidden' : 'h-14 gap-1'}`}
      aria-hidden="true"
    >
      {visibleBars.map((height, index) => (
        <span
          className={`${color} ${compact ? 'w-0.5' : 'w-1'} rounded-full opacity-80 motion-safe:animate-[jx-wave_1.4s_ease-in-out_infinite]`}
          key={`${height}-${index}`}
          style={{
            height: `${compact ? Math.max(5, height / 2) : height}%`,
            animationDelay: `${index * 35}ms`,
          }}
        />
      ))}
    </div>
  );
}

function CountdownTrack({ milliseconds }: { milliseconds: number | null | undefined }) {
  const t = useAppTranslations('Debate');
  const value =
    milliseconds === null || milliseconds === undefined
      ? null
      : Math.max(1, Math.ceil(milliseconds / 1000));
  return (
    <div
      aria-label={t('detail.countdown')}
      className="mt-3 flex items-center justify-center"
      role="status"
    >
      <span
        className="grid size-12 place-items-center border border-[#b7ef00]/80 bg-white/90 font-mono text-xl font-black text-[#6c9100] shadow-[0_0_30px_rgba(183,239,0,0.28)] motion-safe:animate-[jx-countdown_.45s_ease-out] [clip-path:polygon(25%_7%,75%_7%,100%_50%,75%_93%,25%_93%,0_50%)]"
        key={value}
      >
        {value ?? '…'}
      </span>
    </div>
  );
}

function SeatCard({
  seat,
  active,
  handOrder,
  humanSelected,
  agentHandOrder,
  agentStatus,
  online,
}: {
  seat: Seat;
  active: boolean;
  handOrder: number;
  humanSelected: boolean;
  agentHandOrder: number;
  agentStatus: AgentSeatStatus;
  online?: boolean;
}) {
  const t = useAppTranslations('Debate');
  const tone = sideTone(seat.side);
  return (
    <article
      className={`debate-seat-card group relative flex min-h-0 items-center gap-3 rounded-2xl border px-3 py-2.5 transition ${
        active
          ? 'jx-active-seat-shadow border-[#b7ef00] bg-white ring-2 ring-[#eaff9f]'
          : tone === 'red'
            ? 'border-red-100/80 bg-gradient-to-br from-white via-white to-red-50/70 hover:border-red-300'
            : 'border-blue-100/80 bg-gradient-to-br from-white via-white to-blue-50/70 hover:border-blue-300'
      }`}
    >
      <SpeakerMark seat={seat} active={active} online={online} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <strong className="truncate text-base font-black leading-tight text-slate-950 xl:text-lg">
            {seat.occupant_name ?? t('emptySeat')}
          </strong>
          <span
            className={`shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-black leading-none ${seat.occupant_type === 'AGENT' ? 'bg-blue-100 text-blue-800' : 'bg-red-100 text-red-800'}`}
          >
            {seat.occupant_type === 'AGENT' ? t('ai') : t('human')}
          </span>
        </div>
        <p
          className={`mt-1 text-sm font-black ${tone === 'red' ? 'text-red-700' : 'text-blue-700'}`}
        >
          {sideLabel(seat.side, t)} {seat.seat_no}
        </p>
        {handOrder > 0 ? (
          <span
            className={`mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-black ${humanSelected ? 'bg-[#eaff9f] text-[#526b00]' : 'bg-amber-100 text-amber-700'}`}
          >
            <Hand className="size-3" /> {t('detail.rank', { position: handOrder })}
            {humanSelected ? ` · ${t('detail.selectedToSpeak')}` : ''}
          </span>
        ) : agentStatus === 'RAISE' ? (
          <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-black text-amber-700">
            <Hand className="size-3" /> {t('detail.agentRequested')}
          </span>
        ) : agentHandOrder > 0 ? (
          <span
            aria-label={`${t('ai')} ${t('detail.rank', { position: agentHandOrder })}`}
            className={`mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-black ${agentStatus === 'SELECTED' ? 'bg-[#eaff9f] text-[#526b00]' : 'bg-amber-100 text-amber-700'}`}
          >
            <Hand className="size-3" />{' '}
            {agentStatus === 'SELECTED'
              ? t('detail.selectedToSpeak')
              : t('detail.rank', { position: agentHandOrder })}
          </span>
        ) : agentStatus === 'FALLBACK' ? (
          <span className="mt-1 inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-black text-slate-600">
            {t('detail.agentFallback')}
          </span>
        ) : agentStatus === 'DECIDING' ? (
          <span
            aria-label={t('detail.agentDeciding')}
            className="mt-1 inline-flex rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-black text-blue-600 motion-safe:animate-pulse"
            role="status"
          >
            {t('detail.agentDeciding')}
          </span>
        ) : agentStatus === 'WAITING' ? (
          <span
            aria-label={t('detail.agentWaiting')}
            className="mt-1 inline-flex rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[10px] font-black text-blue-600"
            role="status"
          >
            {t('detail.agentWaiting')}
          </span>
        ) : agentStatus === 'SKIP' ? (
          <span className="mt-1 inline-flex rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-black text-slate-600">
            {t('detail.skip')}
          </span>
        ) : null}
      </div>
      {active ? <Waveform tone={tone === 'red' ? 'red' : 'blue'} compact /> : null}
    </article>
  );
}

export function experimentAgentSeatStatus(
  snapshot: MatchSnapshot,
  seat: Pick<Seat, 'occupant_type' | 'side'>,
  agentProfileId?: string | null,
): AgentSeatStatus {
  if (!(snapshot.experiment_mode || snapshot.formal_4v4) || seat.occupant_type !== 'AGENT')
    return null;
  const candidateSide =
    snapshot.action_state === 'FREE_SELECTING' || snapshot.action_state === 'HOST_ANNOUNCING'
      ? snapshot.free_holder_side
      : snapshot.current_speaker_side === 'AFFIRMATIVE'
        ? 'NEGATIVE'
        : snapshot.current_speaker_side === 'NEGATIVE'
          ? 'AFFIRMATIVE'
          : null;
  if (seat.side !== candidateSide) return null;
  const decisionStatus = snapshot.formal_4v4
    ? snapshot.agent_decisions?.find((item) => item.agent_profile_id === agentProfileId)?.status
    : undefined;
  const status =
    decisionStatus === 'HAND'
      ? 'RAISE'
      : (decisionStatus ?? snapshot.experiment_team_state?.agent_status);
  if (status === 'TECHNICAL_MISSING') return 'SKIP';
  return status ?? null;
}

export function freeDebateProgress(remainingMs: number, totalMs: number | null) {
  if (totalMs === null || totalMs <= 0) return null;
  return Math.min(100, Math.max(0, (remainingMs / totalMs) * 100));
}

function FreeDebateClock({ snapshot }: { snapshot: MatchSnapshot }) {
  const t = useAppTranslations('Debate');
  if (
    !showsFreeDebateBudget(snapshot) ||
    snapshot.free_affirmative_remaining_ms === null ||
    snapshot.free_affirmative_remaining_ms === undefined
  ) {
    return null;
  }
  const affirmative = snapshot.free_affirmative_remaining_ms;
  const negative = snapshot.free_negative_remaining_ms ?? 0;
  const total =
    snapshot.current_action?.action_kind === 'FREE_DEBATE' &&
    snapshot.current_action.duration_seconds > 0
      ? snapshot.current_action.duration_seconds * 1000
      : null;
  const affirmativeProgress = freeDebateProgress(affirmative, total);
  const negativeProgress = freeDebateProgress(negative, total);
  const mode = resolveCenterMode(snapshot);
  const holderSide = ['speaking', 'human-ready', 'agent-preparing'].includes(mode)
    ? snapshot.current_speaker_side
    : null;
  const affirmativeActive = holderSide === 'AFFIRMATIVE';
  const negativeActive = holderSide === 'NEGATIVE';
  return (
    <div
      className="mt-3 w-full max-w-xl rounded-2xl border border-blue-100 bg-white/90 px-3 py-2.5 shadow-[0_8px_24px_rgba(42,83,130,0.08)]"
      data-testid="free-debate-clock"
    >
      <div className="flex items-center justify-between gap-2 text-xs font-black sm:text-sm">
        <span
          className={`rounded-lg px-2 py-1 text-red-700 ${affirmativeActive ? 'bg-[#efffc1] ring-1 ring-[#b7ef00]/60' : 'bg-red-50/60'}`}
        >
          {t('affirmative')} {formatRemaining(affirmative)} / {formatRemaining(total)}
        </span>
        <span className="rounded-lg bg-slate-100 px-2 py-1 text-[10px] text-slate-600 sm:text-xs">
          {holderSide
            ? t('holder', { side: sideLabel(holderSide, t) })
            : snapshot.free_holder_side
              ? t('center.applicantSide', { side: sideLabel(snapshot.free_holder_side, t) })
              : t('freeDebate')}
        </span>
        <span
          className={`rounded-lg px-2 py-1 text-blue-700 ${negativeActive ? 'bg-[#efffc1] ring-1 ring-[#b7ef00]/60' : 'bg-blue-50/60'}`}
        >
          {t('negative')} {formatRemaining(negative)} / {formatRemaining(total)}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1.5">
        <span
          aria-label={t('sideRemaining', { side: t('affirmative') })}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={affirmativeProgress === null ? undefined : Math.round(affirmativeProgress)}
          className="h-2 overflow-hidden rounded-full bg-red-100"
          role="progressbar"
        >
          <span
            className={`block h-full origin-left rounded-full transition-transform duration-200 motion-reduce:transition-none ${affirmativeProgress === null ? 'bg-red-200' : 'bg-red-500'}`}
            style={{ transform: `scaleX(${(affirmativeProgress ?? 100) / 100})` }}
          />
        </span>
        <span
          aria-label={t('sideRemaining', { side: t('negative') })}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={negativeProgress === null ? undefined : Math.round(negativeProgress)}
          className="flex h-2 justify-end overflow-hidden rounded-full bg-blue-100"
          role="progressbar"
        >
          <span
            className={`block h-full w-full origin-right rounded-full transition-transform duration-200 motion-reduce:transition-none ${negativeProgress === null ? 'bg-blue-200' : 'bg-blue-500'}`}
            style={{ transform: `scaleX(${(negativeProgress ?? 100) / 100})` }}
          />
        </span>
      </div>
    </div>
  );
}

function TranscriptFeed({
  transcript,
  runtime,
  snapshot,
  currentSeat,
  currentUserId,
  editingSpeechId,
  draftText,
  savingSpeechId,
  onEditSpeech,
  onSaveSpeech,
  onDraftTextChange,
  transcriptLoading,
  transcriptError,
  onRetryTranscript,
  room,
}: Pick<
  DebatePageLayoutProps,
  | 'transcript'
  | 'runtime'
  | 'snapshot'
  | 'currentSeat'
  | 'currentUserId'
  | 'editingSpeechId'
  | 'draftText'
  | 'savingSpeechId'
  | 'onEditSpeech'
  | 'onSaveSpeech'
  | 'onDraftTextChange'
  | 'transcriptLoading'
  | 'transcriptError'
  | 'onRetryTranscript'
  | 'room'
>) {
  const t = useAppTranslations('Debate');
  const { locale } = useAppLocale();
  const [copyState, setCopyState] = useState<'idle' | 'success' | 'error'>('idle');
  const copyTranscript = async () => {
    try {
      await navigator.clipboard.writeText(transcriptText(room, transcript, t, locale));
      setCopyState('success');
      window.setTimeout(() => setCopyState('idle'), 1600);
    } catch {
      setCopyState('error');
      window.setTimeout(() => setCopyState('idle'), 2400);
    }
  };
  return (
    <div className="space-y-3">
      {transcriptError ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-xs font-bold text-amber-900"
          role="status"
        >
          <span>{transcript ? t('transcriptInterrupted') : t('transcriptUnavailable')}</span>
          <Button onClick={onRetryTranscript} size="sm" variant="secondary">
            <RefreshCcw className="size-3.5" /> {t('syncAgain')}
          </Button>
        </div>
      ) : null}
      {transcriptLoading && !transcript ? (
        <div
          className="flex items-center justify-center gap-2 py-12 text-sm font-bold text-slate-500"
          role="status"
        >
          <RefreshCcw className="size-4 animate-spin" /> {t('syncingTranscript')}
        </div>
      ) : null}
      {transcript?.speeches.length ? (
        transcript.speeches.map((speech) => {
          const isPlaceholder = 'origin' in speech && speech.origin === 'ADMIN_PLACEHOLDER';
          const canEdit =
            !isPlaceholder && speech.user_id === currentUserId && speech.status === 'FINALIZED';
          const editing = canEdit && editingSpeechId === speech.id;
          return (
            <article
              className="rounded-2xl border border-slate-100 bg-white/75 p-4"
              data-speech-id={speech.id}
              key={speech.id}
            >
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-bold text-slate-500">
                <span className="text-slate-700">
                  {stageNameForSpeech(room, speech.action_key, t, locale)}
                </span>
                <span className="text-slate-300">·</span>
                <span className={speech.side === 'AFFIRMATIVE' ? 'text-red-600' : 'text-blue-600'}>
                  {sideLabel(speech.side, t)} {speech.seat_no}
                </span>
                {isPlaceholder ? (
                  <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-800">
                    {t('detail.adminPlaceholder')}
                  </span>
                ) : null}
              </div>
              {editing ? (
                <textarea
                  aria-label={t('detail.transcriptEditing')}
                  className="mt-3 min-h-32 w-full rounded-xl border border-blue-200 bg-white p-3 text-sm leading-7 outline-none focus:border-blue-500"
                  value={draftText}
                  onChange={(event) => onDraftTextChange(event.target.value)}
                />
              ) : (
                <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-700">
                  {speech.display_text || t('emptySpeech')}
                </p>
              )}
              {canEdit ? (
                <div className="mt-3 flex justify-end gap-2">
                  {editing ? (
                    <Button
                      disabled={savingSpeechId === speech.id}
                      onClick={() => onSaveSpeech(speech.id)}
                      size="sm"
                      variant="primary"
                    >
                      <Save className="size-4" /> {t('save')}
                    </Button>
                  ) : (
                    <Button
                      onClick={() => onEditSpeech(speech.id, speech.display_text ?? '')}
                      size="sm"
                      variant="secondary"
                    >
                      {t('editMine')}
                    </Button>
                  )}
                </div>
              ) : null}
            </article>
          );
        })
      ) : !transcriptLoading && !transcriptError ? (
        <p className="py-12 text-center text-sm text-slate-500">{t('noSpeeches')}</p>
      ) : null}
      {runtime.interimText ? (
        <article className="rounded-2xl border border-[#b7ef00] bg-[#f5ffd5] p-4 shadow-[0_8px_22px_rgba(183,239,0,0.1)]">
          <div className="flex items-center justify-between gap-3 text-xs font-bold text-slate-600">
            <span>
              {t('detail.currentLive')} ·{' '}
              {sideLabel(currentSeat?.side ?? snapshot.current_speaker_side ?? '', t)}{' '}
              {currentSeat?.seat_no ?? snapshot.current_speaker_seat_no ?? ''}
            </span>
            <span className="rounded-full bg-[#dffb72] px-2 py-1 text-[10px] text-[#4b6300]">
              {snapshot.action_state === 'AGENT_SPEAKING' ? t('liveAgentText') : t('liveAsrText')}
            </span>
          </div>
          <Waveform compact />
          <p className="whitespace-pre-wrap text-sm leading-7 text-slate-800">
            {runtime.interimText}
          </p>
        </article>
      ) : null}
      <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
        <Button onClick={() => void copyTranscript()} size="sm" variant="secondary">
          <FileText className="size-3.5" />
          {copyState === 'success'
            ? t('copied')
            : copyState === 'error'
              ? t('copyFailed')
              : t('copyAll')}
        </Button>
        {copyState === 'error' ? (
          <span className="text-[10px] font-bold text-amber-700">{t('clipboardPermission')}</span>
        ) : null}
      </div>
    </div>
  );
}

function CenterStatusCard({
  icon,
  title,
  children,
  testId,
}: Readonly<{ icon: ReactNode; title: string; children?: ReactNode; testId?: string }>) {
  return (
    <div
      className="debate-center-card mt-4 flex w-full max-w-md flex-col items-center rounded-3xl border border-blue-100 bg-white/80 px-5 py-5"
      data-testid={testId}
    >
      <span className="debate-center-icon grid size-14 shrink-0 place-items-center rounded-full bg-blue-50 text-blue-700">
        {icon}
      </span>
      <strong className="mt-3 text-lg font-black text-slate-950">{title}</strong>
      {children}
    </div>
  );
}

function CenterTimer({
  milliseconds,
  label,
}: Readonly<{
  milliseconds: number | null | undefined;
  label: string;
}>) {
  const t = useAppTranslations('Debate');
  return (
    <div className="mt-2" data-testid="center-timer">
      <p className="text-xs font-bold text-slate-500">{label}</p>
      <p className="debate-center-time mt-1 font-mono text-4xl font-black tracking-tight text-slate-950">
        {milliseconds == null ? t('center.syncingTime') : formatRemaining(milliseconds)}
      </p>
    </div>
  );
}

function CenterContent({
  mode,
  room,
  snapshot,
  currentSeat,
  matchId,
  isOrganizer,
  onStartRuntime,
  runtimeStartPending,
  handoffPending,
}: Readonly<{
  mode: DebateCenterMode;
  room: RoomSnapshot;
  snapshot: MatchSnapshot;
  currentSeat: Seat | undefined;
  matchId: string;
  isOrganizer: boolean;
  onStartRuntime?: () => void;
  runtimeStartPending: boolean;
  handoffPending: boolean;
}>) {
  const t = useAppTranslations('Debate');
  const hint = (text: string) => <p className="mt-2 text-sm leading-6 text-slate-600">{text}</p>;
  if (mode === 'finished' || mode === 'terminated') {
    return (
      <CenterStatusCard
        icon={mode === 'finished' ? <Check className="size-7" /> : <Square className="size-6" />}
        title={t(mode === 'finished' ? 'center.thanks' : 'center.recordRetained')}
        testId="terminal-stage"
      >
        {hint(t(mode === 'finished' ? 'center.finishedHint' : 'center.terminatedHint'))}
        <Link
          className={`${buttonVariants({ variant: 'primary', size: 'sm' })} mt-4`}
          href={`/matches/${matchId}`}
        >
          <FileText className="size-4" />
          {t(mode === 'finished' ? 'postmatch' : 'terminatedRecord')}
        </Link>
      </CenterStatusCard>
    );
  }
  if (mode === 'frozen') {
    return (
      <CenterStatusCard
        icon={<Pause className="size-7" />}
        title={t('detail.runtimeFrozen')}
        testId="runtime-frozen-state"
      >
        {hint(t('detail.runtimeFrozenHint'))}
      </CenterStatusCard>
    );
  }
  if (mode === 'waiting') {
    return (
      <CenterStatusCard
        icon={<Clock3 className="size-7" />}
        title={t('center.waitingTitle')}
        testId="match-waiting-stage"
      >
        {hint(t('center.waitingHint'))}
        {isOrganizer ? (
          <Button
            className="mt-4"
            disabled={runtimeStartPending}
            onClick={onStartRuntime}
            size="sm"
            variant="primary"
          >
            {runtimeStartPending ? (
              <LoaderCircle className="size-4 motion-safe:animate-spin" />
            ) : (
              <Play className="size-4" />
            )}
            {t('center.startMatch')}
          </Button>
        ) : null}
      </CenterStatusCard>
    );
  }
  if (mode === 'countdown')
    return <CountdownTrack milliseconds={snapshot.countdown_remaining_ms} />;
  if (mode === 'host') {
    const identity = room.rule.host_identity;
    const name =
      identity &&
      typeof identity === 'object' &&
      'name' in identity &&
      typeof identity.name === 'string'
        ? identity.name
        : t('center.hostName');
    return (
      <CenterStatusCard
        icon={<Megaphone className="size-7" />}
        title={t('detail.hostAnnouncing')}
        testId="host-announcement-state"
      >
        <p className="mt-1 font-bold text-blue-800">{name}</p>
        {hint(t('detail.hostHint'))}
        <Waveform />
      </CenterStatusCard>
    );
  }
  if (mode === 'agent-preparing') {
    return (
      <CenterStatusCard
        icon={<Bot className="size-7" />}
        title={currentSeat?.occupant_name ?? t('center.agentPreparing')}
        testId="agent-preparing-stage"
      >
        {hint(t('center.agentPreparingHint'))}
      </CenterStatusCard>
    );
  }
  if (mode === 'finalizing') {
    return (
      <CenterStatusCard
        icon={<FileText className="size-7" />}
        title={t('center.finalizingTitle')}
        testId="speech-finalizing-stage"
      >
        {hint(t('center.finalizingHint'))}
      </CenterStatusCard>
    );
  }
  if (mode === 'selection') {
    return (
      <CenterStatusCard
        icon={<Hand className="size-7" />}
        title={
          snapshot.free_holder_side
            ? t('center.selectionSide', { side: sideLabel(snapshot.free_holder_side, t) })
            : t('freeDebate')
        }
        testId="free-selection-stage"
      >
        {hint(t('center.selectionHint'))}
      </CenterStatusCard>
    );
  }
  if (mode === 'preparation') {
    return (
      <CenterStatusCard
        icon={<Clock3 className="size-7" />}
        title={t('center.preparingTitle')}
        testId="preparation-stage"
      >
        <CenterTimer
          milliseconds={snapshot.speech_remaining_ms}
          label={t('center.preparationRemaining')}
        />
      </CenterStatusCard>
    );
  }
  if (mode === 'human-ready' || mode === 'speaking') {
    return (
      <div
        className="debate-center-speaker mt-4 flex w-full min-w-0 max-w-full flex-col items-center"
        data-testid="current-speaker-state"
      >
        <SpeakerMark
          large
          active={mode === 'speaking'}
          seat={currentSeat}
          online={
            currentSeat?.user_id
              ? room.members.find((member) => member.user_id === currentSeat.user_id)?.online
              : undefined
          }
        />
        <h3 className="mt-3 max-w-full truncate text-lg font-black text-slate-950">
          {currentSeat?.occupant_name ?? t('center.confirmingSpeaker')}
        </h3>
        {currentSeat ? (
          <p
            className={`mt-1 text-sm font-bold ${currentSeat.side === 'AFFIRMATIVE' ? 'text-red-600' : 'text-blue-700'}`}
          >
            {sideLabel(currentSeat.side, t)} · {currentSeat.seat_no}
          </p>
        ) : null}
        {mode === 'speaking' ? (
          <>
            <Waveform />
            <CenterTimer
              milliseconds={snapshot.speech_remaining_ms}
              label={t('detail.speechTimer')}
            />
          </>
        ) : (
          <p
            className="mt-2 text-sm leading-6 text-slate-600"
            data-testid={handoffPending ? 'handoff-pending-state' : undefined}
          >
            {t(handoffPending ? 'center.handoffHint' : 'center.readyHint')}
          </p>
        )}
      </div>
    );
  }
  return (
    <CenterStatusCard
      icon={<Clock3 className="size-7" />}
      title={t('waitingStage')}
      testId="idle-stage"
    />
  );
}

export function DebatePageLayout({
  matchId,
  room,
  snapshot,
  runtime,
  presentation,
  transcript,
  transcriptLoading,
  transcriptError,
  currentUserId,
  currentSeat,
  isCurrentSpeaker,
  isOrganizer,
  isDebater,
  myHandIndex,
  canRaiseHand: handRequestAllowed,
  audioStatus,
  playbackStatus,
  audioError,
  outputMuted,
  mutedHumanUserIds,
  commandPending,
  leaving,
  editingSpeechId,
  draftText,
  savingSpeechId,
  drawerOpen,
  onCommand,
  onStartSpeech,
  onFinishSpeech,
  onResetSpeech,
  onPause,
  onTerminate,
  onLeave,
  onEnableAudio,
  onToggleOutputMuted,
  onToggleAllHumanOutputMuted,
  onToggleHumanOutputMuted,
  onOpenDrawer,
  onCloseDrawer,
  emergencyOpen = false,
  emergencyControls,
  onOpenEmergency,
  onCloseEmergency,
  onStartRuntime,
  runtimeStartPending = false,
  onEditSpeech,
  onSaveSpeech,
  onDraftTextChange,
  onRetryTranscript,
  networkOpen,
  networkStats,
  onOpenNetwork,
  onCloseNetwork,
  presentationMode = false,
  onTogglePresentation,
}: DebatePageLayoutProps) {
  const t = useAppTranslations('Debate');
  const { locale } = useAppLocale();
  const transcriptTriggerRef = useRef<HTMLButtonElement>(null);
  const networkTriggerRef = useRef<HTMLButtonElement>(null);

  const resolvedNetworkStats = networkStats ?? {
    rttMs: null,
    packetLossPercent: null,
    sampledAt: null,
  };
  const isTerminal = ['FINISHED', 'TERMINATED'].includes(snapshot.status);
  const centerMode = resolveCenterMode(snapshot);
  const canRaiseHand = Boolean(handRequestAllowed && showsFreeDebateBudget(snapshot));
  const canShowControls = !isTerminal;
  const isHostAnnouncing = centerMode === 'host';
  const isWaitingForHost = centerMode === 'waiting';
  const isCountdown = centerMode === 'countdown';
  const transcriptProps = {
    room,
    transcript,
    runtime,
    snapshot,
    currentSeat,
    currentUserId,
    editingSpeechId,
    draftText,
    savingSpeechId,
    onEditSpeech,
    onSaveSpeech,
    onDraftTextChange,
    transcriptLoading,
    transcriptError,
    onRetryTranscript,
  } as const;
  const showRuntimeFault = shouldShowRuntimeFault(snapshot.status, snapshot.error_code);
  const handoffPending = runtime.commandErrorCode === 'asr_finalization_pending';
  const connectionNotice =
    !isTerminal && !showRuntimeFault && !handoffPending ? runtime.socketError || audioError : null;
  const remoteHumanSeats = room.seats.filter(
    (seat) => seat.occupant_type === 'HUMAN' && seat.user_id && seat.user_id !== currentUserId,
  );
  const mutedHumanUserIdSet = new Set(mutedHumanUserIds);
  const mutedRemoteHumanCount = remoteHumanSeats.filter(
    (seat) => seat.user_id && mutedHumanUserIdSet.has(seat.user_id),
  ).length;
  const allRemoteHumansMuted =
    remoteHumanSeats.length > 0 && mutedRemoteHumanCount === remoteHumanSeats.length;
  const someRemoteHumansMuted = mutedRemoteHumanCount > 0 && !allRemoteHumansMuted;
  const runtimeFaultMessage = showRuntimeFault
    ? runtime.resumeReasons.length > 0
      ? t('detail.recoveryRequirements', {
          reasons: runtime.resumeReasons.join(locale === 'en' ? '; ' : '；'),
        })
      : runtimeErrorLabel(snapshot.error_code, t)
    : null;

  return (
    <main
      className={`debate-page ${presentationMode ? 'debate-page--presentation fixed inset-0 z-40 h-dvh min-h-0 px-4 py-3' : 'h-[calc(100dvh-3.85rem-1px)] min-h-0 px-3 py-2'} overflow-hidden text-slate-950 sm:px-4`}
      data-presentation-mode={presentationMode ? 'true' : 'false'}
    >
      <div
        className={`mx-auto flex h-full w-full flex-col ${presentationMode ? '' : 'max-w-[1660px]'}`}
      >
        <div
          className={`grid min-h-0 min-w-0 flex-1 gap-2 ${presentationMode ? 'grid-cols-1' : 'min-[1580px]:grid-cols-[minmax(0,1fr)_22rem]'}`}
          data-testid="debate-content-grid"
        >
          <section className="flex min-h-0 min-w-0 flex-col">
            <div
              className={`debate-topic grid shrink-0 gap-2 rounded-[1.1rem] border border-blue-100 bg-white/85 px-3 py-2 shadow-[0_10px_30px_rgba(44,85,132,0.06)] sm:grid-cols-[9rem_minmax(0,1fr)_auto] sm:items-center sm:px-4 ${presentationMode ? 'bg-white/95' : ''}`}
            >
              <div className="rounded-lg border border-blue-100 bg-[#f7fbff] px-2.5 py-1.5">
                {presentationMode ? (
                  <JixiaLogo compact />
                ) : (
                  <>
                    <p className="text-[10px] font-black tracking-[0.14em] text-slate-600">
                      {t('roomCode')}
                    </p>
                    <p className="font-mono text-base font-black tracking-[0.08em] text-slate-900">
                      {room.code}
                    </p>
                    <p className="truncate text-[10px] font-bold text-blue-700">
                      {String(room.rule.name ?? '')} · {String(room.rule.side_size ?? 1)}v
                      {String(room.rule.side_size ?? 1)}
                    </p>
                  </>
                )}
              </div>
              <div className="min-w-0 px-1 sm:px-3">
                <p className="text-xs font-black tracking-[0.16em] text-blue-600">{t('topic')}</p>
                <h1 className="truncate text-lg font-black tracking-[-0.04em] text-slate-950 xl:text-xl">
                  {String(room.topic.title ?? '')}
                </h1>
              </div>
              <div className="flex items-center gap-2 justify-self-start sm:justify-self-end">
                <span
                  className={`rounded-full px-3 py-1.5 text-xs font-black ${snapshot.status === 'RUNNING' ? 'bg-[#efffc1] text-[#577100]' : snapshot.status === 'PAUSED' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'}`}
                >
                  {matchStatusLabel(snapshot.status, snapshot.action_state, t)}
                </span>
                {!presentationMode ? (
                  <Button
                    className="min-[1580px]:hidden"
                    onClick={onOpenDrawer}
                    ref={transcriptTriggerRef}
                    size="sm"
                    variant="secondary"
                  >
                    <FileText className="size-4" /> {t('transcript')}
                  </Button>
                ) : null}
                {!presentationMode && onTogglePresentation ? (
                  <Button
                    aria-label={t('presentationAria')}
                    onClick={onTogglePresentation}
                    size="sm"
                    variant="secondary"
                  >
                    <Maximize2 className="size-4" />
                    <span className="hidden sm:inline">{t('presentation')}</span>
                  </Button>
                ) : null}
              </div>
            </div>

            <div className="debate-arena mt-2 grid min-h-0 min-w-0 flex-1 grid-cols-[minmax(8.5rem,0.78fr)_minmax(0,1.55fr)_minmax(8.5rem,0.78fr)] overflow-hidden rounded-[1.35rem] border border-blue-100 bg-white/72 shadow-[0_18px_55px_rgba(42,83,130,0.09)] min-[900px]:grid-cols-[minmax(14rem,0.9fr)_minmax(0,1.45fr)_minmax(14rem,0.9fr)]">
              {(['AFFIRMATIVE', 'NEGATIVE'] as const).map((side) => (
                <aside
                  className={`row-start-1 flex min-h-0 min-w-0 flex-col p-2.5 ${side === 'AFFIRMATIVE' ? 'col-start-1 border-r border-blue-100' : 'col-start-3 border-l border-blue-100'}`}
                  key={side}
                >
                  <div
                    className={`grid shrink-0 gap-2 rounded-2xl border px-3 py-3 ${side === 'AFFIRMATIVE' ? 'border-red-100 bg-red-50/60' : 'border-blue-100 bg-blue-50/60'}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div
                        className={`flex items-center gap-2 text-xl font-black xl:text-2xl ${side === 'AFFIRMATIVE' ? 'text-red-700' : 'text-blue-700'}`}
                      >
                        <Flag className="size-5 fill-current" /> {sideLabel(side, t)}
                      </div>
                      <span
                        className={`text-[10px] font-black tracking-[0.18em] ${side === 'AFFIRMATIVE' ? 'text-red-500' : 'text-blue-500'}`}
                      >
                        {t('detail.teamPosition')}
                      </span>
                    </div>
                    <div
                      className={`mt-1 flex min-h-10 items-center justify-center rounded-xl border-l-4 bg-white/75 px-3 py-1.5 text-center shadow-sm ${side === 'AFFIRMATIVE' ? 'border-red-400' : 'border-blue-400'}`}
                    >
                      <p
                        className={`line-clamp-2 break-words text-base font-black leading-6 xl:text-lg ${side === 'AFFIRMATIVE' ? 'text-red-950' : 'text-blue-950'}`}
                        title={
                          side === 'AFFIRMATIVE'
                            ? String(
                                room.topic.affirmative_text ?? t('detail.sideFallbackAffirmative'),
                              )
                            : String(room.topic.negative_text ?? t('detail.sideFallbackNegative'))
                        }
                      >
                        {side === 'AFFIRMATIVE'
                          ? String(
                              room.topic.affirmative_text ?? t('detail.sideFallbackAffirmative'),
                            )
                          : String(room.topic.negative_text ?? t('detail.sideFallbackNegative'))}
                      </p>
                    </div>
                  </div>
                  <div className="mt-2 grid min-h-0 flex-1 auto-rows-fr gap-1.5">
                    {room.seats
                      .filter((seat) => seat.side === side)
                      .map((seat) => {
                        const humanQueueEntry = (snapshot.team_hand_queue ?? []).find(
                          (entry) => entry.user_id && entry.user_id === seat.user_id,
                        );
                        const agentDecision = (snapshot.agent_decisions ?? []).find(
                          (decision) => decision.agent_profile_id === seat.agent_profile_id,
                        );
                        const selectedAgent = Boolean(
                          seat.agent_profile_id &&
                          seat.agent_profile_id === snapshot.current_agent_profile_id,
                        );
                        const experimentAgentStatus = experimentAgentSeatStatus(
                          snapshot,
                          seat,
                          seat.agent_profile_id,
                        );
                        return (
                          <SeatCard
                            key={seat.id}
                            seat={seat}
                            active={
                              ['human-ready', 'speaking', 'agent-preparing'].includes(centerMode) &&
                              seat.id === currentSeat?.id
                            }
                            handOrder={
                              isTerminal
                                ? 0
                                : (humanQueueEntry?.rank ?? agentDecision?.queue_rank ?? 0)
                            }
                            humanSelected={
                              !isTerminal &&
                              Boolean(humanQueueEntry) &&
                              snapshot.action_state === 'HUMAN_READY_TO_START' &&
                              snapshot.current_speaker_user_id === seat.user_id
                            }
                            agentHandOrder={0}
                            agentStatus={
                              isTerminal
                                ? null
                                : experimentAgentStatus
                                  ? experimentAgentStatus
                                  : selectedAgent && snapshot.agent_selection_mode === 'VOLUNTEER'
                                    ? 'SELECTED'
                                    : selectedAgent && snapshot.agent_selection_mode === 'FALLBACK'
                                      ? 'FALLBACK'
                                      : selectedAgent &&
                                          snapshot.agent_selection_mode === 'ALL_AGENT_SKIP_RANDOM'
                                        ? 'FALLBACK'
                                        : agentDecision?.status === 'DECIDING'
                                          ? 'DECIDING'
                                          : agentDecision?.status === 'SKIP'
                                            ? 'SKIP'
                                            : null
                            }
                            online={
                              seat.user_id
                                ? room.members.find((member) => member.user_id === seat.user_id)
                                    ?.online
                                : undefined
                            }
                          />
                        );
                      })}
                  </div>
                </aside>
              ))}

              <section
                className="debate-center-stage relative col-start-2 row-start-1 min-h-0 min-w-0 px-3 py-3 sm:px-5"
                data-testid="debate-center-stage"
              >
                <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_42%,rgba(183,239,0,0.12),transparent_30%),radial-gradient(circle_at_50%_60%,rgba(39,116,255,0.1),transparent_52%)]" />
                <div className="relative z-10 flex h-full min-h-0 flex-col items-center justify-center text-center">
                  <div className="flex items-center gap-2 text-[#6c9100]">
                    <span className="h-0.5 w-10 bg-gradient-to-r from-transparent to-[#b7ef00]" />
                    <Sparkles className="size-4" />
                    <span className="h-0.5 w-10 bg-gradient-to-l from-transparent to-[#b7ef00]" />
                  </div>
                  <p
                    className="mt-2 text-base font-black text-[#587300] sm:text-lg"
                    data-testid="current-debate-stage"
                  >
                    {t('detail.stage')} · {currentStageName(room, snapshot, t, locale)}
                  </p>
                  <h2 className="debate-stage-title mt-1 max-w-xl text-4xl font-black tracking-[-0.06em] text-[#587300] xl:text-5xl">
                    {centerMode === 'frozen'
                      ? t('center.pausedTitle')
                      : handoffPending && centerMode === 'human-ready'
                        ? t('center.handoffTitle')
                        : presentation.title}
                  </h2>
                  <p className="mt-1 text-xs font-black text-slate-500">
                    {centerMode === 'frozen'
                      ? matchStatusLabel(snapshot.status, snapshot.action_state, t)
                      : presentation.eyebrow}
                  </p>
                  <CenterContent
                    mode={centerMode}
                    room={room}
                    snapshot={snapshot}
                    currentSeat={currentSeat}
                    matchId={matchId}
                    isOrganizer={isOrganizer}
                    onStartRuntime={onStartRuntime}
                    runtimeStartPending={runtimeStartPending}
                    handoffPending={handoffPending}
                  />
                  <FreeDebateClock snapshot={snapshot} />
                  {runtimeFaultMessage || connectionNotice ? (
                    <div
                      className={`mt-2 flex max-w-xl items-start gap-2 rounded-xl border px-3 py-2 text-left text-xs ${runtimeFaultMessage ? 'border-red-200 bg-red-50 text-red-700' : 'border-blue-200 bg-blue-50 text-blue-800'}`}
                      role={runtimeFaultMessage ? 'alert' : 'status'}
                    >
                      <CircleAlert className="mt-0.5 size-4 shrink-0" />
                      <span>
                        {runtimeFaultMessage ??
                          t('detail.connectionNotice', { reason: connectionNotice ?? '' })}
                      </span>
                    </div>
                  ) : null}
                </div>
              </section>
            </div>

            {!presentationMode ? (
              <footer className="debate-controls mt-2 grid shrink-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-[1.05rem] border border-blue-100 bg-[#f8fbff] p-2 shadow-[0_10px_30px_rgba(42,83,130,0.07)]">
                <div
                  className="flex items-center gap-2 rounded-xl border border-blue-100 bg-white px-1.5 py-1"
                  data-testid="match-controls-audio"
                >
                  <Button
                    aria-label={outputMuted ? t('restoreSoundAria') : t('muteLocalAria')}
                    aria-pressed={outputMuted}
                    onClick={onToggleOutputMuted}
                    size="sm"
                    variant={outputMuted ? 'secondary' : 'ghost'}
                  >
                    {outputMuted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
                    {outputMuted ? t('restoreSound') : t('muteLocal')}
                  </Button>
                  {isOrganizer && canShowControls ? (
                    <Button
                      aria-label="打开应急控制台"
                      onClick={() => onOpenEmergency?.()}
                      size="sm"
                      variant="secondary"
                    >
                      <ShieldCheck className="size-4" />
                      <span className="hidden sm:inline">应急控制台</span>
                    </Button>
                  ) : null}
                  <DropdownMenu.Root modal={false}>
                    <DropdownMenu.Trigger asChild>
                      <Button
                        aria-label={
                          mutedRemoteHumanCount > 0
                            ? t('detail.mutedHumans', { count: mutedRemoteHumanCount })
                            : t('humanAudioSettings')
                        }
                        size="sm"
                        variant={mutedRemoteHumanCount > 0 ? 'secondary' : 'ghost'}
                      >
                        {allRemoteHumansMuted ? (
                          <VolumeX className="size-4" />
                        ) : (
                          <UsersRound className="size-4" />
                        )}
                        <span className="hidden sm:inline">{t('humanAudio')}</span>
                        {mutedRemoteHumanCount > 0 ? (
                          <span className="rounded-full bg-slate-900 px-1.5 py-0.5 text-[10px] font-black text-white">
                            {mutedRemoteHumanCount}
                          </span>
                        ) : null}
                      </Button>
                    </DropdownMenu.Trigger>
                    <DropdownMenu.Portal>
                      <DropdownMenu.Content
                        align="start"
                        className="z-50 w-[min(20rem,calc(100vw-1.5rem))] rounded-lg border border-blue-100 bg-white p-1.5 shadow-xl"
                        collisionPadding={12}
                        sideOffset={8}
                      >
                        <div className="px-2.5 pb-2 pt-1.5">
                          <p className="text-sm font-black text-slate-950">{t('humanAudio')}</p>
                          <p className="mt-0.5 text-xs leading-5 text-slate-500">
                            {t('detail.humanAudioHint')}
                          </p>
                        </div>
                        {remoteHumanSeats.length > 0 ? (
                          <>
                            <DropdownMenu.CheckboxItem
                              checked={
                                allRemoteHumansMuted
                                  ? true
                                  : someRemoteHumansMuted
                                    ? 'indeterminate'
                                    : false
                              }
                              className="relative flex cursor-pointer select-none items-center rounded-md px-9 py-2 text-sm font-bold text-slate-800 outline-none data-[highlighted]:bg-blue-50"
                              onCheckedChange={onToggleAllHumanOutputMuted}
                              onSelect={(event) => event.preventDefault()}
                            >
                              <DropdownMenu.ItemIndicator className="absolute left-3 grid size-4 place-items-center text-blue-700">
                                {someRemoteHumansMuted ? (
                                  <span className="h-0.5 w-3 rounded-full bg-current" />
                                ) : (
                                  <Check className="size-4" />
                                )}
                              </DropdownMenu.ItemIndicator>
                              {t('muteAllHumans')}
                            </DropdownMenu.CheckboxItem>
                            <DropdownMenu.Separator className="my-1 h-px bg-slate-100" />
                            {remoteHumanSeats.map((seat) => (
                              <DropdownMenu.CheckboxItem
                                checked={Boolean(
                                  seat.user_id && mutedHumanUserIdSet.has(seat.user_id),
                                )}
                                className="relative flex cursor-pointer select-none items-center justify-between gap-3 rounded-md py-2 pl-9 pr-3 text-sm outline-none data-[highlighted]:bg-blue-50"
                                key={seat.user_id}
                                onCheckedChange={() =>
                                  seat.user_id && onToggleHumanOutputMuted(seat.user_id)
                                }
                                onSelect={(event) => event.preventDefault()}
                              >
                                <DropdownMenu.ItemIndicator className="absolute left-3 grid size-4 place-items-center text-blue-700">
                                  <Check className="size-4" />
                                </DropdownMenu.ItemIndicator>
                                <span className="min-w-0 truncate font-bold text-slate-900">
                                  {seat.occupant_name ?? t('detail.humanDebater')}
                                </span>
                                <span
                                  className={`shrink-0 text-xs font-bold ${seat.side === 'AFFIRMATIVE' ? 'text-red-600' : 'text-blue-700'}`}
                                >
                                  {sideLabel(seat.side, t)} {seat.seat_no}
                                </span>
                              </DropdownMenu.CheckboxItem>
                            ))}
                          </>
                        ) : (
                          <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
                            {t('noOtherHumans')}
                          </p>
                        )}
                      </DropdownMenu.Content>
                    </DropdownMenu.Portal>
                  </DropdownMenu.Root>
                  {playbackStatus === 'blocked' ? (
                    <Button onClick={onEnableAudio} size="sm" variant="primary">
                      <Radio className="size-4" /> {t('enableMatchAudio')}
                    </Button>
                  ) : null}
                </div>
                <div
                  className="flex min-w-0 flex-nowrap items-center justify-center gap-1 overflow-x-auto rounded-xl border border-[#dce9f5] bg-white px-2 py-1"
                  data-testid="match-controls-speech"
                >
                  {canShowControls &&
                  snapshot.action_state === 'HUMAN_READY_TO_START' &&
                  isCurrentSpeaker ? (
                    <Button
                      disabled={commandPending || audioStatus !== 'ready' || !runtime.commandReady}
                      onClick={onStartSpeech}
                      size="sm"
                      variant="primary"
                    >
                      <Mic className="size-4" /> {t('startSpeech')}
                    </Button>
                  ) : null}
                  {canShowControls &&
                  snapshot.action_state === 'HUMAN_SPEAKING' &&
                  isCurrentSpeaker ? (
                    <Button
                      className="gap-1 px-2"
                      disabled={commandPending}
                      onClick={onFinishSpeech}
                      size="sm"
                      variant="danger"
                    >
                      <Square className="size-4 fill-current" /> {t('finishSpeech')}
                    </Button>
                  ) : null}
                  {canRaiseHand ? (
                    <Button
                      disabled={commandPending}
                      onClick={() => onCommand(myHandIndex >= 0 ? 'hand.cancel' : 'hand.raise')}
                      size="sm"
                      variant={myHandIndex >= 0 ? 'secondary' : 'primary'}
                    >
                      <Hand className="size-4" />{' '}
                      {myHandIndex >= 0
                        ? t('cancelHand', { position: myHandIndex + 1 })
                        : t('raiseHand')}
                    </Button>
                  ) : null}
                  {canShowControls &&
                  (((isCurrentSpeaker || isOrganizer) &&
                    ['HUMAN_SPEAKING', 'SPEECH_FINALIZING'].includes(snapshot.action_state)) ||
                    (isOrganizer &&
                      ['AGENT_PREPARING', 'AGENT_SPEAKING', 'AGENT_FINALIZING'].includes(
                        snapshot.action_state,
                      ))) ? (
                    <Button
                      className="gap-1 px-2"
                      disabled={commandPending}
                      onClick={onResetSpeech}
                      size="sm"
                      variant="secondary"
                    >
                      <RotateCcw className="size-4" /> {t('resetSpeech')}
                    </Button>
                  ) : null}
                  {!isTerminal &&
                  !(
                    (canShowControls &&
                      snapshot.action_state === 'HUMAN_READY_TO_START' &&
                      isCurrentSpeaker) ||
                    (canShowControls &&
                      snapshot.action_state === 'HUMAN_SPEAKING' &&
                      isCurrentSpeaker) ||
                    canRaiseHand ||
                    (canShowControls &&
                      (((isCurrentSpeaker || isOrganizer) &&
                        ['HUMAN_SPEAKING', 'SPEECH_FINALIZING'].includes(snapshot.action_state)) ||
                        (isOrganizer &&
                          ['AGENT_PREPARING', 'AGENT_SPEAKING', 'AGENT_FINALIZING'].includes(
                            snapshot.action_state,
                          ))))
                  ) ? (
                    <span className="px-2 text-xs font-bold text-slate-600">
                      {isHostAnnouncing
                        ? t('waitingHost')
                        : snapshot.action_state === 'RESUME_COUNTDOWN'
                          ? t('waitingResume')
                          : isCountdown
                            ? t('waitingCountdown')
                            : t('waitingStage')}
                    </span>
                  ) : null}
                </div>
                <div
                  className="flex items-center justify-end gap-1.5 rounded-xl border border-blue-100 bg-white px-1.5 py-1"
                  data-testid="match-controls-system"
                >
                  <Button
                    aria-label={t('network')}
                    onClick={onOpenNetwork}
                    ref={networkTriggerRef}
                    size="sm"
                    variant="ghost"
                  >
                    <Wifi className="size-4" />
                    {runtime.socketStatus === 'open' && audioStatus === 'ready'
                      ? t('networkGood')
                      : t('connectionCheck')}
                  </Button>
                  {canPauseMatch(snapshot.status, isDebater, isOrganizer) ? (
                    <Button
                      disabled={commandPending}
                      onClick={onPause}
                      size="sm"
                      variant="secondary"
                    >
                      <Pause className="size-4" /> {t('pause')}
                    </Button>
                  ) : null}
                  {(isOrganizer || snapshot.pause_initiator_user_id === currentUserId) &&
                  ['PAUSED', 'SYSTEM_RECOVERY', 'ERROR'].includes(snapshot.status) &&
                  snapshot.action_state !== 'RESUME_COUNTDOWN' ? (
                    <Button
                      disabled={commandPending}
                      onClick={() => onCommand('match.resume')}
                      size="sm"
                      variant="primary"
                    >
                      <Play className="size-4" /> {t('resume')}
                    </Button>
                  ) : null}
                  {isOrganizer && canShowControls ? (
                    <Button
                      disabled={commandPending}
                      onClick={onTerminate}
                      size="sm"
                      variant="danger"
                    >
                      <CircleAlert className="size-4" /> {t('terminate')}
                    </Button>
                  ) : null}
                  <Button disabled={leaving} onClick={onLeave} size="sm" variant="ghost">
                    <ArrowLeft className="size-4" /> {t('leave')}
                  </Button>
                </div>
              </footer>
            ) : null}
            <Dialog.Root
              open={Boolean(networkOpen)}
              onOpenChange={(nextOpen) => {
                if (!nextOpen) onCloseNetwork?.();
              }}
            >
              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/20 p-4 backdrop-blur-[2px]" />
                <Dialog.Content
                  aria-describedby={undefined}
                  aria-labelledby="network-status-title"
                  className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-blue-100 bg-white p-5 shadow-2xl outline-none"
                  onCloseAutoFocus={(event) => {
                    if (networkTriggerRef.current?.isConnected) {
                      event.preventDefault();
                      networkTriggerRef.current.focus();
                    }
                  }}
                >
                  <div className="flex items-center justify-between gap-3">
                    <Dialog.Title asChild>
                      <h2 id="network-status-title" className="text-lg font-black">
                        {t('network')}
                      </h2>
                    </Dialog.Title>
                    <Dialog.Close asChild>
                      <Button size="icon" variant="ghost" aria-label={t('closeNetwork')}>
                        <X className="size-4" />
                      </Button>
                    </Dialog.Close>
                  </div>
                  <dl className="mt-5 grid gap-3 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-slate-500">{t('matchConnection')}</dt>
                      <dd className="font-bold">
                        {runtime.socketStatus === 'open' ? t('connected') : t('connecting')}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-500">{t('audioConnection')}</dt>
                      <dd className="font-bold">
                        {audioStatus === 'ready' ? t('connected') : t('connecting')}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-500">{t('playback')}</dt>
                      <dd className="font-bold">
                        {playbackStatus === 'blocked' ? t('playbackBlocked') : t('playbackReady')}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-500">{t('latency')}</dt>
                      <dd className="font-bold">
                        {resolvedNetworkStats.rttMs === null
                          ? t('noData')
                          : `${Math.round(resolvedNetworkStats.rttMs)} ms`}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-500">{t('packetLoss')}</dt>
                      <dd className="font-bold">
                        {resolvedNetworkStats.packetLossPercent === null
                          ? t('noData')
                          : `${resolvedNetworkStats.packetLossPercent.toFixed(1)}%`}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-500">{t('lastUpdated')}</dt>
                      <dd className="font-bold">
                        {resolvedNetworkStats.sampledAt === null
                          ? t('noData')
                          : new Date(resolvedNetworkStats.sampledAt).toLocaleTimeString(locale)}
                      </dd>
                    </div>
                  </dl>
                  <p className="mt-5 text-xs leading-5 text-slate-500">{t('networkNote')}</p>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          </section>

          {!presentationMode ? (
            <aside className="debate-transcript hidden min-h-0 min-[1580px]:flex min-w-0 flex-col overflow-hidden rounded-[1.35rem] border border-blue-100 bg-white/88 shadow-[0_18px_55px_rgba(42,83,130,0.09)]">
              <div className="shrink-0 border-b border-blue-100 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-[10px] font-black tracking-[0.18em] text-blue-600">
                      TRANSCRIPT
                    </p>
                    <h2 className="mt-1 text-lg font-black">{t('transcript')}</h2>
                  </div>
                  <span className="rounded-full bg-blue-50 px-2.5 py-1 text-[10px] font-black text-blue-700">
                    {t('transcriptLive')}
                  </span>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3">
                <TranscriptFeed {...transcriptProps} />
              </div>
            </aside>
          ) : null}
        </div>
      </div>

      <Dialog.Root
        open={drawerOpen}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) onCloseDrawer();
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/20 backdrop-blur-[2px]" />
          <Dialog.Content
            aria-describedby={undefined}
            aria-labelledby="transcript-drawer-title"
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-xl flex-col border-l border-blue-100 bg-[#fbfdff] shadow-2xl outline-none"
            onCloseAutoFocus={(event) => {
              if (transcriptTriggerRef.current?.isConnected) {
                event.preventDefault();
                transcriptTriggerRef.current.focus();
              }
            }}
          >
            <div className="flex items-center justify-between border-b border-blue-100 px-6 py-5">
              <div>
                <p className="text-xs font-black tracking-[0.18em] text-blue-600">TRANSCRIPT</p>
                <Dialog.Title asChild>
                  <h2 className="mt-1 text-xl font-black" id="transcript-drawer-title">
                    {t('transcript')}
                  </h2>
                </Dialog.Title>
              </div>
              <Dialog.Close asChild>
                <Button aria-label={t('closeTranscript')} size="icon" variant="ghost">
                  <X className="size-5" />
                </Button>
              </Dialog.Close>
            </div>
            <div
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 py-5"
              data-testid="transcript-drawer-scroll"
            >
              <TranscriptFeed {...transcriptProps} />
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root
        open={emergencyOpen}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) onCloseEmergency?.();
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/25 backdrop-blur-[2px]" />
          <Dialog.Content
            aria-describedby={undefined}
            aria-labelledby="emergency-console-title"
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-sm flex-col overflow-y-auto overscroll-contain border-l border-amber-200 bg-[#fffdf7] p-5 shadow-2xl outline-none"
          >
            <div className="flex items-center justify-between gap-3 border-b border-amber-100 pb-4">
              <div>
                <p className="text-xs font-black tracking-[0.18em] text-amber-700">HOST CONSOLE</p>
                <Dialog.Title asChild>
                  <h2 id="emergency-console-title" className="mt-1 text-xl font-black">
                    应急控制台
                  </h2>
                </Dialog.Title>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="关闭应急控制台" size="icon" variant="ghost">
                  <X className="size-5" />
                </Button>
              </Dialog.Close>
            </div>
            <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
              所有操作由服务端确认并串行进入比赛状态机。发生异常时先暂停，再按需恢复或重置当前发言。
            </p>
            <div className="mt-5 grid gap-2">
              {isWaitingForHost ? (
                <Button
                  disabled={runtimeStartPending}
                  onClick={() => onStartRuntime?.()}
                  variant="primary"
                >
                  {runtimeStartPending ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Play className="size-4" />
                  )}
                  正式开始比赛
                </Button>
              ) : null}
              {canPauseMatch(snapshot.status, isDebater, isOrganizer) ? (
                <Button disabled={commandPending} onClick={onPause} variant="secondary">
                  <Pause className="size-4" />
                  暂停比赛
                </Button>
              ) : null}
              {(isOrganizer || snapshot.pause_initiator_user_id === currentUserId) &&
              ['PAUSED', 'SYSTEM_RECOVERY', 'ERROR'].includes(snapshot.status) &&
              snapshot.action_state !== 'RESUME_COUNTDOWN' ? (
                <Button
                  disabled={commandPending}
                  onClick={() => onCommand('match.resume')}
                  variant="primary"
                >
                  <Play className="size-4" />
                  恢复比赛
                </Button>
              ) : null}
              {isOrganizer &&
              canShowControls &&
              !emergencyControls &&
              [
                'HUMAN_SPEAKING',
                'SPEECH_FINALIZING',
                'AGENT_PREPARING',
                'AGENT_SPEAKING',
                'AGENT_FINALIZING',
              ].includes(snapshot.action_state) ? (
                <Button disabled={commandPending} onClick={onResetSpeech} variant="secondary">
                  <RotateCcw className="size-4" />
                  重置当前发言
                </Button>
              ) : null}
              {isOrganizer && canShowControls ? (
                <Button disabled={commandPending} onClick={onTerminate} variant="danger">
                  <CircleAlert className="size-4" />
                  终止比赛
                </Button>
              ) : null}
            </div>
            {emergencyControls}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </main>
  );
}
