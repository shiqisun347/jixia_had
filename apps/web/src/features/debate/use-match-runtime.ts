'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { MatchCommand, MatchSnapshot, MatchTranscript } from '@/lib/matches-api';
import { matchesApi } from '@/lib/matches-api';
import { useAppTranslations } from '@/i18n';

declare global {
  interface Window {
    __JX_CORE_WS_ORIGIN__?: string;
    __JX_MATCH_SOCKET_FACTORY__?: (url: string) => WebSocket;
  }
}

export type MatchSocketStatus = 'connecting' | 'open' | 'closed' | 'error';

type MatchSocketEvent = {
  match_id?: string;
  type?: string;
  sequence?: number;
  connection_epoch?: number;
  message_id?: string;
  duplicate?: boolean;
  code?: string;
  message?: string;
  payload?: { text?: string; speech_id?: string; reasons?: string[] };
  snapshot?: MatchSnapshot;
};

export function matchSocketErrorText(
  event: MatchSocketEvent,
  t?: (key: string, values?: Record<string, string | number | Date>) => string,
): string | null | undefined {
  if (event.type === 'command.error') {
    if (event.code === 'asr_finalization_pending') {
      return (
        t?.('runtime.finalizationPending') ??
        '上一段发言仍在整理，麦克风尚未开启。请稍后再次点击开始发言。'
      );
    }
    return event.message ?? t?.('runtime.commandFailed') ?? '比赛指令未执行，请刷新后重试。';
  }
  if (event.type === 'match.resume_check_failed') {
    const reasons = event.payload?.reasons?.filter(Boolean) ?? [];
    return reasons.length > 0
      ? (t?.('runtime.recoveryRequirements', { reasons: reasons.join('；') }) ??
          `恢复条件未满足：${reasons.join('；')}`)
      : (t?.('runtime.recoveryFallback') ?? '恢复条件未满足，请检查辩手在线状态与设备。');
  }
  if (event.type === 'match.resume_countdown') return null;
  return undefined;
}

export function newestMatchSnapshot(
  current: MatchSnapshot | undefined,
  incoming: MatchSnapshot,
): MatchSnapshot {
  if (current && incoming.sequence < current.sequence) return current;
  // A compatibility snapshot may omit transient text. Preserve it only for
  // the same speech; never carry a previous speaker's draft into the next turn.
  if (
    !Object.prototype.hasOwnProperty.call(incoming, 'interim_text') &&
    current?.interim_text &&
    current.current_speech_id === incoming.current_speech_id
  ) {
    return {
      ...incoming,
      interim_text: current.interim_text,
    };
  }
  return incoming;
}

