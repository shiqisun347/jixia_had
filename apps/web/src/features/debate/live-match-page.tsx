'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, ClipboardCheck, LoaderCircle } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { buttonVariants } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { useCurrentUser } from '@/features/auth/use-auth';
import { ApiClientError } from '@/lib/auth-api';
import { matchesApi } from '@/lib/matches-api';
import { roomsApi, type RoomSnapshot } from '@/lib/rooms-api';
import { surveyApi } from '@/lib/experiments-api';
import { useSubmissionGate } from '@/lib/use-submission-gate';
import { useAppTranslations } from '@/i18n';

import { canPauseMatch, DebatePageLayout } from './debate-page-layout';
import {
  classifyRemoteAudioSource,
  humanAudioMuteStorageKey,
  parseMutedHumanUserIds,
  serializeMutedHumanUserIds,
  shouldMuteRemoteAudio,
  type RemoteAudioSource,
} from './match-audio-playback';
import { shouldConnectMatchAudio } from './match-audio-policy';
import { resolveCurrentSeat } from './match-presentation';
import { useMatchRuntime } from './use-match-runtime';
import { shouldRestoreMicrophone, useMatchCommand } from './use-match-command';
import { useSmoothMatchSnapshot } from './use-smooth-match-snapshot';

interface MatchAudioSession {
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  enableAudio(): Promise<void>;
  canPlaybackAudio?: boolean;
  setOutputMuted?(muted: boolean): void;
  setMutedHumanUserIds?(userIds: readonly string[]): void;
  disconnect(): void;
  getNetworkStats?: () => Promise<{ rttMs: number | null; packetLossPercent: number | null }>;
}

declare global {
  interface Window {
    __JX_MATCH_AUDIO_OVERRIDE__?: () => Promise<MatchAudioSession>;
  }
}

function canUseLocalAudioOverride(): boolean {
  return window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
}

function errorText(error: unknown, fallback = '比赛连接暂时不可用，请刷新后重试。'): string {
  return error instanceof ApiClientError ? error.message : fallback;
}

export function transcriptSaveErrorText(error: unknown, fallback = '保存失败，请稍后重试。'): string {
  return error instanceof ApiClientError ? error.message : fallback;
}

export function normalizedTranscriptDraft(draft: string): string | null {
  const normalized = draft.trim();
  return normalized ? normalized : null;
}

export function canManageMatch(
  room: RoomSnapshot | undefined,
  userId: string | undefined,
  role: string | undefined,
): boolean {
  if (!room || !userId) return false;
  if (room.organizer_user_id === userId) return true;
  return Boolean(
    room.experiment_mode && (room.viewer_is_experiment_controller || role === 'ADMIN'),
  );
}

export function shouldShowPostmatchPrompt(
  matchStatus: string | undefined,
  isDebater: boolean,
  taskPending: boolean,
  dismissed: boolean,
): boolean {
  return matchStatus === 'FINISHED' && isDebater && taskPending && !dismissed;
}

type MatchConfirmation = 'finish' | 'pause' | 'reset' | 'terminate' | 'leave';

function confirmationCopy(t: ReturnType<typeof useAppTranslations>): Record<
  MatchConfirmation,
  { title: string; description: string; confirmLabel: string }
> {
  return {
  finish: {
    title: t('dialog.finishTitle'), description: t('dialog.finishDescription'), confirmLabel: t('dialog.finishConfirm'),
  },
  pause: {
    title: t('dialog.pauseTitle'), description: t('dialog.pauseDescription'), confirmLabel: t('dialog.pauseConfirm'),
  },
  reset: {
    title: t('dialog.resetTitle'), description: t('dialog.resetDescription'), confirmLabel: t('dialog.resetConfirm'),
  },
  terminate: {
    title: t('dialog.terminateTitle'), description: t('dialog.terminateDescription'), confirmLabel: t('terminate'),
  },
  leave: {
    title: t('dialog.leaveTitle'), description: t('dialog.leaveDescription'), confirmLabel: t('dialog.leaveConfirm'),
  },
  };
}

