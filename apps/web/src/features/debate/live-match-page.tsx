'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, LoaderCircle } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { buttonVariants } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { useCurrentUser } from '@/features/auth/use-auth';
import { ApiClientError } from '@/lib/auth-api';
import { matchesApi } from '@/lib/matches-api';
import { roomsApi } from '@/lib/rooms-api';
import { useSubmissionGate } from '@/lib/use-submission-gate';

import { DebatePageLayout } from './debate-page-layout';
import { shouldConnectMatchAudio } from './match-audio-policy';
import { resolveCurrentSeat } from './match-presentation';
import { useMatchRuntime } from './use-match-runtime';
import { shouldRestoreMicrophone, useMatchCommand } from './use-match-command';
import { useSmoothMatchSnapshot } from './use-smooth-match-snapshot';

interface MatchAudioSession {
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  enableAudio(): Promise<void>;
  setOutputMuted?(muted: boolean): void;
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

function errorText(error: unknown): string {
  return error instanceof ApiClientError ? error.message : '比赛连接暂时不可用，请刷新后重试。';
}

export function transcriptSaveErrorText(error: unknown): string {
  return error instanceof ApiClientError ? error.message : '保存失败，请稍后重试。';
}

export function normalizedTranscriptDraft(draft: string): string | null {
  const normalized = draft.trim();
  return normalized ? normalized : null;
}

type MatchConfirmation = 'finish' | 'pause' | 'reset' | 'terminate' | 'leave';

const confirmationCopy: Record<
  MatchConfirmation,
  { title: string; description: string; confirmLabel: string }
> = {
  finish: {
    title: '提前结束本次发言？',
    description: '麦克风将立即关闭，系统会等待 ASR 最终文字并进入下一环节。',
    confirmLabel: '结束发言',
  },
  pause: {
    title: '暂停整场比赛？',
    description: '暂停期间计时、ASR、Agent 调用和实时语音都会冻结。',
    confirmLabel: '确认暂停',
  },
  reset: {
    title: '异常重置当前发言？',
    description: '当前未完成的文字和音频将被清除，并从本次发言起点重新开始。',
    confirmLabel: '确认重置',
  },
  terminate: {
    title: '终止本场比赛？',
    description: '终止后不能继续发言，本场比赛不会正常评分或进入排行榜。',
    confirmLabel: '终止比赛',
  },
  leave: {
    title: '离开比赛页面？',
    description: '离开后你的辩手席位不会由 Agent 接管；持续离线 1 分钟后比赛会暂停。',
    confirmLabel: '确认离开',
  },
};

export function actionLabel(actionState: string): {
  eyebrow: string;
  title: string;
  detail: string;
} {
  switch (actionState) {
    case 'HOST_ANNOUNCING':
      return { eyebrow: '赛制播报', title: '主持音频播放中', detail: '播报结束后进入当前阶段。' };
    case 'HUMAN_READY_TO_START':
      return {
        eyebrow: '当前发言席位已就绪',
        title: '轮到你发言了！',
        detail: '点击开始后才会开启麦克风并启动正式计时。',
      };
    case 'HUMAN_SPEAKING':
      return {
        eyebrow: '实时发言中',
        title: '麦克风已开启',
        detail: '服务端正在控制发言时长，你可以提前结束。',
      };
    case 'SPEECH_FINALIZING':
      return {
        eyebrow: '文字整理中',
        title: '正在整理文字记录',
        detail: '发言已经结束，正在等待 ASR 最终结果。',
      };
    case 'AGENT_PREPARING':
      return {
        eyebrow: 'Agent 准备中',
        title: 'Agent 正在思考中',
        detail: '正在生成正式发言并建立实时语音，等待时间不计入发言时长。',
      };
    case 'AGENT_SPEAKING':
      return {
        eyebrow: 'Agent 实时发言',
        title: 'Agent 正在发言',
        detail: '语音正在实时播放，文字记录同步更新。',
      };
    case 'AGENT_FINALIZING':
      return {
        eyebrow: 'Agent 发言收尾',
        title: '正在提交正式记录',
        detail: '系统正在确认实际播放文字和音频文件。',
      };
    case 'PREPARING':
      return {
        eyebrow: '准备阶段',
        title: '准备时间进行中',
        detail: '倒计时结束后自动进入下一动作。',
      };
    case 'MATCH_FINISHED':
      return {
        eyebrow: '比赛结束',
        title: '本场辩论已完成',
        detail: '完整文字记录已经归档，AI 裁判正在生成或已经完成评分。',
      };
    case 'RECOVERY_REQUIRED':
      return {
        eyebrow: '系统恢复保护',
        title: '比赛已安全暂停',
        detail: '计时与实时语音均已冻结；满足在线和设备条件后可以申请恢复。',
      };
    case 'FREE_SELECTING':
      return {
        eyebrow: '自由辩论候选中',
        title: '申请下一次发言',
        detail: '人类举手优先；无人举手时由本方 Agent 独立决策并选择发言者。',
      };
    case 'RESUME_COUNTDOWN':
      return {
        eyebrow: '比赛即将恢复',
        title: '3 秒后恢复比赛',
        detail: '请保持设备连接，当前发言将从安全起点重新开始。',
      };
    default:
      return { eyebrow: '比赛启动', title: '正在建立实时状态', detail: '请保持页面打开。' };
  }
}

export function terminalPresentation(status: string): ReturnType<typeof actionLabel> | null {
  if (status === 'TERMINATED') {
    return {
      eyebrow: '比赛终止',
      title: '本场比赛已终止',
      detail: '比赛已由房主终止，不能继续发言；现有文字记录仍可查看。',
    };
  }
  return status === 'FINISHED' ? actionLabel('MATCH_FINISHED') : null;
}

export function LiveMatchPage({ matchId }: Readonly<{ matchId: string }>) {
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
  const [audioStatus, setAudioStatus] = useState<'connecting' | 'ready' | 'blocked' | 'error'>(
    'connecting',
  );
  const [audioError, setAudioError] = useState<string | null>(null);
  const [outputMuted, setOutputMuted] = useState(false);
  const outputMutedRef = useRef(false);
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
  const confirmationGate = useSubmissionGate();
  const hostAudioRef = useRef<HTMLAudioElement | null>(null);
  const [hostAudioMounted, setHostAudioMounted] = useState(false);
  const setHostAudioRef = useCallback((element: HTMLAudioElement | null) => {
    hostAudioRef.current = element;
    setHostAudioMounted(Boolean(element));
  }, []);
  const [hostEndedActionKey, setHostEndedActionKey] = useState<string | null>(null);
  const hostFinishPendingRef = useRef<string | null>(null);
  const hostFinishTimerRef = useRef<number | null>(null);
  const presenceRefreshEpochRef = useRef<number | null>(null);
  const snapshot = runtime.snapshot;
  const shouldConnectAudio = shouldConnectMatchAudio(snapshot?.status);
  const terminal = Boolean(snapshot && !shouldConnectAudio);

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
    if (!shouldConnectAudio) {
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
      try {
        if (canUseLocalAudioOverride() && window.__JX_MATCH_AUDIO_OVERRIDE__) {
          audioSessionRef.current = await window.__JX_MATCH_AUDIO_OVERRIDE__();
          if (!cancelled) setAudioStatus('ready');
        } else {
          const [{ Room, RoomEvent, Track }, token] = await Promise.all([
            import('livekit-client'),
            matchesApi.liveKitToken(matchId),
          ]);
          const room = new Room({ adaptiveStream: true, dynacast: true });
          const attachedAudioElements = new Set<HTMLMediaElement>();
          const attachedElementsByTrack = new Map<
            { detach: () => HTMLMediaElement[] },
            Set<HTMLMediaElement>
          >();
          let playbackBlocked = false;
          const markPlaybackBlocked = () => {
            playbackBlocked = true;
            if (!cancelled) {
              setAudioStatus('blocked');
              setAudioError('浏览器尚未允许播放比赛声音，请点击“开启比赛声音”。');
            }
          };
          const attachAudio = (track: {
            kind: string;
            attach: () => HTMLMediaElement;
            detach: () => HTMLMediaElement[];
          }) => {
            if (track.kind !== Track.Kind.Audio) return;
            const element = track.attach();
            element.autoplay = true;
            element.setAttribute('playsinline', 'true');
            element.volume = 1;
            element.muted = outputMutedRef.current;
            element.style.display = 'none';
            document.body.appendChild(element);
            attachedAudioElements.add(element);
            attachedElementsByTrack.set(track, new Set([element]));
            void element.play().catch(markPlaybackBlocked);
          };
          const detachAudio = (track: { detach: () => HTMLMediaElement[] }) => {
            // RemoteTrackPublication detaches its elements before emitting
            // TrackUnsubscribed when a remote media track ends. Keep our own
            // references so ended audio elements are removed even after the
            // SDK has already cleared track.attachedElements.
            const elements = attachedElementsByTrack.get(track) ?? new Set<HTMLMediaElement>();
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
          audioSessionRef.current = {
            setMicrophoneEnabled: (enabled) =>
              room.localParticipant.setMicrophoneEnabled(enabled).then(() => undefined),
            enableAudio: async () => {
              await room.startAudio();
              await Promise.allSettled([...attachedAudioElements].map((element) => element.play()));
              playbackBlocked = false;
            },
            setOutputMuted: (muted) => {
              for (const element of attachedAudioElements) element.muted = muted;
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
          if (!cancelled) {
            const needsPlaybackConsent = playbackBlocked || !room.canPlaybackAudio;
            setAudioStatus(needsPlaybackConsent ? 'blocked' : 'ready');
            setAudioError(
              needsPlaybackConsent ? '浏览器尚未允许播放比赛声音，请点击“开启比赛声音”。' : null,
            );
          }
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
  }, [matchId, shouldConnectAudio, terminal]);

  const displaySnapshot = useSmoothMatchSnapshot(snapshot);
  const room = roomQuery.data;
  const userId = currentUser.data?.user.id;
  const isCurrentSpeaker = snapshot?.current_speaker_user_id === userId;
  const isOrganizer = room?.organizer_user_id === userId;
  const currentMember = room?.members.find((member) => member.user_id === userId);
  const mySeat = room?.seats.find((seat) => seat.user_id === userId);
  const isDebater = Boolean(mySeat);
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
  const presentation =
    terminalPresentation(snapshot?.status ?? '') ??
    actionLabel(snapshot?.action_state ?? 'NOT_STARTED');
  const currentSeat = useMemo(
    () => resolveCurrentSeat(room?.seats, snapshot),
    [room?.seats, snapshot],
  );

  const confirmationIsStillValid = useMemo(() => {
    if (!confirmation || !snapshot) return false;
    if (confirmation === 'finish') {
      return snapshot.action_state === 'HUMAN_SPEAKING' && isCurrentSpeaker;
    }
    if (confirmation === 'pause') {
      return snapshot.status === 'RUNNING' && isDebater;
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
      showToast({ message: '比赛状态已变化，未执行该操作。', tone: 'info' });
    }, 0);
    return () => window.clearTimeout(task);
  }, [confirmation, confirmationIsStillValid, confirmationPending, showToast]);

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
        setAudioError('实时音频仍在连接，请稍后再试。');
        return;
      }
      await session.enableAudio();
      if (snapshot?.action_state === 'HOST_ANNOUNCING' && hostAudioRef.current?.src) {
        await hostAudioRef.current.play();
      }
      setAudioStatus('ready');
      setAudioError(null);
    } catch {
      setAudioStatus('blocked');
      setAudioError('浏览器仍未允许播放比赛声音，请再次点击“开启比赛声音”。');
    }
  }

  function toggleOutputMuted() {
    const muted = !outputMutedRef.current;
    outputMutedRef.current = muted;
    setOutputMuted(muted);
    audioSessionRef.current?.setOutputMuted?.(muted);
    if (hostAudioRef.current) hostAudioRef.current.muted = muted;
  }

  async function startSpeech() {
    if (audioStatus !== 'ready') {
      setAudioError('实时音频尚未连接，请等待连接完成后再开始发言。');
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
      showToast({ message: '文字不能为空', tone: 'error' });
      return;
    }
    savingSpeechRef.current = true;
    setSavingSpeechId(speechId);
    try {
      const transcript = await matchesApi.updateDisplayText(matchId, speechId, displayText);
      queryClient.setQueryData(['matches', matchId, 'transcript'], transcript);
      setEditingSpeechId(null);
      showToast({ message: '文字修改已保存', tone: 'success' });
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
    hostAudioUrl && audioError?.startsWith('浏览器尚未允许播放') ? null : audioError;
  const entryError = runtime.error ?? roomQuery.error ?? currentUser.error;

  useEffect(() => {
    if (entryError) showToast({ message: errorText(entryError), tone: 'error' });
  }, [entryError, showToast]);

  useEffect(() => {
    if (visibleAudioError) {
      showToast({
        message: visibleAudioError,
        tone: audioStatus === 'blocked' ? 'info' : 'error',
      });
    }
  }, [audioStatus, showToast, visibleAudioError]);

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
    const actionKey = currentActionKey;
    let objectUrl: string | null = null;
    let cancelled = false;
    const onEnded = () => setHostEndedActionKey(actionKey);
    element.addEventListener('ended', onEnded);
    void (async () => {
      try {
        const response = await fetch(hostAudioUrl, { credentials: 'include' });
        if (!response.ok) {
          let message = '主持音频暂时不可用，请刷新后重试。';
          try {
            const payload = (await response.json()) as { error?: { message?: string } };
            message = payload.error?.message || message;
          } catch {
            // Keep the safe fallback when the error body is unavailable.
          }
          if (!cancelled) {
            setAudioStatus('error');
            setAudioError(message);
          }
          return;
        }
        const blob = await response.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        element.src = objectUrl;
        element.currentTime = 0;
        await element.play();
      } catch (error) {
        if (cancelled) return;
        if (error instanceof DOMException && error.name === 'NotAllowedError') {
          setAudioStatus('blocked');
          setAudioError('浏览器尚未允许播放主持音频，请点击“开启比赛声音”。');
        } else {
          setAudioStatus('error');
          setAudioError('主持音频加载失败，请刷新后重试。');
        }
      }
    })();
    return () => {
      cancelled = true;
      if (hostFinishTimerRef.current !== null) {
        window.clearTimeout(hostFinishTimerRef.current);
        hostFinishTimerRef.current = null;
      }
      setHostEndedActionKey((current) => (current === actionKey ? null : current));
      element.removeEventListener('ended', onEnded);
      element.pause();
      element.removeAttribute('src');
      element.load();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [currentActionKey, hostAudioMounted, hostAudioUrl]);

  useEffect(() => {
    if (
      !hostEndedActionKey ||
      hostFinishPendingRef.current === hostEndedActionKey ||
      (!isOrganizer && !isDebater) ||
      !runtime.commandReady ||
      snapshot?.action_state !== 'HOST_ANNOUNCING'
    ) {
      return;
    }
    const actionKey = hostEndedActionKey;
    hostFinishPendingRef.current = actionKey;
    hostFinishTimerRef.current = window.setTimeout(() => {
      hostFinishTimerRef.current = null;
      void command('host.finished').finally(() => {
        if (hostFinishPendingRef.current === actionKey) hostFinishPendingRef.current = null;
        setHostEndedActionKey((current) => (current === actionKey ? null : current));
      });
    }, 1000);
    return () => {
      if (hostFinishTimerRef.current !== null) {
        window.clearTimeout(hostFinishTimerRef.current);
        hostFinishTimerRef.current = null;
      }
      if (hostFinishPendingRef.current === actionKey) hostFinishPendingRef.current = null;
    };
  }, [
    command,
    hostEndedActionKey,
    isDebater,
    isOrganizer,
    runtime.commandReady,
    snapshot?.action_state,
  ]);

  useEffect(
    () => () => {
      if (hostFinishTimerRef.current !== null) window.clearTimeout(hostFinishTimerRef.current);
    },
    [],
  );

  if (entryError) {
    return (
      <main className="jx-page-grid grid min-h-screen place-items-center px-6">
        <section className="max-w-md rounded-[1.75rem] border border-red-200 bg-white p-8 text-center shadow-xl">
          <CircleAlert className="mx-auto size-9 text-red-600" />
          <h1 className="mt-4 text-2xl font-black text-slate-950">无法进入比赛</h1>
          <p className="mt-3 text-sm leading-7 text-slate-600">
            请返回大厅后重试；如果比赛仍在进行，你可以再次进入。
          </p>
          <Link
            className={buttonVariants({ variant: 'primary', size: 'lg' }) + ' mt-6'}
            href="/lobby"
          >
            返回公开大厅
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
          <p className="mt-4 text-sm font-semibold text-slate-500">正在进入比赛现场…</p>
        </div>
      </main>
    );
  }

  return (
    <>
      <DebatePageLayout
        audioError={terminal ? null : visibleAudioError}
        audioStatus={terminal ? 'ready' : audioStatus}
        outputMuted={outputMuted}
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
          confirmLabel={confirmationCopy[confirmation].confirmLabel}
          description={
            confirmation === 'leave' && currentMember?.member_role === 'SPECTATOR'
              ? '离开后会释放观战名额，你之后仍可在有空位时重新加入。'
              : confirmationCopy[confirmation].description
          }
          loading={confirmationPending}
          onConfirm={() => void runConfirmedAction()}
          onOpenChange={(open) => {
            if (!open) setConfirmation(null);
          }}
          open
          title={
            confirmation === 'leave' && currentMember?.member_role === 'SPECTATOR'
              ? '离开观战？'
              : confirmationCopy[confirmation].title
          }
        />
      ) : null}
      <audio ref={setHostAudioRef} className="sr-only" muted={outputMuted} preload="auto" />
    </>
  );
}
