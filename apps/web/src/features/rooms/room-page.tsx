'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  ClipboardCopy,
  Headphones,
  LoaderCircle,
  Link2,
  LogOut,
  Mic,
  Play,
  Radio,
  RefreshCcw,
  ShieldCheck,
} from 'lucide-react';
import Link from 'next/link';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { Button, buttonVariants } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { useCurrentUser } from '@/features/auth/use-auth';
import { ApiClientError } from '@/lib/auth-api';
import {
  runLiveKitDeviceProbe,
  runSpeakerProbe,
  type LiveKitDeviceProbeResult,
} from '@/lib/livekit-device-probe';
import { matchesApi } from '@/lib/matches-api';
import { roomsApi, type RoomSnapshot, type SeatSwap } from '@/lib/rooms-api';
import { useSubmissionGate } from '@/lib/use-submission-gate';

import { resolveRoomEntry } from './room-entry';
import { derivePreparationFlow, deriveRoomStartBlockers } from './room-experience';
import { PreparationProgress, RoomSeatCard } from './room-preparation-view';

const humanTermsQueryKey = ['legal', 'human-participation'] as const;
const subscribeToOrigin = () => () => undefined;

function errorText(error: unknown): string {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试。';
}

type PreparationState =
  | 'IDLE'
  | 'PROBING'
  | 'WARN_CONFIRM'
  | 'SAVING'
  | 'READYING'
  | 'READY'
  | 'FAIL_RETRY'
  | 'ERROR_RETRY';

class DevicePreparationError extends Error {
  constructor(
    readonly stage: 'SAVING' | 'READYING',
    readonly source: unknown,
  ) {
    super(stage === 'SAVING' ? 'device_check_save_failed' : 'device_ready_failed');
    this.name = 'DevicePreparationError';
  }
}

function devicePreparationErrorText(error: unknown): string {
  if (!(error instanceof DevicePreparationError)) {
    return '设备准备没有完成，请重新尝试。';
  }
  if (error.source instanceof ApiClientError && error.source.status < 500) {
    return error.source.message;
  }
  return error.stage === 'SAVING'
    ? '检测结果保存失败，请重新检测后再试。'
    : '检测已保存，但准备状态更新失败。请点击“直接使用检测并准备”重试。';
}