export function actionLabel(
  actionState: string,
  isCurrentSpeaker = true,
  currentSpeakerLabel = '当前辩手',
  translate?: (key: string, values?: Record<string, string>) => string,
): {
  eyebrow: string;
  title: string;
  detail: string;
} {
  const t = translate ?? ((key: string, values?: Record<string, string>) => {
    if (key === 'humanReady.otherTitle') return `轮到${values?.speaker ?? currentSpeakerLabel}发言`;
    const legacy: Record<string, string> = {
      'hostAnnouncing.eyebrow': '赛制播报', 'hostAnnouncing.title': '主持音频播放中', 'hostAnnouncing.detail': '播报结束后进入当前阶段。',
      'humanReady.eyebrow': '当前发言席位已就绪', 'humanReady.ownTitle': '轮到你发言了！', 'humanReady.ownDetail': '点击开始后才会开启麦克风并启动正式计时。', 'humanReady.otherDetail': '当前发言者开始后，其他辩手请保持关注。',
      'humanSpeaking.eyebrow': '实时发言中', 'humanSpeaking.title': '麦克风已开启', 'humanSpeaking.detail': '服务端正在控制发言时长，你可以提前结束。',
      'finalizing.eyebrow': '文字整理中', 'finalizing.title': '正在整理文字记录', 'finalizing.detail': '发言已经结束，正在等待 ASR 最终结果。',
      'agentPreparing.eyebrow': 'Agent 准备中', 'agentPreparing.title': 'Agent 正在思考中', 'agentPreparing.detail': '正在生成正式发言并建立实时语音，等待时间不计入发言时长。',
      'agentSpeaking.eyebrow': 'Agent 实时发言', 'agentSpeaking.title': 'Agent 正在发言', 'agentSpeaking.detail': '语音正在实时播放，文字记录同步更新。',
      'agentFinalizing.eyebrow': 'Agent 发言收尾', 'agentFinalizing.title': '正在提交正式记录', 'agentFinalizing.detail': '系统正在确认实际播放文字和音频文件。',
      'preparing.eyebrow': '准备阶段', 'preparing.title': '准备时间进行中', 'preparing.detail': '倒计时结束后自动进入下一动作。',
      'finished.eyebrow': '比赛结束', 'finished.title': '本场辩论已完成', 'finished.detail': '完整文字记录已经归档，AI 裁判正在生成或已经完成评分。',
      'recovery.eyebrow': '系统恢复保护', 'recovery.title': '比赛已安全暂停', 'recovery.detail': '计时与实时语音均已冻结；满足在线和设备条件后可以申请恢复。',
      'selecting.eyebrow': '自由辩论候选中', 'selecting.title': '申请下一次发言', 'selecting.detail': '人类举手优先；无人举手时由本方 Agent 独立决策并选择发言者。',
      'resuming.eyebrow': '比赛即将恢复', 'resuming.title': '3 秒后恢复比赛', 'resuming.detail': '请保持设备连接，当前发言将从安全起点重新开始。',
      'starting.eyebrow': '比赛启动', 'starting.title': '正在建立实时状态', 'starting.detail': '请保持页面打开。',
    };
    return legacy[key] ?? key;
  });
  switch (actionState) {
    case 'HOST_ANNOUNCING':
      return { eyebrow: t('hostAnnouncing.eyebrow'), title: t('hostAnnouncing.title'), detail: t('hostAnnouncing.detail') };
    case 'HUMAN_READY_TO_START':
      return {
        eyebrow: t('humanReady.eyebrow'),
        title: isCurrentSpeaker ? t('humanReady.ownTitle') : t('humanReady.otherTitle', { speaker: currentSpeakerLabel }),
        detail: isCurrentSpeaker ? t('humanReady.ownDetail') : t('humanReady.otherDetail'),
      };
    case 'HUMAN_SPEAKING':
      return { eyebrow: t('humanSpeaking.eyebrow'), title: t('humanSpeaking.title'), detail: t('humanSpeaking.detail') };
    case 'SPEECH_FINALIZING':
      return { eyebrow: t('finalizing.eyebrow'), title: t('finalizing.title'), detail: t('finalizing.detail') };
    case 'AGENT_PREPARING':
      return { eyebrow: t('agentPreparing.eyebrow'), title: t('agentPreparing.title'), detail: t('agentPreparing.detail') };
    case 'AGENT_SPEAKING':
      return { eyebrow: t('agentSpeaking.eyebrow'), title: t('agentSpeaking.title'), detail: t('agentSpeaking.detail') };
    case 'AGENT_FINALIZING':
      return { eyebrow: t('agentFinalizing.eyebrow'), title: t('agentFinalizing.title'), detail: t('agentFinalizing.detail') };
    case 'PREPARING':
      return { eyebrow: t('preparing.eyebrow'), title: t('preparing.title'), detail: t('preparing.detail') };
    case 'MATCH_FINISHED':
      return { eyebrow: t('finished.eyebrow'), title: t('finished.title'), detail: t('finished.detail') };
    case 'RECOVERY_REQUIRED':
      return { eyebrow: t('recovery.eyebrow'), title: t('recovery.title'), detail: t('recovery.detail') };
    case 'FREE_SELECTING':
      return { eyebrow: t('selecting.eyebrow'), title: t('selecting.title'), detail: t('selecting.detail') };
    case 'RESUME_COUNTDOWN':
      return { eyebrow: t('resuming.eyebrow'), title: t('resuming.title'), detail: t('resuming.detail') };
    default:
      return { eyebrow: t('starting.eyebrow'), title: t('starting.title'), detail: t('starting.detail') };
  }
}

export function terminalPresentation(
  status: string,
  translate?: (key: string) => string,
): ReturnType<typeof actionLabel> | null {
  const t = translate ?? ((key: string) => ({
    'terminated.eyebrow': '比赛终止',
    'terminated.title': '本场比赛已终止',
    'terminated.detail': '比赛已由房主终止，不能继续发言；现有文字记录仍可查看。',
  })[key] ?? key);
  if (status === 'TERMINATED') {
    return {
      eyebrow: t('terminated.eyebrow'),
      title: t('terminated.title'),
      detail: t('terminated.detail'),
    };
  }
  return status === 'FINISHED' ? actionLabel('MATCH_FINISHED', true, '当前辩手', translate) : null;
}

