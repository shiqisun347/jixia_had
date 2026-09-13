'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { MatchCommand, MatchSnapshot } from '@/lib/matches-api';
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
  type?: string;
  sequence?: number;
  connection_epoch?: number;
  message_id?: string;
  duplicate?: boolean;
  code?: string;
  message?: string;
  payload?: { text?: string; reasons?: string[] };
  snapshot?: MatchSnapshot;
};

export function matchSocketErrorText(
  event: MatchSocketEvent,
  t?: (key: string, values?: Record<string, string | number | Date>) => string,
): string | null | undefined {
  if (event.type === 'command.error') {
    return event.message ?? t?.('runtime.commandFailed') ?? '比赛指令未执行，请刷新后重试。';
  }
  if (event.type === 'match.resume_check_failed') {
    const reasons = event.payload?.reasons?.filter(Boolean) ?? [];
    return reasons.length > 0
      ? t?.('runtime.recoveryRequirements', { reasons: reasons.join('；') }) ?? `恢复条件未满足：${reasons.join('；')}`
      : t?.('runtime.recoveryFallback') ?? '恢复条件未满足，请检查辩手在线状态与设备。';
  }
  if (event.type === 'match.resume_countdown') return null;
  return undefined;
}

export function newestMatchSnapshot(
  current: MatchSnapshot | undefined,
  incoming: MatchSnapshot,
): MatchSnapshot {
  return !current || incoming.sequence >= current.sequence ? incoming : current;
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
  const query = useQuery({
    queryKey: ['matches', matchId, 'snapshot'],
    queryFn: () => matchesApi.snapshot(matchId),
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
  const [resumeReasons, setResumeReasons] = useState<string[]>(query.data?.resume_reasons ?? []);
  const [interimText, setInterimText] = useState(() => query.data?.interim_text ?? '');
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
    if (!hasInitialSnapshot) return;
    let disposed = false;
    let reconnectAttempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    const pending = pendingRef.current;
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
        const observeSequence = (sequence: number | undefined) => {
          if (sequence === undefined || sequence <= latestSequenceRef.current) return;
          latestSequenceRef.current = sequence;
          setLatestSequence(sequence);
        };
        observeSequence(event.sequence);
        if (event.connection_epoch) {
          connectionEpochRef.current = event.connection_epoch;
          setConnectionEpoch(event.connection_epoch);
        }
        if (event.type === 'match.snapshot') {
          const snapshot = (event as unknown as { payload: MatchSnapshot }).payload;
          observeSequence(snapshot.sequence);
          // Older Core/client snapshots may omit the transient field. Do not
          // erase a live subtitle merely because a compatibility snapshot lacks it.
          if (Object.prototype.hasOwnProperty.call(snapshot, 'interim_text')) {
            setInterimText(snapshot.interim_text ?? '');
          }
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
          void queryClient.invalidateQueries({
            queryKey: ['rooms', snapshot.room_id, 'snapshot'],
          });
        } else if (event.type === 'command.ack' && event.message_id) {
          if (event.snapshot) {
            const acknowledged = event.snapshot;
            observeSequence(acknowledged.sequence);
            queryClient.setQueryData<MatchSnapshot>(['matches', matchId, 'snapshot'], (current) =>
              newestMatchSnapshot(current, acknowledged),
            );
            setResumeReasons([]);
            setSocketError(null);
          }
          const pending = pendingRef.current.get(event.message_id);
          if (pending) {
            clearTimeout(pending.timeout);
            pending.resolve(true);
            pendingRef.current.delete(event.message_id);
          }
        } else if (event.type === 'command.error' && event.message_id) {
          const translate = tRef.current;
          setSocketError(matchSocketErrorText(event, translate) ?? translate('runtime.commandFailed'));
          const pending = pendingRef.current.get(event.message_id);
          if (pending) {
            clearTimeout(pending.timeout);
            pending.resolve(false);
            pendingRef.current.delete(event.message_id);
          }
        } else if (event.type === 'match.resume_check_failed') {
          setResumeReasons(event.payload?.reasons?.filter(Boolean) ?? []);
          const translate = tRef.current;
          setSocketError(matchSocketErrorText(event, translate) ?? translate('runtime.recoveryFallback'));
        } else if (event.type === 'match.resume_countdown') {
          setResumeReasons([]);
          setSocketError(null);
        } else if (event.type === 'asr.interim' || event.type === 'agent.subtitle') {
          setInterimText(event.payload?.text ?? '');
        } else if (
          event.type === 'transcript.updated' ||
          event.type === 'speech.finished' ||
          event.type === 'agent.finalized'
        ) {
          setInterimText('');
          void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'transcript'] });
          void queryClient.invalidateQueries({ queryKey: ['matches', matchId, 'snapshot'] });
        } else if (
          event.type === 'asr.segment_final' ||
          event.type === 'asr.retry_required' ||
          event.type === 'agent.retrying'
        ) {
          if (event.type !== 'asr.segment_final') setInterimText('');
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
      socket.addEventListener('message', onMessage);
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
    async (command: Omit<MatchCommand, 'expected_sequence' | 'connection_epoch'>) => {
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
            queryFn: () => matchesApi.snapshot(matchId),
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
    [matchId, queryClient],
  );

  return useMemo(
    () => ({
      snapshot: query.data ?? null,
      isLoading: query.isPending,
      error: query.error,
      socketStatus,
      connectionEpoch,
      socketError,
      resumeReasons,
      interimText,
      commandReady:
        socketStatus === 'open' &&
        connectionEpoch !== null &&
        Boolean(query.data && query.data.sequence >= latestSequence),
      sendCommand,
    }),
    [
      connectionEpoch,
      query.data,
      query.error,
      query.isPending,
      sendCommand,
      resumeReasons,
      socketError,
      interimText,
      latestSequence,
      socketStatus,
    ],
  );
}
