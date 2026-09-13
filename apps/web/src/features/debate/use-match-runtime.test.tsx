import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MatchSnapshot } from '@/lib/matches-api';
import { matchesApi } from '@/lib/matches-api';

import { useMatchRuntime } from './use-match-runtime';

class FakeMatchSocket {
  readyState: number = WebSocket.CONNECTING;
  readonly sent: string[] = [];
  throwOnSend = false;
  private readonly listeners = new Map<string, Set<(event: Event) => void>>();

  addEventListener(type: string, listener: (event: Event) => void) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  close() {
    this.readyState = WebSocket.CLOSED;
    this.emit('close', new CloseEvent('close'));
  }

  send(payload: string) {
    if (this.throwOnSend) throw new Error('socket send failed');
    this.sent.push(payload);
  }

  open() {
    this.readyState = WebSocket.OPEN;
    this.emit('open', new Event('open'));
  }

  message(payload: object) {
    this.emit('message', new MessageEvent('message', { data: JSON.stringify(payload) }));
  }

  private emit(type: string, event: Event) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function snapshot(sequence: number): MatchSnapshot {
  return {
    id: 'match-1',
    room_id: 'room-1',
    sequence,
    status: 'RUNNING',
    action_state: 'HUMAN_READY_TO_START',
  } as unknown as MatchSnapshot;
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe('useMatchRuntime presence refresh', () => {
  afterEach(() => {
    cleanup();
    delete window.__JX_MATCH_SOCKET_FACTORY__;
    vi.restoreAllMocks();
  });

  it.each(['match.online', 'match.offline'])(
    'refreshes the authoritative match sequence after %s before sending a command',
    async (eventType) => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const socket = new FakeMatchSocket();
      const snapshotRequest = vi
        .spyOn(matchesApi, 'snapshot')
        .mockResolvedValueOnce(snapshot(7))
        .mockResolvedValueOnce(snapshot(7))
        .mockResolvedValueOnce(snapshot(9));
      window.__JX_MATCH_SOCKET_FACTORY__ = () => socket as unknown as WebSocket;

      const { result } = renderHook(() => useMatchRuntime('match-1'), {
        wrapper: wrapper(queryClient),
      });

      await waitFor(() => expect(result.current.snapshot?.sequence).toBe(7));
      act(() => socket.open());
      await waitFor(() => expect(snapshotRequest).toHaveBeenCalledTimes(2));
      act(() => {
        socket.message({
          type: 'match.snapshot',
          connection_epoch: 4,
          payload: snapshot(7),
        });
      });
      await waitFor(() => expect(result.current.connectionEpoch).toBe(4));

      const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
      act(() => socket.message({ type: eventType, sequence: 9 }));

      await waitFor(() => expect(snapshotRequest).toHaveBeenCalledTimes(3));
      await waitFor(() =>
        expect(
          queryClient.getQueryData<MatchSnapshot>(['matches', 'match-1', 'snapshot'])?.sequence,
        ).toBe(9),
      );
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ['matches', 'match-1', 'snapshot'],
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ['rooms', 'room-1', 'snapshot'],
      });

      act(() => {
        void result.current.sendCommand({
          type: 'speech.start',
          message_id: 'message-1',
        } as Parameters<(typeof result.current)['sendCommand']>[0]);
      });
      expect(JSON.parse(socket.sent.at(-1) ?? '{}')).toMatchObject({
        expected_sequence: 9,
        connection_epoch: 4,
      });
    },
  );

  it('waits for a lagging snapshot before sending with the latest sequence and epoch', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const socket = new FakeMatchSocket();
    const refreshedSnapshot = deferred<MatchSnapshot>();
    const snapshotRequest = vi
      .spyOn(matchesApi, 'snapshot')
      .mockResolvedValueOnce(snapshot(7))
      .mockResolvedValueOnce(snapshot(7))
      .mockImplementationOnce(() => refreshedSnapshot.promise);
    window.__JX_MATCH_SOCKET_FACTORY__ = () => socket as unknown as WebSocket;

    const { result } = renderHook(() => useMatchRuntime('match-1'), {
      wrapper: wrapper(queryClient),
    });

    await waitFor(() => expect(result.current.snapshot?.sequence).toBe(7));
    act(() => socket.open());
    await waitFor(() => expect(snapshotRequest).toHaveBeenCalledTimes(2));
    act(() => {
      socket.message({
        type: 'match.snapshot',
        connection_epoch: 4,
        payload: snapshot(7),
      });
    });
    await waitFor(() => expect(result.current.commandReady).toBe(true));

    act(() => socket.message({ type: 'match.online', sequence: 9 }));
    await waitFor(() => expect(result.current.commandReady).toBe(false));
    await waitFor(() => expect(snapshotRequest).toHaveBeenCalledTimes(3));

    const commandResult = result.current.sendCommand({
      type: 'speech.start',
      message_id: 'message-lagging',
    } as Parameters<(typeof result.current)['sendCommand']>[0]);
    expect(socket.sent).toEqual([]);

    await act(async () => {
      refreshedSnapshot.resolve(snapshot(9));
      await refreshedSnapshot.promise;
    });
    await waitFor(() => expect(socket.sent).toHaveLength(1));
    expect(JSON.parse(socket.sent[0] ?? '{}')).toMatchObject({
      message_id: 'message-lagging',
      expected_sequence: 9,
      connection_epoch: 4,
    });

    act(() => {
      socket.message({
        type: 'command.ack',
        message_id: 'message-lagging',
        snapshot: snapshot(10),
      });
    });
    await expect(commandResult).resolves.toBe(true);
  });

  it('settles a synchronous send failure immediately and permits a later command', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const socket = new FakeMatchSocket();
    vi.spyOn(matchesApi, 'snapshot').mockResolvedValue(snapshot(7));
    window.__JX_MATCH_SOCKET_FACTORY__ = () => socket as unknown as WebSocket;

    const { result } = renderHook(() => useMatchRuntime('match-1'), {
      wrapper: wrapper(queryClient),
    });

    await waitFor(() => expect(result.current.snapshot?.sequence).toBe(7));
    act(() => socket.open());
    act(() => {
      socket.message({
        type: 'match.snapshot',
        connection_epoch: 4,
        payload: snapshot(7),
      });
    });
    await waitFor(() => expect(result.current.commandReady).toBe(true));

    socket.throwOnSend = true;
    await expect(
      result.current.sendCommand({
        type: 'speech.start',
        message_id: 'message-failed',
      } as Parameters<(typeof result.current)['sendCommand']>[0]),
    ).resolves.toBe(false);

    socket.throwOnSend = false;
    const retryResult = result.current.sendCommand({
      type: 'speech.start',
      message_id: 'message-retry',
    } as Parameters<(typeof result.current)['sendCommand']>[0]);
    await waitFor(() => expect(socket.sent).toHaveLength(1));
    act(() => {
      socket.message({
        type: 'command.ack',
        message_id: 'message-retry',
        snapshot: snapshot(8),
      });
    });
    await expect(retryResult).resolves.toBe(true);
  });
});