export function LiveMatchPage({ matchId }: Readonly<{ matchId: string }>) {
  const matchT = useAppTranslations('Match');
  const debateT = useAppTranslations('Debate');
  const debateTRef = useRef(debateT);
  useEffect(() => {
    debateTRef.current = debateT;
  }, [debateT]);
  const router = useRouter();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const currentUser = useCurrentUser();
  const runtime = useMatchRuntime(matchId);
  const roomQuery = useQuery({
    queryKey: ['rooms', runtime.snapshot?.room_id, 'snapshot'],
    queryFn: () => roomsApi.snapshot(runtime.snapshot?.room_id ?? ''),
    enabled: Boolean(runtime.snapshot?.room_id),
    staleTime: 30_000,
  });
  const transcriptQuery = useQuery({
    queryKey: ['matches', matchId, 'transcript'],
    queryFn: () => matchesApi.transcript(matchId),
    enabled: Boolean(runtime.snapshot),
    staleTime: Number.POSITIVE_INFINITY,
  });
  const audioSessionRef = useRef<MatchAudioSession | null>(null);
  // Transport readiness controls microphone commands; playback consent is independent.
  const [audioStatus, setAudioStatus] = useState<'connecting' | 'ready' | 'error'>('connecting');
  const [playbackStatus, setPlaybackStatus] = useState<'ready' | 'blocked'>('ready');
  const [audioError, setAudioError] = useState<string | null>(null);
  const [outputMuted, setOutputMuted] = useState(false);
  const outputMutedRef = useRef(false);
  const [mutedHumanUserIds, setMutedHumanUserIds] = useState<string[]>(() => {
    if (typeof window === 'undefined') return [];
    return parseMutedHumanUserIds(window.sessionStorage.getItem(humanAudioMuteStorageKey(matchId)));
  });
  const mutedHumanUserIdsRef = useRef(new Set(mutedHumanUserIds));
  const knownHumanUserIdsRef = useRef<string[]>([]);
  const [networkStats, setNetworkStats] = useState<{
    rttMs: number | null;
    packetLossPercent: number | null;
    sampledAt: number | null;
  }>({ rttMs: null, packetLossPercent: null, sampledAt: null });
  const [networkOpen, setNetworkOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingSpeechId, setEditingSpeechId] = useState<string | null>(null);
  const [draftText, setDraftText] = useState('');
  const [savingSpeechId, setSavingSpeechId] = useState<string | null>(null);
  const savingSpeechRef = useRef(false);
  const [leaving, setLeaving] = useState(false);
  const [confirmation, setConfirmation] = useState<MatchConfirmation | null>(null);
  const [confirmationPending, setConfirmationPending] = useState(false);
  const [postmatchPromptDismissed, setPostmatchPromptDismissed] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.sessionStorage.getItem(`jx:postmatch-prompt:${matchId}`) === '1',
  );
  const confirmationGate = useSubmissionGate();
  const hostAudioRef = useRef<HTMLAudioElement | null>(null);
  const [hostAudioMounted, setHostAudioMounted] = useState(false);
  const setHostAudioRef = useCallback((element: HTMLAudioElement | null) => {
    hostAudioRef.current = element;
    setHostAudioMounted(Boolean(element));
  }, []);
  const presenceRefreshEpochRef = useRef<number | null>(null);
  const snapshot = runtime.snapshot;
  const shouldConnectAudio = shouldConnectMatchAudio(snapshot?.status);
  const terminal = Boolean(snapshot && !shouldConnectAudio);

  useEffect(() => {
    knownHumanUserIdsRef.current =
      roomQuery.data?.seats.flatMap((seat) =>
        seat.occupant_type === 'HUMAN' && seat.user_id ? [seat.user_id] : [],
      ) ?? [];
  }, [roomQuery.data?.seats]);

  useEffect(() => {
    if (
      runtime.connectionEpoch === null ||
      !runtime.snapshot?.room_id ||
      !roomQuery.isSuccess ||
      presenceRefreshEpochRef.current === runtime.connectionEpoch
    ) {
      return;
    }
    presenceRefreshEpochRef.current = runtime.connectionEpoch;
    void roomQuery.refetch();
  }, [roomQuery, runtime.connectionEpoch, runtime.snapshot?.room_id]);

  useEffect(() => {
    if (terminal || audioStatus !== 'ready') return;
    let cancelled = false;
    const sample = async () => {
      const session = audioSessionRef.current;
      if (!session) return;
      const next = session.getNetworkStats
        ? await session
            .getNetworkStats()
            .then((value) => value ?? { rttMs: null, packetLossPercent: null })
            .catch(() => ({ rttMs: null, packetLossPercent: null }))
        : { rttMs: null, packetLossPercent: null };
      if (!cancelled) setNetworkStats({ ...next, sampledAt: Date.now() });
    };
    void sample();
    const timer = window.setInterval(() => void sample(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [audioStatus, terminal]);

  useEffect(() => {
    if (!shouldConnectAudio || runtime.connectionEpoch === null || !roomQuery.isSuccess) {
      if (terminal) {
        const session = audioSessionRef.current;
        if (session) {
          void session.setMicrophoneEnabled(false);
          session.disconnect();
          audioSessionRef.current = null;
        }
      }
      return;
    }
    let cancelled = false;
    void (async () => {
      setAudioStatus('connecting');
      setPlaybackStatus('ready');
      setAudioError(null);
      try {
        if (canUseLocalAudioOverride() && window.__JX_MATCH_AUDIO_OVERRIDE__) {
          const session = await window.__JX_MATCH_AUDIO_OVERRIDE__();
          if (cancelled) {
            void session.setMicrophoneEnabled(false);
            session.disconnect();
          } else {
            audioSessionRef.current = session;
            if (outputMutedRef.current) session.setOutputMuted?.(true);
            if (mutedHumanUserIdsRef.current.size > 0) {
              session.setMutedHumanUserIds?.([...mutedHumanUserIdsRef.current]);
            }
            setAudioStatus('ready');
            if (session.canPlaybackAudio === false) {
              setPlaybackStatus('blocked');
              setAudioError(debateTRef.current('dialog.audioBlocked'));
            }
          }
        } else {
          const [{ Room, RoomEvent, Track }, token] = await Promise.all([
            import('livekit-client'),
            matchesApi.liveKitToken(matchId, runtime.connectionEpoch),
          ]);
          const room = new Room({ adaptiveStream: true, dynacast: true });
          const attachedAudioElements = new Set<HTMLMediaElement>();
          const attachedElementsByTrack = new Map<
            { detach: () => HTMLMediaElement[] },
            { elements: Set<HTMLMediaElement>; source: RemoteAudioSource }
          >();
          let currentMutedHumanUserIds = new Set(mutedHumanUserIdsRef.current);
          let playbackBlocked = false;
          const markPlaybackBlocked = () => {
            playbackBlocked = true;
            if (!cancelled) {
              setPlaybackStatus('blocked');
              setAudioError(debateTRef.current('dialog.audioBlocked'));
            }
          };
          const attachAudio = (
            track: {
              kind: string;
              attach: () => HTMLMediaElement;
              detach: () => HTMLMediaElement[];
            },
            _publication: unknown,
            participant: { identity: string },
          ) => {
            if (track.kind !== Track.Kind.Audio) return;
            const source = classifyRemoteAudioSource(
              participant.identity,
              matchId,
              knownHumanUserIdsRef.current,
            );
            const element = track.attach();
            element.autoplay = true;
            element.setAttribute('playsinline', 'true');
            element.volume = 1;
            element.muted = shouldMuteRemoteAudio(
              outputMutedRef.current,
              source,
              currentMutedHumanUserIds,
            );
            element.style.display = 'none';
            document.body.appendChild(element);
            attachedAudioElements.add(element);
            attachedElementsByTrack.set(track, { elements: new Set([element]), source });
            void element.play().catch(markPlaybackBlocked);
          };
          const detachAudio = (track: { detach: () => HTMLMediaElement[] }) => {
            // RemoteTrackPublication detaches its elements before emitting
            // TrackUnsubscribed when a remote media track ends. Keep our own
            // references so ended audio elements are removed even after the
            // SDK has already cleared track.attachedElements.
            const elements =
              attachedElementsByTrack.get(track)?.elements ?? new Set<HTMLMediaElement>();
            for (const element of elements) {
              attachedAudioElements.delete(element);
              element.remove();
            }
            attachedElementsByTrack.delete(track);
            track.detach();
          };
          room.on(RoomEvent.TrackSubscribed, attachAudio);
          room.on(RoomEvent.TrackUnsubscribed, detachAudio);
          await room.connect(token.server_url, token.participant_token);
          if (cancelled) {
            room.off(RoomEvent.TrackSubscribed, attachAudio);
            room.off(RoomEvent.TrackUnsubscribed, detachAudio);
            for (const track of attachedElementsByTrack.keys()) track.detach();
            for (const element of attachedAudioElements) element.remove();
            attachedAudioElements.clear();
            attachedElementsByTrack.clear();
            await room.disconnect();
            return;
          }
          audioSessionRef.current = {
            setMicrophoneEnabled: (enabled) =>
              room.localParticipant.setMicrophoneEnabled(enabled).then(() => undefined),
            enableAudio: async () => {
              await room.startAudio();
              await Promise.allSettled([...attachedAudioElements].map((element) => element.play()));
              playbackBlocked = false;
            },
            setOutputMuted: (muted) => {
              for (const { elements, source } of attachedElementsByTrack.values()) {
                for (const element of elements) {
                  element.muted = shouldMuteRemoteAudio(muted, source, currentMutedHumanUserIds);
                }
              }
            },
            setMutedHumanUserIds: (userIds) => {
              currentMutedHumanUserIds = new Set(userIds);
              for (const { elements, source } of attachedElementsByTrack.values()) {
                for (const element of elements) {
                  element.muted = shouldMuteRemoteAudio(
                    outputMutedRef.current,
                    source,
                    currentMutedHumanUserIds,
                  );
                }
              }
            },
            disconnect: () => {
              room.off(RoomEvent.TrackSubscribed, attachAudio);
              room.off(RoomEvent.TrackUnsubscribed, detachAudio);
              for (const track of attachedElementsByTrack.keys()) track.detach();
              for (const element of attachedAudioElements) element.remove();
              attachedAudioElements.clear();
              attachedElementsByTrack.clear();
              void room.disconnect();
            },
            getNetworkStats: async () => {
              const publication = [...room.localParticipant.audioTrackPublications.values()].find(
                (item) => item.track,
              );
              if (!publication?.track || !('getSenderStats' in publication.track)) {
                return { rttMs: null, packetLossPercent: null };
              }
              const stats = await (
                publication.track as typeof publication.track & {
                  getSenderStats: () => Promise<{
                    roundTripTime?: number;
                    packetsSent?: number;
                    packetsLost?: number;
                  }>;
                }
              ).getSenderStats();
              const sent = stats?.packetsSent ?? null;
              const lost = stats?.packetsLost ?? null;
              return {
                rttMs: stats?.roundTripTime == null ? null : stats.roundTripTime * 1000,
                packetLossPercent:
                  sent !== null && lost !== null && sent + lost > 0
                    ? (Math.max(0, lost) / (sent + Math.max(0, lost))) * 100
                    : null,
              };
            },
          };
          const needsPlaybackConsent = playbackBlocked || !room.canPlaybackAudio;
          setAudioStatus('ready');
          setPlaybackStatus(needsPlaybackConsent ? 'blocked' : 'ready');
          setAudioError(
            needsPlaybackConsent ? debateTRef.current('dialog.audioBlocked') : null,
          );
        }
      } catch (error) {
        if (!cancelled) {
          setAudioStatus('error');
          setAudioError(errorText(error));
        }
      }
    })();
    return () => {
      cancelled = true;
      const session = audioSessionRef.current;
      if (session) {
        void session.setMicrophoneEnabled(false);
        session.disconnect();
      }
      audioSessionRef.current = null;
    };
  }, [matchId, roomQuery.isSuccess, runtime.connectionEpoch, shouldConnectAudio, terminal]);

  const displaySnapshot = useSmoothMatchSnapshot(snapshot);
  const room = roomQuery.data;
  const userId = currentUser.data?.user.id;
  const isCurrentSpeaker = Boolean(
    userId && snapshot?.current_speaker_user_id && snapshot.current_speaker_user_id === userId,
  );
  const isOrganizer = canManageMatch(room, userId, currentUser.data?.user.role);
  const currentMember = room?.members.find((member) => member.user_id === userId);
  const mySeat = room?.seats.find((seat) => seat.user_id === userId);
  const isDebater = Boolean(mySeat);
  const postmatchStatusQuery = useQuery({
    queryKey: ['survey', 'postmatch', matchId, 'status'],
    queryFn: () => surveyApi.postmatchStatus(matchId),
    enabled: snapshot?.status === 'FINISHED' && isDebater,
    staleTime: 5_000,
  });

  const postmatchPromptOpen = shouldShowPostmatchPrompt(
    snapshot?.status,
    isDebater,
    postmatchStatusQuery.data?.pending === true,
    postmatchPromptDismissed,
  );

  const dismissPostmatchPrompt = useCallback(() => {
    window.sessionStorage.setItem(`jx:postmatch-prompt:${matchId}`, '1');
    setPostmatchPromptDismissed(true);
  }, [matchId]);
  const candidateSide =
    snapshot?.action_state === 'FREE_SELECTING'
      ? snapshot.free_holder_side
      : snapshot?.current_speaker_side === 'AFFIRMATIVE'
        ? 'NEGATIVE'
        : 'AFFIRMATIVE';
  const handQueue = snapshot?.hand_queue ?? [];
  const myHandIndex = userId ? handQueue.indexOf(userId) : -1;
  const canRaiseHand =
    snapshot?.hand_window_open && Boolean(mySeat) && mySeat?.side === candidateSide;
  const currentSeat = useMemo(
    () => resolveCurrentSeat(room?.seats, snapshot),
    [room?.seats, snapshot],
  );
  const currentSpeakerLabel = currentSeat
    ? `${currentSeat.side === 'AFFIRMATIVE' ? debateT('affirmative') : debateT('negative')} ${currentSeat.seat_no}`
    : debateT('currentSpeaker');
  const presentation =
    terminalPresentation(snapshot?.status ?? '', (key) => matchT(key as never)) ??
    actionLabel(
      snapshot?.action_state ?? 'NOT_STARTED',
      isCurrentSpeaker,
      currentSpeakerLabel,
      (key, values) => matchT(key as never, values as never),
    );

  useEffect(() => {
    if (audioStatus !== 'ready') return;
    const session = audioSessionRef.current;
    if (!session) return;
    const microphoneEnabled =
      snapshot?.action_state === 'HUMAN_SPEAKING' && snapshot.current_speaker_user_id === userId;
    void session.setMicrophoneEnabled(microphoneEnabled);
  }, [
    audioStatus,
    runtime.connectionEpoch,
    snapshot?.action_state,
    snapshot?.current_speaker_user_id,
    userId,
  ]);

  const confirmationIsStillValid = useMemo(() => {
    if (!confirmation || !snapshot) return false;
    if (confirmation === 'finish') {
      return snapshot.action_state === 'HUMAN_SPEAKING' && isCurrentSpeaker;
    }
    if (confirmation === 'pause') {
      return canPauseMatch(snapshot.status, isDebater, isOrganizer);
    }
    if (confirmation === 'reset') {
      return (
        ((isCurrentSpeaker || isOrganizer) &&
          ['HUMAN_SPEAKING', 'SPEECH_FINALIZING'].includes(snapshot.action_state)) ||
        (isOrganizer &&
          ['AGENT_PREPARING', 'AGENT_SPEAKING', 'AGENT_FINALIZING'].includes(snapshot.action_state))
      );
    }
    return confirmation === 'terminate' ? isOrganizer && !terminal : true;
  }, [confirmation, isCurrentSpeaker, isDebater, isOrganizer, snapshot, terminal]);

  useEffect(() => {
    if (!confirmation || confirmationPending || confirmationIsStillValid) return;
    const task = window.setTimeout(() => {
      setConfirmation(null);
      showToast({ message: debateT('dialog.stateChanged'), tone: 'info' });
    }, 0);
    return () => window.clearTimeout(task);
  }, [confirmation, confirmationIsStillValid, confirmationPending, debateT, showToast]);

  const sendMatchCommand = useCallback(
    (
      type:
        | 'host.finished'
        | 'speech.start'
        | 'speech.finish'
        | 'speech.reset'
        | 'hand.raise'
        | 'hand.cancel'
        | 'match.pause'
        | 'match.resume'
        | 'match.terminate',
    ) => runtime.sendCommand({ type, message_id: crypto.randomUUID() }),
    [runtime],
  );
  const { command, isPending: commandPending } = useMatchCommand(sendMatchCommand);

  async function enableMatchAudio() {
    try {
      const session = audioSessionRef.current;
      if (!session) {
        setAudioError(debateT('dialog.audioConnecting'));
        return;
      }
      await session.enableAudio();
      if (snapshot?.action_state === 'HOST_ANNOUNCING' && hostAudioRef.current?.src) {
        await hostAudioRef.current.play();
      }
      setPlaybackStatus('ready');
      setAudioError(null);
    } catch {
      setPlaybackStatus('blocked');
      setAudioError(debateT('dialog.audioStillBlocked'));
    }
  }

  function toggleOutputMuted() {
    const muted = !outputMutedRef.current;
    outputMutedRef.current = muted;
    setOutputMuted(muted);
    audioSessionRef.current?.setOutputMuted?.(muted);
    if (hostAudioRef.current) hostAudioRef.current.muted = muted;
  }

  function updateMutedHumanUsers(nextUserIds: string[]) {
    const normalized = [...new Set(nextUserIds)];
    mutedHumanUserIdsRef.current = new Set(normalized);
    setMutedHumanUserIds(normalized);
    const storageKey = humanAudioMuteStorageKey(matchId);
    if (normalized.length > 0) {
      window.sessionStorage.setItem(storageKey, serializeMutedHumanUserIds(normalized));
    } else {
      window.sessionStorage.removeItem(storageKey);
    }
    audioSessionRef.current?.setMutedHumanUserIds?.(normalized);
  }

  function toggleHumanOutputMuted(userIdToToggle: string) {
    const next = new Set(mutedHumanUserIdsRef.current);
    if (next.has(userIdToToggle)) next.delete(userIdToToggle);
    else next.add(userIdToToggle);
    updateMutedHumanUsers([...next]);
  }

  function toggleAllHumanOutputMuted() {
    const remoteHumanUserIds =
      room?.seats.flatMap((seat) =>
        seat.occupant_type === 'HUMAN' && seat.user_id && seat.user_id !== userId
          ? [seat.user_id]
          : [],
      ) ?? [];
    const next = new Set(mutedHumanUserIdsRef.current);
    const allMuted =
      remoteHumanUserIds.length > 0 && remoteHumanUserIds.every((candidate) => next.has(candidate));
    for (const candidate of remoteHumanUserIds) {
      if (allMuted) next.delete(candidate);
      else next.add(candidate);
    }
    updateMutedHumanUsers([...next]);
  }

  async function startSpeech() {
    if (audioStatus !== 'ready') {
      setAudioError(debateT('dialog.audioNotReady'));
      return;
    }
    if (await command('speech.start')) {
      await audioSessionRef.current?.setMicrophoneEnabled(true);
    }
  }

  async function finishSpeech() {
    await audioSessionRef.current?.setMicrophoneEnabled(false);
    if (shouldRestoreMicrophone(await command('speech.finish'), isCurrentSpeaker)) {
      await audioSessionRef.current?.setMicrophoneEnabled(true);
    }
  }

  async function saveSpeechText(speechId: string) {
    if (savingSpeechRef.current) return;
    const displayText = normalizedTranscriptDraft(draftText);
    if (!displayText) {
      showToast({ message: debateT('dialog.emptyText'), tone: 'error' });
      return;
    }
    savingSpeechRef.current = true;
    setSavingSpeechId(speechId);
    try {
      const transcript = await matchesApi.updateDisplayText(matchId, speechId, displayText);
      queryClient.setQueryData(['matches', matchId, 'transcript'], transcript);
      setEditingSpeechId(null);
      showToast({ message: debateT('dialog.textSaved'), tone: 'success' });
    } catch (saveError: unknown) {
      showToast({ message: transcriptSaveErrorText(saveError), tone: 'error' });
    } finally {
      savingSpeechRef.current = false;
      setSavingSpeechId(null);
    }
  }

  async function terminateMatch() {
    await command('match.terminate');
  }

  async function pauseMatch() {
    await audioSessionRef.current?.setMicrophoneEnabled(false);
    if (shouldRestoreMicrophone(await command('match.pause'), isCurrentSpeaker)) {
      await audioSessionRef.current?.setMicrophoneEnabled(true);
    }
  }

  async function resetSpeech() {
    await audioSessionRef.current?.setMicrophoneEnabled(false);
    if (shouldRestoreMicrophone(await command('speech.reset'), isCurrentSpeaker)) {
      await audioSessionRef.current?.setMicrophoneEnabled(true);
    }
  }

  async function leaveMatch() {
    if (!room) return;
    setLeaving(true);
    try {
      await roomsApi.leave(room.id);
      window.location.replace('/lobby');
    } catch (error) {
      setAudioError(errorText(error));
      setLeaving(false);
    }
  }

  async function runConfirmedAction() {
    if (!confirmation || !confirmationGate.tryStart()) return;
    setConfirmationPending(true);
    try {
      if (confirmation === 'finish') await finishSpeech();
      if (confirmation === 'pause') await pauseMatch();
      if (confirmation === 'reset') await resetSpeech();
      if (confirmation === 'terminate') await terminateMatch();
      if (confirmation === 'leave') await leaveMatch();
    } finally {
      confirmationGate.release();
      setConfirmationPending(false);
      setConfirmation(null);
    }
  }

  const currentActionKey = snapshot?.current_action
    ? `${snapshot.current_action.stage_position}:${snapshot.current_action.action_position}`
    : null;
  const hostAudioPath = snapshot?.current_action?.host_audio_path;
  const hostAudioUrl =
    snapshot?.action_state === 'HOST_ANNOUNCING' && currentActionKey && hostAudioPath
      ? hostAudioPath.startsWith('/api/')
        ? hostAudioPath
        : `/api/matches/${matchId}/host-audio/${encodeURIComponent(currentActionKey)}`
      : null;
  const visibleAudioError =
    hostAudioUrl && audioError === debateT('dialog.audioBlocked') ? null : audioError;
  // After the live page has a valid snapshot, room and user, refresh failures
  // are recoverable transport errors and must not unmount the match UI.
  const hasEnteredMatch = Boolean(snapshot && roomQuery.data && currentUser.data);
  const entryError = !snapshot
    ? runtime.error
    : !roomQuery.data
      ? roomQuery.error
      : !currentUser.data
        ? currentUser.error
        : null;
  const transientQueryError = hasEnteredMatch
    ? (runtime.error ?? roomQuery.error ?? currentUser.error)
    : null;

  useEffect(() => {
    if (entryError) showToast({ message: errorText(entryError, matchT('connectionUnavailable')), tone: 'error' });
  }, [entryError, matchT, showToast]);

  useEffect(() => {
    if (transientQueryError) {
      showToast({ message: debateT('dialog.syncRetrying'), tone: 'error' });
    }
  }, [debateT, showToast, transientQueryError]);

  useEffect(() => {
    if (visibleAudioError) {
      showToast({
        message: visibleAudioError,
        tone: playbackStatus === 'blocked' ? 'info' : 'error',
      });
    }
  }, [playbackStatus, showToast, visibleAudioError]);

  useEffect(() => {
    const element = hostAudioRef.current;
    if (!element || !hostAudioUrl || !currentActionKey) {
      if (element) {
        element.pause();
        element.removeAttribute('src');
        element.load();
      }
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    let cancelMetadataWait: (() => void) | null = null;
    void (async () => {
      try {
        const response = await fetch(hostAudioUrl, { credentials: 'include' });
        if (!response.ok) {
          let message = debateTRef.current('dialog.hostAudioUnavailable');
          try {
            const payload = (await response.json()) as { error?: { message?: string } };
            message = payload.error?.message || message;
          } catch {
            // Keep the safe fallback when the error body is unavailable.
          }
          if (!cancelled) {
            setAudioError(message);
          }
          return;
        }
        const blob = await response.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        element.src = objectUrl;
        const remainingMs = snapshot?.host_audio_remaining_ms ?? 0;
        if (remainingMs <= 0) return;
        const durationMs = snapshot?.current_action?.host_audio_duration_ms ?? remainingMs;
        if (element.readyState < HTMLMediaElement.HAVE_METADATA) {
          await new Promise<void>((resolve, reject) => {
            const onMetadata = () => {
              cleanup();
              resolve();
            };
            const onError = () => {
              cleanup();
              reject(new Error('host_audio_metadata_unavailable'));
            };
            const cleanup = () => {
              element.removeEventListener('loadedmetadata', onMetadata);
              element.removeEventListener('error', onError);
              cancelMetadataWait = null;
            };
            cancelMetadataWait = () => {
              cleanup();
              resolve();
            };
            element.addEventListener('loadedmetadata', onMetadata, { once: true });
            element.addEventListener('error', onError, { once: true });
          });
        }
        if (cancelled) return;
        element.currentTime = Math.max(0, durationMs - remainingMs) / 1000;
        await element.play();
      } catch (error) {
        if (cancelled) return;
        if (error instanceof DOMException && error.name === 'NotAllowedError') {
          setPlaybackStatus('blocked');
          setAudioError(debateTRef.current('dialog.audioBlocked'));
        } else {
          setAudioError(debateTRef.current('dialog.hostAudioLoadFailed'));
        }
      }
    })();
    return () => {
      cancelled = true;
      cancelMetadataWait?.();
      element.pause();
      element.removeAttribute('src');
      element.load();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [
    currentActionKey,
    hostAudioMounted,
    hostAudioUrl,
    snapshot?.current_action?.host_audio_duration_ms,
    snapshot?.host_audio_remaining_ms,
  ]);

  if (entryError) {
    return (
      <main className="jx-page-grid grid min-h-screen place-items-center px-6">
        <section className="max-w-md rounded-[1.75rem] border border-red-200 bg-white p-8 text-center shadow-xl">
          <CircleAlert className="mx-auto size-9 text-red-600" />
          <h1 className="mt-4 text-2xl font-black text-slate-950">{debateT('dialog.entryFailedTitle')}</h1>
          <p className="mt-3 text-sm leading-7 text-slate-600">
            {debateT('dialog.entryFailedDescription')}
          </p>
          <Link
            className={buttonVariants({ variant: 'primary', size: 'lg' }) + ' mt-6'}
            href="/lobby"
          >
            {debateT('dialog.returnLobby')}
          </Link>
        </section>
      </main>
    );
  }
  if (runtime.isLoading || !snapshot || !room || !currentUser.data) {
    return (
      <main className="jx-page-grid grid min-h-screen place-items-center">
        <div className="text-center">
          <LoaderCircle className="mx-auto size-9 animate-spin text-blue-600" />
          <p className="mt-4 text-sm font-semibold text-slate-500">{debateT('dialog.entering')}</p>
        </div>
      </main>
    );
  }

  return (
    <>
      <DebatePageLayout
        audioError={terminal ? null : visibleAudioError}
        audioStatus={terminal ? 'ready' : audioStatus}
        playbackStatus={terminal ? 'ready' : playbackStatus}
        outputMuted={outputMuted}
        mutedHumanUserIds={mutedHumanUserIds}
        canRaiseHand={canRaiseHand}
        commandPending={commandPending}
        currentSeat={currentSeat}
        currentUserId={userId ?? ''}
        draftText={draftText}
        drawerOpen={drawerOpen}
        editingSpeechId={editingSpeechId}
        handQueue={handQueue}
        agentHandQueue={snapshot?.agent_hand_queue ?? []}
        isCurrentSpeaker={isCurrentSpeaker}
        isDebater={isDebater}
        isOrganizer={isOrganizer}
        leaving={leaving}
        matchId={matchId}
        myHandIndex={myHandIndex}
        onCloseDrawer={() => setDrawerOpen(false)}
        onCommand={(type) => void command(type)}
        onDraftTextChange={setDraftText}
        onEditSpeech={(speechId, text) => {
          setEditingSpeechId(speechId);
          setDraftText(text);
        }}
        onEnableAudio={() => void enableMatchAudio()}
        onToggleOutputMuted={toggleOutputMuted}
        onToggleAllHumanOutputMuted={toggleAllHumanOutputMuted}
        onToggleHumanOutputMuted={toggleHumanOutputMuted}
        onFinishSpeech={() => setConfirmation('finish')}
        onLeave={() => setConfirmation('leave')}
        onOpenDrawer={() => setDrawerOpen(true)}
        onPause={() => setConfirmation('pause')}
        onResetSpeech={() => setConfirmation('reset')}
        onSaveSpeech={(speechId) => void saveSpeechText(speechId)}
        onStartSpeech={() => void startSpeech()}
        onTerminate={() => setConfirmation('terminate')}
        networkOpen={networkOpen}
        networkStats={networkStats}
        onOpenNetwork={() => setNetworkOpen(true)}
        onCloseNetwork={() => setNetworkOpen(false)}
        presentation={presentation}
        room={room}
        runtime={{
          socketStatus: runtime.socketStatus,
          socketError: runtime.socketError,
          interimText: runtime.interimText,
          resumeReasons: runtime.resumeReasons,
          commandReady: runtime.commandReady,
        }}
        savingSpeechId={savingSpeechId}
        snapshot={displaySnapshot ?? snapshot}
        transcript={transcriptQuery.data}
        transcriptLoading={transcriptQuery.isPending || transcriptQuery.isFetching}
        transcriptError={transcriptQuery.isError}
        onRetryTranscript={() => void transcriptQuery.refetch()}
      />
      {confirmation ? (
        <ConfirmDialog
          confirmLabel={confirmationCopy(debateT)[confirmation].confirmLabel}
          description={
            confirmation === 'leave' && currentMember?.member_role === 'SPECTATOR'
              ? debateT('dialog.leaveSpectatorDescription')
              : confirmationCopy(debateT)[confirmation].description
          }
          loading={confirmationPending}
          onConfirm={() => void runConfirmedAction()}
          onOpenChange={(open) => {
            if (!open) setConfirmation(null);
          }}
          open
          title={
            confirmation === 'leave' && currentMember?.member_role === 'SPECTATOR'
              ? debateT('dialog.leaveSpectatorTitle')
              : confirmationCopy(debateT)[confirmation].title
          }
        />
      ) : null}
      {postmatchPromptOpen && postmatchStatusQuery.data?.pending ? (
        <ConfirmDialog
          cancelLabel={debateT('dialog.surveyLater')}
          confirmLabel={debateT('dialog.surveyComplete')}
          description={debateT('dialog.surveyDescription')}
          icon={<ClipboardCheck className="size-5" aria-hidden="true" />}
          onConfirm={() => {
            dismissPostmatchPrompt();
            router.push(postmatchStatusQuery.data?.href ?? '/me/postmatch-surveys');
          }}
          onOpenChange={(open) => {
            if (!open) dismissPostmatchPrompt();
          }}
          open
          title={debateT('dialog.surveyTitle')}
          tone="primary"
        />
      ) : null}
      {snapshot?.status === 'FINISHED' &&
      isDebater &&
      postmatchStatusQuery.data?.pending &&
      !postmatchPromptOpen ? (
        <div className="fixed bottom-4 left-1/2 z-[80] w-[min(calc(100vw-2rem),520px)] -translate-x-1/2 rounded-xl border border-blue-200 bg-white p-3 shadow-[0_16px_48px_rgba(15,23,42,0.18)]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm font-bold text-slate-800">{debateT('dialog.surveyPending')}</p>
            <Link
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-bold text-white hover:bg-blue-700"
              href={postmatchStatusQuery.data.href}
            >
              <ClipboardCheck className="size-4" aria-hidden="true" /> {debateT('dialog.surveyComplete')}
            </Link>
          </div>
        </div>
      ) : null}
      <audio ref={setHostAudioRef} className="sr-only" muted={outputMuted} preload="auto" />
    </>
  );
}