export function RoomPage({
  roomId,
  recheck = false,
  created = false,
}: Readonly<{ roomId: string; recheck?: boolean; created?: boolean }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const userQuery = useCurrentUser();
  const startGate = useSubmissionGate();
  const termsQuery = useQuery({ queryKey: humanTermsQueryKey, queryFn: roomsApi.humanTerms });
  const roomQueryKey = useMemo(() => ['rooms', roomId, 'snapshot'] as const, [roomId]);
  const roomQuery = useQuery({
    queryKey: roomQueryKey,
    queryFn: () => roomsApi.snapshot(roomId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'WAITING') return 1_500;
      if (status && ['START_PENDING_RUNTIME', 'RUNNING', 'PAUSED'].includes(status)) return 2_000;
      return false;
    },
    placeholderData: (previous) => previous,
  });
  const recordingRef = useRef<HTMLAudioElement | null>(null);
  const [probeResult, setProbeResult] = useState<LiveKitDeviceProbeResult | null>(null);
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null);
  const [playbackConfirmed, setPlaybackConfirmed] = useState(false);
  const [playbackPlaying, setPlaybackPlaying] = useState(false);
  const [preparationState, setPreparationState] = useState<PreparationState>('IDLE');
  const [inviteOpen, setInviteOpen] = useState(created);
  const [leaveConfirmOpen, setLeaveConfirmOpen] = useState(false);
  const [swapTarget, setSwapTarget] = useState<{ userId: string; name: string } | null>(null);
  const notifiedSwapIds = useRef(new Set<string>());
  const swapSyncIssueNotified = useRef(false);
  const probeAbortRef = useRef<AbortController | null>(null);
  const room = roomQuery.data;
  const currentUserId = userQuery.data?.user.id;
  const currentMember = room?.members.find((member) => member.user_id === currentUserId);
  const isOrganizer = Boolean(room && currentUserId === room.organizer_user_id);
  const swapQueryKey = useMemo(() => ['rooms', roomId, 'seat-swap-requests'] as const, [roomId]);
  const swapQuery = useQuery({
    queryKey: swapQueryKey,
    queryFn: () => roomsApi.seatSwapRequests(roomId),
    enabled: room?.status === 'WAITING' && Boolean(currentUserId),
    refetchInterval: room?.status === 'WAITING' ? 1_500 : false,
    placeholderData: (previous) => previous,
  });
  const swapRequests = useMemo(
    () => (room?.status === 'WAITING' ? (swapQuery.data ?? []) : []),
    [room?.status, swapQuery.data],
  );
  useLayoutEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  }, [roomId]);
  useEffect(() => {
    if (roomQuery.error) {
      showToast({ message: errorText(roomQuery.error), tone: 'error' });
    }
  }, [roomQuery.error, showToast]);
  useEffect(() => {
    if (!currentUserId) return;
    const rejected = swapRequests.find(
      (item) => item.requester_user_id === currentUserId && item.status === 'REJECTED',
    );
    if (rejected && !notifiedSwapIds.current.has(rejected.id)) {
      notifiedSwapIds.current.add(rejected.id);
      showToast({ message: '对方拒绝了席位交换申请。', tone: 'error' });
    }
  }, [currentUserId, showToast, swapRequests]);
  useEffect(() => {
    if (swapQuery.isError && !swapSyncIssueNotified.current) {
      swapSyncIssueNotified.current = true;
      showToast({ message: '席位交换状态暂时无法同步，系统会自动重试。', tone: 'error' });
    } else if (swapQuery.isSuccess) {
      swapSyncIssueNotified.current = false;
    }
  }, [showToast, swapQuery.isError, swapQuery.isSuccess]);
  const origin = useSyncExternalStore(
    subscribeToOrigin,
    () => window.location.origin,
    () => '',
  );
  const inviteLink = room ? `${origin}/join/${room.code}` : '';

  useEffect(
    () => () => {
      if (recordingUrl) URL.revokeObjectURL(recordingUrl);
    },
    [recordingUrl],
  );
  useEffect(
    () => () => {
      probeAbortRef.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (!room) return;
    const entry = resolveRoomEntry(room);
    if (!recheck && (entry.kind === 'LIVE_MATCH' || entry.kind === 'POSTMATCH')) {
      window.location.replace(entry.href);
    }
  }, [recheck, room]);
  const refreshRoom = async (snapshot: RoomSnapshot) => {
    queryClient.setQueryData<RoomSnapshot | undefined>(roomQueryKey, (previous) =>
      !previous || snapshot.sequence >= previous.sequence ? snapshot : previous,
    );
  };
  const pushNotice = (message: string, tone: 'success' | 'error' | 'info' = 'info') =>
    showToast({ message, tone });
  const joinMutation = useMutation({
    mutationFn: (role: 'DEBATER' | 'SPECTATOR') =>
      roomsApi.join(roomId, {
        member_role: role,
        human_participation_terms_version: role === 'DEBATER' ? termsQuery.data?.version : null,
      }),
    onSuccess: async (snapshot) => {
      await refreshRoom(snapshot);
      const entry = resolveRoomEntry(snapshot);
      if (entry.kind === 'LIVE_MATCH') window.location.replace(entry.href);
    },
    onError: (error) => {
      setLeaveConfirmOpen(false);
      showToast({ message: errorText(error), tone: 'error' });
    },
  });
  const seatMutation = useMutation({
    mutationFn: (payload: { side: 'AFFIRMATIVE' | 'NEGATIVE'; seat_no: number }) =>
      roomsApi.selectSeat(roomId, {
        ...payload,
        human_participation_terms_version: termsQuery.data?.version ?? '',
      }),
    onSuccess: (snapshot, payload) => {
      void refreshRoom(snapshot);
      const target = snapshot.seats.find(
        (seat) => seat.side === payload.side && seat.seat_no === payload.seat_no,
      );
      pushNotice(
        `已切换到${payload.side === 'AFFIRMATIVE' ? '正方' : '反方'}${payload.seat_no}辩；${
          snapshot.latest_device_check?.is_valid
            ? '设备检测仍有效，无需重复检测。'
            : '请选择开始设备检测。'
        }`,
        target?.occupant_type === 'HUMAN' ? 'success' : 'info',
      );
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const swapMutation = useMutation({
    mutationFn: (targetUserId: string) => roomsApi.createSeatSwapRequest(roomId, targetUserId),
    onSuccess: () => {
      setSwapTarget(null);
      void queryClient.invalidateQueries({ queryKey: swapQueryKey });
      showToast({ message: '交换申请已发送，等待对方确认。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const respondSwapMutation = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'ACCEPT' | 'REJECT' }) =>
      roomsApi.respondSeatSwapRequest(roomId, id, decision),
    onSuccess: (_, variables) => {
      queryClient.setQueryData<SeatSwap[]>(swapQueryKey, (items) =>
        items?.filter((item) => item.id !== variables.id),
      );
      void roomQuery.refetch();
      showToast({
        message: variables.decision === 'ACCEPT' ? '已同意交换席位。' : '已拒绝交换申请。',
        tone: 'success',
      });
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const roleMutation = useMutation({
    mutationFn: (role: 'DEBATER' | 'SPECTATOR') =>
      roomsApi.changeRole(roomId, role, termsQuery.data?.version),
    onSuccess: (snapshot, role) => {
      void refreshRoom(snapshot);
      pushNotice(
        role === 'SPECTATOR' ? '已切换为观众，原席位已按规则补位。' : '已切换为辩手，请选择席位。',
        'success',
      );
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const preparationMutation = useMutation({
    mutationFn: async ({
      result,
      version: existingVersion,
    }: {
      result?: LiveKitDeviceProbeResult;
      version?: number;
    }) => {
      let savedSnapshot: RoomSnapshot | null = null;
      let version = existingVersion ?? null;
      if (result) {
        setPreparationState('SAVING');
        let saved: RoomSnapshot;
        try {
          saved = await roomsApi.deviceCheck(roomId, {
            status: result.status,
            warning_confirmed: result.status === 'WARN',
            details: {
              microphone: 'pass',
              speaker: 'tone_played',
              livekit_test_room: 'connected',
              rtt_p95_ms: result.rttP95Ms,
              packet_loss_p95_percent: result.packetLossP95Percent,
              connection_quality: result.connectionQuality,
              samples: result.samples,
              input_peak: result.inputPeak,
              recording_seconds: 3,
              recording_playback_confirmed: playbackConfirmed,
            },
          });
        } catch (error) {
          throw new DevicePreparationError('SAVING', error);
        }
        savedSnapshot = saved;
        await refreshRoom(saved);
        setProbeResult(null);
        version = saved.latest_device_check?.check_version ?? null;
      }
      if (!version) throw new DevicePreparationError('SAVING', new Error('missing_check'));
      if (recheck) {
        if (!savedSnapshot) throw new DevicePreparationError('SAVING', new Error('missing_check'));
        return savedSnapshot;
      }
      setPreparationState('READYING');
      try {
        return await roomsApi.ready(roomId, version);
      } catch (error) {
        throw new DevicePreparationError('READYING', error);
      }
    },
    onSuccess: async (snapshot) => {
      await refreshRoom(snapshot);
      setPreparationState('READY');
      setProbeResult(null);
      pushNotice(recheck ? '设备复检已完成。' : '设备正常，已自动完成准备。', 'success');
      if (recheck && snapshot.match_id) {
        window.location.replace(`/debate?match_id=${encodeURIComponent(snapshot.match_id)}`);
      }
    },
    onError: (error) => {
      setPreparationState('ERROR_RETRY');
      pushNotice(devicePreparationErrorText(error), 'error');
      void roomQuery.refetch();
    },
  });
  const startMutation = useMutation({
    mutationFn: async () => {
      const starting = await roomsApi.start(roomId);
      await refreshRoom(starting);
      return matchesApi.startRuntime(roomId);
    },
    onSuccess: (match) => {
      window.location.replace(`/debate?match_id=${encodeURIComponent(match.match_id)}`);
    },
    onError: async (error) => {
      showToast({ message: errorText(error), tone: 'error' });
      await roomQuery.refetch();
      startGate.release();
    },
  });
  const runtimeStartMutation = useMutation({
    mutationFn: () => matchesApi.startRuntime(roomId),
    onSuccess: (match) => {
      window.location.replace(`/debate?match_id=${encodeURIComponent(match.match_id)}`);
    },
    onError: async (error) => {
      showToast({ message: errorText(error), tone: 'error' });
      await roomQuery.refetch();
      startGate.release();
    },
  });
  const leaveMutation = useMutation({
    mutationFn: () => roomsApi.leave(roomId),
    onSuccess: () => {
      window.location.replace('/lobby');
    },
    onError: (error) => showToast({ message: errorText(error), tone: 'error' }),
  });
  const invalidateDeviceMutation = useMutation({
    mutationFn: () => roomsApi.invalidateDeviceCheck(roomId),
    onSuccess: (snapshot) => {
      void refreshRoom(snapshot);
      setPreparationState('IDLE');
      pushNotice('检测到设备变化，请重新完成快速检测。', 'info');
    },
    onError: (error) => pushNotice(errorText(error), 'error'),
  });
  useEffect(() => {
    if (recheck || !currentMember?.ready || !navigator.mediaDevices) return;
    const invalidate = () => {
      if (!invalidateDeviceMutation.isPending) invalidateDeviceMutation.mutate();
    };
    navigator.mediaDevices.addEventListener('devicechange', invalidate);
    return () => navigator.mediaDevices.removeEventListener('devicechange', invalidate);
  }, [currentMember?.ready, invalidateDeviceMutation, recheck]);
  async function testMicrophone() {
    if (preparationState === 'PROBING' || preparationMutation.isPending) return;
    probeAbortRef.current?.abort();
    const controller = new AbortController();
    probeAbortRef.current = controller;
    preparationMutation.reset();
    setPlaybackConfirmed(false);
    setPlaybackPlaying(false);
    setPreparationState('PROBING');
    const toneStarted = await runSpeakerProbe(playSpeakerTone);
    setProbeResult(null);
    try {
      if (!toneStarted) throw new Error('speaker_tone_failed');
      const result = await runLiveKitDeviceProbe(controller.signal);
      if (recordingUrl) URL.revokeObjectURL(recordingUrl);
      setRecordingUrl(result.recordingBlob ? URL.createObjectURL(result.recordingBlob) : null);
      setProbeResult(result);
      if (result.status === 'FAIL') {
        setPreparationState('FAIL_RETRY');
        pushNotice('检测未通过。请确认麦克风有声音、网络稳定后重新检测。', 'error');
      } else if (result.status === 'WARN') {
        setPreparationState('WARN_CONFIRM');
      } else {
        preparationMutation.mutate({ result });
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      const message =
        error instanceof ApiClientError
          ? error.status >= 500
            ? '实时网络检测服务暂时不可用，请稍后重新检测。'
            : error.message
          : error instanceof Error && error.message === 'speaker_tone_failed'
            ? '扬声器测试音播放失败，请检查系统音频输出后重试。'
            : '麦克风或实时网络检测失败。请检查录音权限、设备连接和网络后重试。';
      setPreparationState('ERROR_RETRY');
      pushNotice(message, 'error');
    } finally {
      if (probeAbortRef.current === controller) probeAbortRef.current = null;
    }
  }

  async function playSpeakerTone(): Promise<boolean> {
    try {
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.frequency.value = 523.25;
      gain.gain.setValueAtTime(0.0001, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.12, context.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.4);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.42);
      oscillator.addEventListener('ended', () => void context.close(), { once: true });
      return true;
    } catch {
      return false;
    }
  }

  async function copyText(value: string, successMessage: string) {
    try {
      await navigator.clipboard.writeText(value);
      pushNotice(successMessage, 'success');
    } catch {
      setInviteOpen(true);
      pushNotice('自动复制失败，请在邀请区域手动选择并复制。', 'error');
    }
  }

  async function playRecordingAndTone() {
    const recording = recordingRef.current;
    if (!recording) {
      pushNotice('没有取得可播放的录音，请重新检测。', 'error');
      return;
    }
    setPlaybackPlaying(true);
    setPlaybackConfirmed(false);
    try {
      recording.currentTime = 0;
      await new Promise<void>((resolve, reject) => {
        const finish = () => {
          recording.removeEventListener('error', fail);
          resolve();
        };
        const fail = () => {
          recording.removeEventListener('ended', finish);
          reject(new Error('recording_playback_failed'));
        };
        recording.addEventListener('ended', finish, { once: true });
        recording.addEventListener('error', fail, { once: true });
        void recording.play().catch(fail);
      });
      if (!(await playSpeakerTone())) throw new Error('speaker_tone_failed');
      setPlaybackConfirmed(true);
    } catch {
      pushNotice('声音播放失败，请检查扬声器后重新播放。', 'error');
    } finally {
      setPlaybackPlaying(false);
    }
  }

  const hasOnlineSuccessor = Boolean(
    room?.members.some((member) => member.user_id !== currentUserId && member.online),
  );
  const leaveDescription =
    isOrganizer && !hasOnlineSuccessor
      ? '退出后房间将立即关闭，之后无法重新进入。'
      : '退出后会释放当前席位和准备状态；只返回大厅不会产生这些影响。';

  if (roomQuery.isPending)
    return (
      <main className="jx-page-grid jx-page-viewport grid place-items-center">
        <LoaderCircle className="size-8 animate-spin text-blue-600" aria-label="正在加载房间" />
      </main>
    );
  if (!room)
    return (
      <main className="jx-page-grid jx-page-viewport grid place-items-center px-6">
        <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-center">
          <CircleAlert className="mx-auto size-8 text-red-600" />
          <h1 className="mt-4 text-xl font-black">房间无法打开</h1>
          <p className="mt-2 text-sm text-slate-600">请返回大厅后重试，或稍后重新打开房间。</p>
          <Link
            className="mt-5 inline-flex rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-bold text-white"
            href="/lobby"
          >
            返回大厅
          </Link>
        </div>
      </main>
    );
  if (['FINISHED', 'TERMINATED'].includes(room.status)) {
    return (
      <main className="jx-page-grid jx-page-viewport grid place-items-center px-6">
        <section className="max-w-lg rounded-[1.75rem] border border-blue-100 bg-white/90 p-8 text-center shadow-[0_24px_70px_rgba(40,76,142,0.12)]">
          <CircleAlert className="mx-auto size-9 text-blue-600" />
          <h1 className="mt-4 text-2xl font-black text-slate-950">
            {room.status === 'FINISHED' ? '比赛已经结束' : '房间已经关闭'}
          </h1>
          <p className="mt-3 text-sm leading-7 text-slate-600">
            {room.status === 'FINISHED'
              ? '本场比赛已进入赛后记录，不需要再次加入或检测设备。'
              : '这个房间已经终止，无法再次加入。你可以返回大厅选择其他房间。'}
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Link
              className="inline-flex min-h-11 items-center rounded-xl border border-blue-100 bg-white px-4 py-2.5 text-sm font-bold text-slate-700"
              href="/lobby"
            >
              返回大厅
            </Link>
            {room.status === 'FINISHED' && room.match_id ? (
              <Link
                className="inline-flex min-h-11 items-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-black !text-white"
                href={`/matches/${room.match_id}`}
              >
                查看赛后记录
              </Link>
            ) : null}
          </div>
        </section>
      </main>
    );
  }
  if (!recheck && ['START_PENDING_RUNTIME', 'RUNNING', 'PAUSED'].includes(room.status)) {
    const alreadyJoined = room.viewer_membership_state === 'ACTIVE';
    return (
      <main className="jx-page-grid jx-page-viewport px-6 py-7">
        <div className="mx-auto w-full max-w-4xl">
          <section className="mt-8 overflow-hidden rounded-[2rem] border border-blue-100 bg-white/90 p-8 shadow-[0_26px_80px_rgba(40,76,142,0.12)] sm:p-12">
            <div className="mx-auto max-w-2xl text-center">
              <span className="inline-flex items-center gap-2 rounded-full bg-lime-100 px-3 py-1.5 text-xs font-black text-lime-800">
                <Radio className="size-4" />
                {room.status === 'RUNNING'
                  ? '比赛进行中'
                  : room.status === 'PAUSED'
                    ? '比赛已暂停'
                    : '正在开赛'}
              </span>
              <h1 className="mt-5 text-3xl font-black tracking-[-0.05em] text-slate-950">
                {room.title}
              </h1>
              <p className="mt-3 text-sm leading-7 text-slate-600">
                {alreadyJoined
                  ? room.match_id
                    ? '正在返回比赛现场，请稍候。'
                    : '房间已经锁定，正在建立比赛运行时。'
                  : '比赛已经开始，只能以观众身份加入；观众不会开启麦克风，也没有比赛控制权限。'}
              </p>
              <div className="mt-8 flex flex-wrap justify-center gap-3">
                <Link
                  className="inline-flex min-h-11 items-center rounded-xl border border-blue-100 bg-white px-4 py-2.5 text-sm font-bold text-slate-700"
                  href="/lobby"
                >
                  返回大厅
                </Link>
                {!alreadyJoined ? (
                  <Button
                    disabled={joinMutation.isPending}
                    onClick={() => joinMutation.mutate('SPECTATOR')}
                    variant="primary"
                  >
                    {joinMutation.isPending ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <Radio className="size-4" />
                    )}
                    作为观众进入比赛
                  </Button>
                ) : isOrganizer && !room.match_id ? (
                  <Button
                    disabled={startGate.isPending}
                    onClick={() => {
                      if (startGate.tryStart()) runtimeStartMutation.mutate();
                    }}
                    variant="primary"
                  >
                    {startGate.isPending ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <RefreshCcw className="size-4" />
                    )}
                    继续启动比赛
                  </Button>
                ) : (
                  <span className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-blue-50 px-4 py-2.5 text-sm font-bold text-blue-700">
                    <LoaderCircle className="size-4 animate-spin" /> 等待比赛入口
                  </span>
                )}
              </div>
            </div>
          </section>
        </div>
      </main>
    );
  }
  const sideSize = Number(room.rule.side_size ?? 1);
  const ownSeat = room.seats.find((seat) => seat.user_id === currentUserId);
  const reusableCheck =
    !recheck && room.latest_device_check?.is_valid ? room.latest_device_check : null;
  const preparationFlow = derivePreparationFlow({
    hasActiveMembership: Boolean(currentMember),
    memberRole: currentMember?.member_role,
    hasOwnSeat: Boolean(ownSeat),
    hasValidDeviceCheck: Boolean(room.latest_device_check?.is_valid),
    ready: Boolean(currentMember?.ready),
    recheck,
    deviceResultStatus: probeResult?.status,
  });
  const startBlockers = deriveRoomStartBlockers({ members: room.members, seats: room.seats });

  return (
    <main className="jx-page-grid jx-page-viewport px-5 py-6 sm:px-8">
      <div className="mx-auto max-w-[1500px]">
        <section className="mt-2 overflow-hidden rounded-[1.75rem] border border-blue-100 bg-white/90 shadow-[0_26px_80px_rgba(40,76,142,0.11)]">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-blue-100 px-6 py-5">
            <div>
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-lime-100 px-2.5 py-1 text-[11px] font-black text-lime-800">
                  {room.status === 'WAITING' ? '准备中' : room.status}
                </span>
                <button
                  className="inline-flex items-center gap-1.5 rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-xs font-black tracking-[0.18em] text-slate-700 transition hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                  onClick={() => setInviteOpen((open) => !open)}
                  type="button"
                >
                  <Link2 className="size-3.5" /> {room.code}
                </button>
              </div>
              <h1 className="mt-3 text-2xl font-black tracking-[-0.04em] text-slate-950">
                {room.title}
              </h1>
              <p className="mt-1 text-sm text-slate-500">{String(room.topic.title ?? '')}</p>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              {!recheck ? (
                <Button onClick={() => setInviteOpen((open) => !open)} variant="secondary">
                  <ClipboardCopy className="size-4" /> 邀请加入
                </Button>
              ) : null}
              <Link
                className={buttonVariants({ variant: 'secondary', size: 'md' })}
                href={
                  recheck && room.match_id
                    ? `/debate?match_id=${encodeURIComponent(room.match_id)}`
                    : '/lobby'
                }
              >
                <ArrowLeft className="size-4" />
                {recheck ? '返回比赛' : '返回大厅'}
              </Link>
              {currentMember && !recheck ? (
                <Button
                  disabled={leaveMutation.isPending}
                  onClick={() => setLeaveConfirmOpen(true)}
                  variant="danger"
                >
                  <LogOut className="size-4" />
                  退出房间
                </Button>
              ) : null}
              {isOrganizer && !recheck ? (
                <div className="grid justify-items-end gap-1.5">
                  <Button
                    disabled={
                      startGate.isPending || room.status !== 'WAITING' || startBlockers.length > 0
                    }
                    onClick={() => {
                      if (startGate.tryStart()) startMutation.mutate();
                    }}
                    variant="primary"
                  >
                    {startGate.isPending ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <Play className="size-4" />
                    )}
                    {startGate.isPending ? '正在启动' : '开始比赛'}
                  </Button>
                  {room.status === 'WAITING' && startBlockers[0] ? (
                    <p
                      className="max-w-64 text-right text-[11px] font-bold leading-4 text-slate-600"
                      role="status"
                    >
                      {startBlockers[0]}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
          {inviteOpen && !recheck ? (
            <section
              className="border-b border-blue-100 bg-[#10233e] px-6 py-5 text-white sm:px-7"
              aria-label="邀请加入"
            >
              <div className="flex flex-wrap items-center justify-between gap-5">
                <div>
                  <p className="text-[10px] font-black tracking-[0.18em] text-lime-300">
                    INVITE ROOM
                  </p>
                  <h2 className="mt-1 text-lg font-black">分享房间，邀请辩手或观众</h2>
                  <p className="mt-1 text-xs text-slate-300">
                    房间公开可见，打开链接后由对方选择加入身份。
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded-xl border border-white/10 bg-white/10 px-4 py-3 font-mono text-lg font-black tracking-[0.2em] text-lime-200">
                    {room.code}
                  </code>
                  <Button
                    onClick={() => void copyText(room.code, '房间号已复制。')}
                    variant="primary"
                  >
                    <ClipboardCopy className="size-4" /> 复制房间号
                  </Button>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <input
                  aria-label="邀请链接"
                  className="min-h-10 min-w-[18rem] flex-1 rounded-xl border border-white/10 bg-white/10 px-3 font-mono text-xs text-slate-200 outline-none focus:border-lime-300"
                  onFocus={(event) => event.currentTarget.select()}
                  readOnly
                  value={inviteLink}
                />
                <Button
                  className="!text-[#1e2a3a]"
                  onClick={() => void copyText(inviteLink, '邀请链接已复制。')}
                  variant="secondary"
                >
                  <Link2 className="size-4" /> 复制邀请链接
                </Button>
              </div>
            </section>
          ) : null}
          <PreparationProgress flow={preparationFlow} />
          <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="p-5 sm:p-7">
              <div className="grid gap-6 lg:grid-cols-2">
                <section>
                  <h2 className="flex items-center gap-2 text-sm font-black text-red-700">
                    <span className="size-2 rounded-full bg-red-500" />
                    正方席位
                  </h2>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    {Array.from({ length: sideSize }, (_, index) => (
                      <RoomSeatCard
                        key={index + 1}
                        room={room}
                        side="AFFIRMATIVE"
                        seatNo={index + 1}
                        currentUserId={currentUserId}
                        disabled={
                          !currentMember || room.status !== 'WAITING' || seatMutation.isPending
                        }
                        loading={
                          seatMutation.isPending &&
                          seatMutation.variables?.side === 'AFFIRMATIVE' &&
                          seatMutation.variables.seat_no === index + 1
                        }
                        onSelect={() =>
                          seatMutation.mutate({ side: 'AFFIRMATIVE', seat_no: index + 1 })
                        }
                        onRequestSwap={(targetUserId) => {
                          const target = room.seats.find((seat) => seat.user_id === targetUserId);
                          setSwapTarget({
                            userId: targetUserId,
                            name: target?.occupant_name ?? '该辩手',
                          });
                        }}
                      />
                    ))}
                  </div>
                </section>
                <section>
                  <h2 className="flex items-center gap-2 text-sm font-black text-blue-700">
                    <span className="size-2 rounded-full bg-blue-500" />
                    反方席位
                  </h2>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    {Array.from({ length: sideSize }, (_, index) => (
                      <RoomSeatCard
                        key={index + 1}
                        room={room}
                        side="NEGATIVE"
                        seatNo={index + 1}
                        currentUserId={currentUserId}
                        disabled={
                          !currentMember || room.status !== 'WAITING' || seatMutation.isPending
                        }
                        loading={
                          seatMutation.isPending &&
                          seatMutation.variables?.side === 'NEGATIVE' &&
                          seatMutation.variables.seat_no === index + 1
                        }
                        onSelect={() =>
                          seatMutation.mutate({ side: 'NEGATIVE', seat_no: index + 1 })
                        }
                        onRequestSwap={(targetUserId) => {
                          const target = room.seats.find((seat) => seat.user_id === targetUserId);
                          setSwapTarget({
                            userId: targetUserId,
                            name: target?.occupant_name ?? '该辩手',
                          });
                        }}
                      />
                    ))}
                  </div>
                </section>
              </div>
              <section className="mt-7 rounded-2xl border border-slate-200 bg-slate-50/75 p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="font-black text-slate-900">房间成员 · {room.members.length}</h2>
                  {currentMember?.member_role === 'DEBATER' ? (
                    <Button
                      className="border-blue-600 bg-blue-600 !text-white shadow-[0_8px_22px_rgba(37,99,235,0.22)] hover:border-blue-700 hover:bg-blue-700"
                      disabled={roleMutation.isPending || room.status !== 'WAITING'}
                      onClick={() => roleMutation.mutate('SPECTATOR')}
                      variant="secondary"
                    >
                      {roleMutation.isPending ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : (
                        <Radio className="size-4" />
                      )}
                      切换为观众
                    </Button>
                  ) : currentMember?.member_role === 'SPECTATOR' ? (
                    <Button
                      className="border-blue-600 bg-blue-600 !text-white shadow-[0_8px_22px_rgba(37,99,235,0.22)] hover:border-blue-700 hover:bg-blue-700"
                      disabled={
                        roleMutation.isPending || room.status !== 'WAITING' || !termsQuery.data
                      }
                      onClick={() => roleMutation.mutate('DEBATER')}
                      variant="secondary"
                    >
                      {roleMutation.isPending ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : (
                        <Mic className="size-4" />
                      )}
                      切换为辩手
                    </Button>
                  ) : null}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {room.members.map((member) => (
                    <span
                      className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700"
                      key={member.user_id}
                    >
                      <span
                        className={`size-2 rounded-full ${member.online ? 'bg-emerald-500' : 'bg-slate-300'}`}
                      />
                      {member.real_name}
                      <small className="text-slate-600">
                        {member.member_role === 'SPECTATOR'
                          ? '观众'
                          : member.ready
                            ? '已准备'
                            : '未准备'}
                      </small>
                    </span>
                  ))}
                </div>
              </section>
            </div>
            <aside className="border-t border-blue-100 bg-[#142647] p-6 text-white xl:border-l xl:border-t-0">
              <p className="text-xs font-black tracking-[0.16em] text-lime-300">CURRENT STEP</p>
              <h2 className="mt-3 text-xl font-black">
                {recheck
                  ? '恢复前设备复检'
                  : !currentMember
                    ? '先选择加入身份'
                    : preparationFlow.isSpectator
                      ? '观众席已就绪'
                      : preparationFlow.activeStep === 2
                        ? '选择你的辩手席位'
                        : currentMember.ready
                          ? '准备已经完成'
                          : '设备检测与准备'}
              </h2>
              <p className="mt-2 text-xs leading-5 text-slate-300">
                {preparationFlow.nextAction}。
              </p>
              {!currentMember ? (
                <div className="mt-6 space-y-3">
                  <button
                    className="flex min-h-20 w-full items-center justify-between rounded-2xl border border-lime-300/45 bg-lime-300/15 p-4 text-left transition hover:-translate-y-0.5 hover:bg-lime-300/20 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-lime-300/30 disabled:cursor-not-allowed disabled:border-slate-500 disabled:bg-slate-700 disabled:text-slate-400 disabled:shadow-none"
                    disabled={joinMutation.isPending || !termsQuery.data}
                    onClick={() => joinMutation.mutate('DEBATER')}
                    type="button"
                  >
                    <span>
                      <strong className="block text-sm text-white">
                        {room.viewer_membership_state === 'LEFT'
                          ? '重新作为辩手加入'
                          : '作为辩手加入'}
                      </strong>
                      <small className="mt-1 block text-xs text-slate-300">
                        选席后完成设备检测
                      </small>
                    </span>
                    <Mic className="size-5 text-lime-300" />
                  </button>
                  <button
                    className="flex min-h-20 w-full items-center justify-between rounded-2xl border border-blue-200/70 bg-[#22385d] p-4 text-left shadow-[0_8px_20px_rgba(4,15,39,0.18)] transition hover:-translate-y-0.5 hover:border-blue-100 hover:bg-[#2b4773] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-300/50 disabled:cursor-not-allowed disabled:border-slate-500 disabled:bg-slate-700 disabled:text-slate-400 disabled:shadow-none"
                    disabled={joinMutation.isPending}
                    onClick={() => joinMutation.mutate('SPECTATOR')}
                    type="button"
                  >
                    <span>
                      <strong className="block text-sm text-white">
                        {room.viewer_membership_state === 'LEFT'
                          ? '重新作为观众加入'
                          : '作为观众加入'}
                      </strong>
                      <small className="mt-1 block text-xs text-slate-300">
                        无需麦克风和设备检测
                      </small>
                    </span>
                    <Radio className="size-5 text-blue-300" />
                  </button>
                </div>
              ) : preparationFlow.isSpectator ? (
                <div className="mt-6 rounded-2xl border border-blue-300/30 bg-blue-300/10 p-5">
                  <div className="flex items-center gap-2 text-base font-black text-blue-100">
                    <CheckCircle2 className="size-5 text-lime-300" /> 观众席已就绪
                  </div>
                  <p className="mt-2 text-xs leading-5 text-slate-300">
                    等待房主开始比赛。观众不会开启麦克风，也没有比赛控制权限。
                  </p>
                  <Button
                    className="mt-4 w-full"
                    disabled={
                      roleMutation.isPending || room.status !== 'WAITING' || !termsQuery.data
                    }
                    onClick={() => roleMutation.mutate('DEBATER')}
                    variant="primary"
                  >
                    <Mic className="size-4" /> 切换为辩手
                  </Button>
                </div>
              ) : preparationFlow.isHumanParticipant ? (
                <div className="mt-6 space-y-4">
                  {currentMember.ready && !recheck ? (
                    <div className="rounded-2xl border border-lime-300/30 bg-lime-300/10 p-5 text-sm leading-6 text-lime-100">
                      <div className="flex items-center gap-2 text-base font-black text-lime-200">
                        <CheckCircle2 className="size-5" /> 已准备
                      </div>
                      <p className="mt-2 text-lime-100/80">等待房主完成其他席位后开始比赛。</p>
                      {recordingUrl ? (
                        <>
                          <audio
                            ref={recordingRef}
                            className="sr-only"
                            preload="metadata"
                            src={recordingUrl}
                          />
                          <button
                            className="mt-3 inline-flex items-center gap-1.5 text-xs font-bold text-lime-200 underline decoration-lime-300/40 underline-offset-4"
                            onClick={() => void playRecordingAndTone()}
                            type="button"
                          >
                            <Headphones className="size-3.5" /> 可选：试听刚才的录音
                          </button>
                        </>
                      ) : null}
                    </div>
                  ) : reusableCheck && !probeResult ? (
                    <div className="rounded-2xl border border-lime-300/30 bg-lime-300/10 p-5">
                      <div className="flex items-start gap-3">
                        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-lime-300" />
                        <div>
                          <strong className="block text-sm text-lime-100">上次检测仍有效</strong>
                          <p className="mt-1 text-xs leading-5 text-lime-100/75">
                            有效至{' '}
                            {new Date(reusableCheck.valid_until).toLocaleTimeString('zh-CN', {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                            ，无需重复录音。
                          </p>
                        </div>
                      </div>
                      <Button
                        className="mt-4 w-full"
                        disabled={preparationMutation.isPending}
                        onClick={() =>
                          preparationMutation.mutate({ version: reusableCheck.check_version })
                        }
                        variant="primary"
                      >
                        {preparationMutation.isPending ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <ShieldCheck className="size-4" />
                        )}
                        直接使用检测并准备
                      </Button>
                      <button
                        className="mt-3 flex w-full items-center justify-center gap-2 text-xs font-bold text-slate-300 hover:text-white"
                        onClick={testMicrophone}
                        type="button"
                      >
                        <RefreshCcw className="size-3.5" /> 重新完整检测
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        aria-busy={preparationState === 'PROBING' || preparationMutation.isPending}
                        className="flex w-full items-center justify-between rounded-2xl border border-lime-300/70 bg-[#36520d] p-5 text-left shadow-[0_10px_24px_rgba(12,30,2,0.22)] transition hover:border-lime-200 hover:bg-[#466b12] disabled:cursor-wait disabled:border-blue-300 disabled:bg-blue-700 disabled:text-white disabled:shadow-[0_10px_24px_rgba(29,78,216,0.2)] disabled:opacity-100"
                        disabled={
                          preparationState === 'PROBING' ||
                          playbackPlaying ||
                          preparationMutation.isPending
                        }
                        onClick={testMicrophone}
                        type="button"
                      >
                        <span className="flex items-center gap-3">
                          <span className="grid size-11 place-items-center rounded-xl bg-lime-300 text-slate-950">
                            {preparationState === 'PROBING' || preparationMutation.isPending ? (
                              <LoaderCircle className="size-5 animate-spin" />
                            ) : (
                              <Mic className="size-5" />
                            )}
                          </span>
                          <span>
                            <strong className="block text-sm text-white">
                              {preparationState === 'PROBING'
                                ? '正在检测，请说话 3 秒'
                                : preparationState === 'SAVING'
                                  ? '正在保存检测结果'
                                  : preparationState === 'READYING'
                                    ? '正在自动完成准备'
                                    : probeResult
                                      ? '重新检测'
                                      : '开始设备检测'}
                            </strong>
                            <small className="mt-1 block text-xs text-slate-300">
                              {preparationState === 'PROBING'
                                ? '保持说话，系统正在并行检查实时网络'
                                : probeResult
                                  ? '重新检查会替换当前结果'
                                  : 'PASS 后自动保存并准备，无需额外确认'}
                            </small>
                          </span>
                        </span>
                        {preparationState !== 'PROBING' && !preparationMutation.isPending ? (
                          <ArrowLeft className="size-5 rotate-180 text-lime-300" />
                        ) : null}
                      </button>
                      {probeResult && probeResult.status !== 'FAIL' ? (
                        <div
                          className={`rounded-xl border p-4 text-sm leading-6 ${probeResult.status === 'PASS' ? 'border-lime-300/25 bg-lime-300/10 text-lime-100' : 'border-amber-300/30 bg-amber-300/10 text-amber-100'}`}
                        >
                          <strong className="block">
                            {probeResult.status === 'PASS'
                              ? '麦克风和网络正常'
                              : '网络稍弱，可以确认后继续'}
                          </strong>
                          <details className="mt-2 text-xs opacity-80">
                            <summary className="cursor-pointer font-bold">查看检测详情</summary>
                            <div className="mt-2 space-y-1">
                              <p>
                                麦克风输入：
                                {probeResult.inputPeak === null
                                  ? '未取得'
                                  : `${Math.round(probeResult.inputPeak * 1000) / 10}%`}
                              </p>
                              <p>
                                网络延迟：
                                {probeResult.rttP95Ms === null
                                  ? '暂未取得'
                                  : `${Math.round(probeResult.rttP95Ms)} ms`}
                              </p>
                              <p>
                                网络丢包：
                                {probeResult.packetLossP95Percent === null
                                  ? '暂未取得'
                                  : `${probeResult.packetLossP95Percent.toFixed(1)}%`}
                              </p>
                            </div>
                          </details>
                        </div>
                      ) : null}
                      {probeResult?.status === 'WARN' ? (
                        <div className="rounded-xl border border-amber-300/40 bg-amber-300/15 p-4 text-xs leading-5 text-amber-50 shadow-[0_10px_24px_rgba(0,0,0,0.1)]">
                          <p className="font-bold">网络指标存在波动，可能影响实时语音。</p>
                          <p className="mt-1 text-amber-100/80">你可以确认后继续，或重新检测。</p>
                          <Button
                            className="mt-3 w-full"
                            disabled={preparationMutation.isPending}
                            onClick={() => preparationMutation.mutate({ result: probeResult })}
                            variant="primary"
                          >
                            <ShieldCheck className="size-4" /> 确认网络警告并准备
                          </Button>
                        </div>
                      ) : null}
                      {recordingUrl && probeResult?.status !== 'FAIL' ? (
                        <div className="rounded-xl border border-white/10 bg-white/5 p-4">
                          <p className="text-sm font-bold text-white">可选：试听刚才的录音</p>
                          <p className="mt-1 text-xs leading-5 text-slate-300">
                            不影响准备状态；用于确认设备是否选对。
                          </p>
                          <audio
                            ref={recordingRef}
                            className="sr-only"
                            preload="metadata"
                            src={recordingUrl}
                          />
                          <Button
                            className="mt-3 w-full !text-[#1e2a3a]"
                            disabled={playbackPlaying || preparationMutation.isPending}
                            onClick={() => void playRecordingAndTone()}
                            variant="secondary"
                          >
                            {playbackPlaying ? (
                              <LoaderCircle className="size-4 animate-spin" />
                            ) : playbackConfirmed ? (
                              <CheckCircle2 className="size-4" />
                            ) : (
                              <Headphones className="size-4" />
                            )}
                            {playbackPlaying
                              ? '正在播放'
                              : playbackConfirmed
                                ? '重新试听录音'
                                : '试听录音'}
                          </Button>
                          {playbackConfirmed ? (
                            <p className="mt-3 text-xs font-bold text-lime-300" role="status">
                              录音播放正常。
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              ) : (
                <div className="mt-6 rounded-xl border border-white/10 bg-white/5 p-4 text-sm leading-6 text-slate-300">
                  请在左侧选择一个正方或反方席位。选定后，这里会立即显示设备检测按钮。
                </div>
              )}
              <div className="mt-8 border-t border-white/10 pt-5 text-xs leading-5 text-slate-400">
                <p>赛制：{String(room.rule.name ?? '')}</p>
                <p className="mt-1">
                  规模：{sideSize}v{sideSize}
                </p>
              </div>
            </aside>
          </div>
        </section>
      </div>
      <ConfirmDialog
        confirmLabel={isOrganizer && !hasOnlineSuccessor ? '退出并关闭房间' : '确认退出'}
        description={leaveDescription}
        loading={leaveMutation.isPending}
        onConfirm={() => leaveMutation.mutate()}
        onOpenChange={setLeaveConfirmOpen}
        open={leaveConfirmOpen}
        title={isOrganizer && !hasOnlineSuccessor ? '退出并关闭房间？' : '退出房间？'}
      />
      <ConfirmDialog
        confirmLabel="发送交换申请"
        description={
          swapTarget
            ? `将向 ${swapTarget.name} 发送席位交换申请，对方同意后两人的席位才会互换。`
            : ''
        }
        loading={swapMutation.isPending}
        onConfirm={() => {
          if (swapTarget) swapMutation.mutate(swapTarget.userId);
        }}
        onOpenChange={(open) => {
          if (!open) setSwapTarget(null);
        }}
        open={swapTarget !== null}
        title="申请交换席位？"
        tone="primary"
      />
      {swapRequests
        .filter((item) => item.target_user_id === currentUserId && item.status === 'PENDING')
        .map((item) => (
          <ConfirmDialog
            key={item.id}
            confirmLabel="同意交换"
            cancelLabel="拒绝"
            description={`${item.requester_name} 申请与你交换当前辩手席位。`}
            loading={respondSwapMutation.isPending && respondSwapMutation.variables?.id === item.id}
            onConfirm={() => respondSwapMutation.mutate({ id: item.id, decision: 'ACCEPT' })}
            onOpenChange={(open) => {
              if (!open) respondSwapMutation.mutate({ id: item.id, decision: 'REJECT' });
            }}
            open
            title="收到席位交换申请"
            tone="primary"
          />
        ))}
    </main>
  );
}