function websocketUrl(matchId: string): string {
  const configured = window.__JX_CORE_WS_ORIGIN__;
  const base = configured ? new URL(configured) : new URL(window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  base.pathname = `/api/matches/${matchId}/events`;
  base.search = '';
  return base.toString();
}

function createMatchSocket(matchId: string): WebSocket {
  const url = websocketUrl(matchId);
  const isLocal =
    window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
  if (isLocal && window.__JX_MATCH_SOCKET_FACTORY__) {
    return window.__JX_MATCH_SOCKET_FACTORY__(url);
  }
  return new WebSocket(url);
}

export function useMatchRuntime(matchId: string) {
  const t = useAppTranslations('Match');
  const tRef = useRef(t);
  const queryClient = useQueryClient();
  const readSnapshot = useCallback(async () => {
    const key = ['matches', matchId, 'snapshot'];
    const before = queryClient.getQueryData<MatchSnapshot>(key);
    const incoming = await matchesApi.snapshot(matchId);
    const current = queryClient.getQueryData<MatchSnapshot>(key);
    // A WS snapshot or subtitle can arrive while HTTP is in flight, including
    // multiple subtitle revisions at the same authoritative sequence.
    if (current && current !== before && incoming.sequence <= current.sequence) return current;
    return newestMatchSnapshot(current, incoming);
  }, [matchId, queryClient]);
  const query = useQuery({
    queryKey: ['matches', matchId, 'snapshot'],
    queryFn: readSnapshot,
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  });
  const socketRef = useRef<WebSocket | null>(null);
  const connectionEpochRef = useRef<number | null>(null);
  const latestSequenceRef = useRef(0);
  const pendingRef = useRef(
    new Map<
      string,
      { resolve: (success: boolean) => void; timeout: ReturnType<typeof setTimeout> }
    >(),
  );
  const [socketStatus, setSocketStatus] = useState<MatchSocketStatus>('connecting');
  const [connectionEpoch, setConnectionEpoch] = useState<number | null>(null);
  const [latestSequence, setLatestSequence] = useState(0);
  const [socketError, setSocketError] = useState<string | null>(null);
  const [commandErrorCode, setCommandErrorCode] = useState<string | null>(null);
  const commandErrorRef = useRef<string | null>(null);
  const [resumeReasons, setResumeReasons] = useState<string[]>(query.data?.resume_reasons ?? []);
  const interimText = query.data?.interim_text ?? '';
  const hasInitialSnapshot = Boolean(query.data);

  useEffect(() => {
    tRef.current = t;
  }, [t]);

  useEffect(() => {
    const sequence = query.data?.sequence;
    if (sequence === undefined || sequence <= latestSequenceRef.current) return;
    latestSequenceRef.current = sequence;
    setLatestSequence(sequence);
  }, [query.data?.sequence]);

  useEffect(() => {
    if (query.data?.context_version === undefined) return;
    const transcriptKey = ['matches', matchId, 'transcript'];
    const transcript = queryClient.getQueryData<MatchTranscript>(transcriptKey);
    if (transcript && transcript.context_version < query.data.context_version) {
      void queryClient.invalidateQueries({ queryKey: transcriptKey });
    }
  }, [matchId, query.data?.context_version, queryClient]);

  useEffect(() => {
    if (!hasInitialSnapshot) return;
    let disposed = false;
    let reconnectAttempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    const pending = pendingRef.current;
    const snapshotKey = ['matches', matchId, 'snapshot'];
    let transcriptRefreshQueued = false;
    const refreshTranscript = () => {
      if (transcriptRefreshQueued) return;
      transcriptRefreshQueued = true;
      queueMicrotask(() => {
        transcriptRefreshQueued = false;
        if (!disposed) {
          // A completion may arrive during an older request. Supersede that
          // read instead of accepting its pre-commit text as the final result.
          void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'transcript'] });
        }
      });
    };
    const updateInterim = (speechId: string | undefined, text: string, sequence?: number) => {
      if (!speechId) return;
      queryClient.setQueryData<MatchSnapshot>(snapshotKey, (current) =>
        current?.current_speech_id === speechId &&
        (sequence === undefined || sequence >= current.sequence)
          ? { ...current, interim_text: text }
          : current,
      );
    };
    const settlePendingCommands = () => {
      for (const item of pending.values()) {
        clearTimeout(item.timeout);
        item.resolve(false);
      }
      pending.clear();
    };
    const onMessage = (message: MessageEvent) => {
      try {
        const event = JSON.parse(String(message.data)) as MatchSocketEvent;
        if (event.match_id && event.match_id !== matchId) return;
        const observeSequence = (sequence: number | undefined) => {
          if (sequence === undefined || sequence <= latestSequenceRef.current) return;
          latestSequenceRef.current = sequence;
          setLatestSequence(sequence);
        };
        if (event.type !== 'match.snapshot') observeSequence(event.sequence);
        if (event.type === 'match.snapshot') {
          const snapshot = (event as unknown as { payload: MatchSnapshot }).payload;
          const current = queryClient.getQueryData<MatchSnapshot>(snapshotKey);
          if (!snapshot || !Number.isSafeInteger(snapshot.sequence)) {
            setSocketError(tRef.current('runtime.eventInvalid'));
            return;
          }
          if (snapshot.sequence < Math.max(current?.sequence ?? 0, latestSequenceRef.current))
            return;
          if (event.connection_epoch) {
            connectionEpochRef.current = event.connection_epoch;
            setConnectionEpoch(event.connection_epoch);
          }
          observeSequence(snapshot.sequence);
          queryClient.setQueryData<MatchSnapshot>(['matches', matchId, 'snapshot'], (current) =>
            newestMatchSnapshot(current, snapshot),
          );
          setResumeReasons(snapshot.resume_reasons ?? []);
          setSocketError((current) =>
            snapshot.action_state === 'RECOVERY_REQUIRED' && current?.startsWith('恢复条件未满足')
              ? current
              : null,
          );
          if (snapshot.action_state !== 'RECOVERY_REQUIRED') setResumeReasons([]);
          refreshTranscript();
          void queryClient.invalidateQueries({
            queryKey: ['rooms', snapshot.room_id, 'snapshot'],
          });
        } else if (event.type === 'command.ack' && event.message_id) {
          setCommandErrorCode(null);
          if (event.snapshot) {
            const acknowledged = event.snapshot;
            observeSequence(acknowledged.sequence);
            const current = queryClient.getQueryData<MatchSnapshot>(snapshotKey);
            queryClient.setQueryData<MatchSnapshot>(['matches', matchId, 'snapshot'], (current) =>
              newestMatchSnapshot(current, acknowledged),
            );
            if (acknowledged.sequence >= (current?.sequence ?? 0)) {
              setResumeReasons(acknowledged.resume_reasons ?? []);
              setSocketError(null);
            }
          }
          const pending = pendingRef.current.get(event.message_id);
          if (pending) {
            clearTimeout(pending.timeout);
            pending.resolve(true);
            pendingRef.current.delete(event.message_id);
          }
        } else if (event.type === 'command.error' && event.message_id) {
          setCommandErrorCode(event.code ?? null);
          const translate = tRef.current;
          commandErrorRef.current =
            matchSocketErrorText(event, translate) ?? translate('runtime.commandFailed');
          setSocketError(commandErrorRef.current);
          const pending = pendingRef.current.get(event.message_id);
          if (pending) {
            clearTimeout(pending.timeout);
            pending.resolve(false);
            pendingRef.current.delete(event.message_id);
          }
        } else if (event.type === 'match.resume_check_failed') {
          setResumeReasons(event.payload?.reasons?.filter(Boolean) ?? []);
          const translate = tRef.current;
          setSocketError(
            matchSocketErrorText(event, translate) ?? translate('runtime.recoveryFallback'),
          );
        } else if (event.type === 'match.resume_countdown') {
          setResumeReasons([]);
          setSocketError(null);
        } else if (event.type === 'asr.interim' || event.type === 'agent.subtitle') {
          const current = queryClient.getQueryData<MatchSnapshot>(snapshotKey);
          if (event.sequence !== undefined && event.sequence < (current?.sequence ?? 0)) return;
          updateInterim(event.payload?.speech_id, event.payload?.text ?? '', event.sequence);
        } else if (
          event.type === 'transcript.updated' ||
          event.type === 'speech.finished' ||
          event.type === 'agent.finalized' ||
          event.type === 'speech.skipped' ||
          event.type === 'stage.reset' ||
          event.type === 'speech.reset' ||
          event.type === 'match.finished' ||
          event.type === 'match.terminated'
        ) {
          updateInterim(event.payload?.speech_id, '', event.sequence);
          refreshTranscript();
          void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'snapshot'] });
        } else if (
          event.type === 'asr.segment_final' ||
          event.type === 'asr.retry_required' ||
          event.type === 'agent.retrying'
        ) {
          if (event.type !== 'asr.segment_final')
            updateInterim(event.payload?.speech_id, '', event.sequence);
        } else if (event.type === 'agent.text_delta' || event.type === 'agent.text_generated') {
          // Unplayed model tokens are diagnostic input, never playback captions.
        } else if (event.type === 'match.online' || event.type === 'match.offline') {
          const snapshot = queryClient.getQueryData<MatchSnapshot>([
            'matches',
            matchId,
            'snapshot',
          ]);
          void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'snapshot'] });
          if (snapshot) {
            void queryClient.invalidateQueries({
              queryKey: ['rooms', snapshot.room_id, 'snapshot'],
            });
          }
        } else if (event.type !== 'command.ack') {
          void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'snapshot'] });
        }
      } catch {
        setSocketError(tRef.current('runtime.eventInvalid'));
      }
    };
    function scheduleReconnect() {
      if (disposed || reconnectTimer !== null) return;
      connectionEpochRef.current = null;
      setConnectionEpoch(null);
      setSocketStatus('connecting');
      setSocketError((current) =>
        current?.startsWith('恢复条件未满足') ? current : tRef.current('runtime.reconnecting'),
      );
      const delay = Math.min(500 * 2 ** reconnectAttempt, 5_000);
      reconnectAttempt += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delay);
    }
    function connect() {
      if (disposed) return;
      let socket: WebSocket;
      try {
        socket = createMatchSocket(matchId);
      } catch {
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;
      setSocketStatus('connecting');
      socket.addEventListener('open', () => {
        if (disposed || socketRef.current !== socket) return;
        reconnectAttempt = 0;
        setSocketStatus('open');
        setSocketError((current) =>
          current === tRef.current('runtime.reconnecting') ||
          current === tRef.current('runtime.reconnectFailed')
            ? null
            : current,
        );
        void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'snapshot'] });
        refreshTranscript();
      });
      socket.addEventListener('error', () => {
        if (disposed || socketRef.current !== socket) return;
        setSocketStatus('error');
        setSocketError(tRef.current('runtime.reconnectFailed'));
      });
      socket.addEventListener('close', () => {
        if (disposed || socketRef.current !== socket) return;
        socketRef.current = null;
        setSocketStatus('closed');
        settlePendingCommands();
        scheduleReconnect();
      });
      socket.addEventListener('message', (message) => {
        if (disposed || socketRef.current !== socket) return;
        onMessage(message);
      });
    }
    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      const socket = socketRef.current;
      socketRef.current = null;
      connectionEpochRef.current = null;
      socket?.close();
      settlePendingCommands();
    };
  }, [hasInitialSnapshot, matchId, queryClient]);

  const sendCommand = useCallback(
    async (
      command: Omit<MatchCommand, 'expected_sequence' | 'connection_epoch'>,
      isStillCurrent?: () => boolean,
    ) => {
      commandErrorRef.current = null;
      setCommandErrorCode(null);
      const queryKey = ['matches', matchId, 'snapshot'] as const;
      let snapshot = queryClient.getQueryData<MatchSnapshot>(queryKey);

      for (
        let attempt = 0;
        snapshot && snapshot.sequence < latestSequenceRef.current && attempt < 2;
        attempt += 1
      ) {
        try {
          snapshot = await queryClient.fetchQuery({
            queryKey,
            queryFn: readSnapshot,
            staleTime: 0,
          });
        } catch {
          return false;
        }
      }

      const socket = socketRef.current;
      const connectionEpoch = connectionEpochRef.current;
      snapshot = queryClient.getQueryData<MatchSnapshot>(queryKey);
      if (
        !socket ||
        socket.readyState !== WebSocket.OPEN ||
        !snapshot ||
        connectionEpoch === null ||
        (isStillCurrent && !isStillCurrent()) ||
        snapshot.sequence < latestSequenceRef.current
      ) {
        return false;
      }
      const payload: MatchCommand = {
        ...command,
        expected_sequence: snapshot.sequence,
        connection_epoch: connectionEpoch,
      };
      return new Promise<boolean>((resolve) => {
        const timeout = setTimeout(() => {
          pendingRef.current.delete(payload.message_id);
          resolve(false);
        }, 10_000);
        pendingRef.current.set(payload.message_id, { resolve, timeout });
        try {
          socket.send(JSON.stringify(payload));
        } catch {
          clearTimeout(timeout);
          pendingRef.current.delete(payload.message_id);
          resolve(false);
        }
      });
    },
    [matchId, queryClient, readSnapshot],
  );

  const isCurrentConnection = useCallback(
    (epoch: number | null) =>
      epoch !== null &&
      connectionEpochRef.current === epoch &&
      socketRef.current?.readyState === WebSocket.OPEN,
    [],
  );

  return useMemo(
    () => ({
      snapshot: query.data ?? null,
      isLoading: query.isPending,
      error: query.error,
      socketStatus,
      connectionEpoch,
      socketError,
      commandErrorCode,
      resumeReasons,
      interimText,
      commandReady:
        socketStatus === 'open' &&
        connectionEpoch !== null &&
        Boolean(query.data && query.data.sequence >= latestSequence),
      sendCommand,
      isCurrentConnection,
      commandFailureMessage: () => commandErrorRef.current,
    }),
    [
      connectionEpoch,
      query.data,
      query.error,
      query.isPending,
      sendCommand,
      isCurrentConnection,
      resumeReasons,
      socketError,
      commandErrorCode,
      interimText,
      latestSequence,
      socketStatus,
    ],
  );
}
