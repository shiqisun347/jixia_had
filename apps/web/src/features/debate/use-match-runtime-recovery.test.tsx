import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { matchesApi, type MatchSnapshot } from '@/lib/matches-api';
import { useMatchRuntime } from '@/features/debate/use-match-runtime';

// Synthetic HTTP and socket boundaries exercise the real hook and QueryClient.
class OwnedSocket {
  readyState = WebSocket.CONNECTING as number;
  send = vi.fn<(raw: string) => void>();
  listeners = new Map<string, Array<(event: Event) => void>>();
  addEventListener(type: string, cb: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), cb]);
  }
  emit(type: string, event: Event) {
    this.listeners.get(type)?.forEach((cb) => cb(event));
  }
  open() {
    this.readyState = WebSocket.OPEN;
    this.emit('open', new Event('open'));
  }
  close() {
    this.readyState = WebSocket.CLOSED;
    this.emit('close', new CloseEvent('close'));
  }
  message(data: unknown) {
    this.emit('message', new MessageEvent('message', { data: JSON.stringify(data) }));
  }
}
const key = ['matches', 'synthetic-spec307', 'transcript'];
const snap = (sequence: number, extra: object = {}) =>
  ({
    match_id: 'synthetic-spec307',
    room_id: 'synthetic-room',
    sequence,
    status: 'RUNNING',
    action_state: 'HUMAN_SPEAKING',
    current_speech_id: 'speech-a',
    current_speaker_side: 'AFFIRMATIVE',
    current_speaker_seat_no: 1,
    ...extra,
  }) as unknown as MatchSnapshot;

async function prepare() {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
    },
  });
  const sockets: OwnedSocket[] = [];
  window.__JX_MATCH_SOCKET_FACTORY__ = () => {
    const socket = new OwnedSocket();
    sockets.push(socket);
    return socket as unknown as WebSocket;
  };
  let serverTranscript = {
    match_id: 'synthetic-spec307',
    context_version: 1,
    speeches: [] as object[],
  };
  const request = vi.fn(async () => serverTranscript);
  vi.spyOn(matchesApi, 'snapshot').mockResolvedValue(snap(1));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(
    () => {
      const runtime = useMatchRuntime('synthetic-spec307');
      const transcript = useQuery({
        queryKey: key,
        queryFn: request,
        enabled: Boolean(runtime.snapshot),
        staleTime: Infinity,
      });
      return { runtime, transcript, transcriptData: transcript.data };
    },
    { wrapper },
  );
  await waitFor(() => expect(hook.result.current.transcript.isSuccess).toBe(true));
  act(() => sockets[0]!.open());
  act(() => sockets[0]!.message({ type: 'match.snapshot', payload: snap(1), connection_epoch: 1 }));
  await waitFor(() =>
    expect(client.getQueryState(['matches', 'synthetic-spec307', 'snapshot'])?.fetchStatus).toBe(
      'idle',
    ),
  );
  await waitFor(() => expect(hook.result.current.runtime.snapshot?.sequence).toBe(1));
  return {
    ...hook,
    client,
    sockets,
    request,
    setServerTranscript(value: typeof serverTranscript) {
      serverTranscript = value;
    },
  };
}

describe('match transport and transcript recovery', () => {
  afterEach(() => {
    cleanup();
    delete window.__JX_MATCH_SOCKET_FACTORY__;
    vi.restoreAllMocks();
  });

  it('rechecks a start owner after a held REST refresh before sending to a reset action', async () => {
    const p = await prepare();
    let resolveSnapshot!: (snapshot: MatchSnapshot) => void;
    let markRefreshing!: () => void;
    const refreshing = new Promise<void>((resolve) => {
      markRefreshing = resolve;
    });
    vi.mocked(matchesApi.snapshot).mockImplementationOnce(() => {
      markRefreshing();
      return new Promise((resolve) => {
        resolveSnapshot = resolve;
      });
    });
    p.sockets[0]!.send.mockImplementation((raw) => {
      const sent = JSON.parse(raw) as { message_id: string };
      p.sockets[0]!.message({
        type: 'command.ack',
        message_id: sent.message_id,
        snapshot: snap(2),
      });
    });
    act(() => p.sockets[0]!.message({ type: 'match.online', sequence: 2 }));
    await refreshing;
    let ownerCurrent = true;
    let command!: Promise<boolean>;
    act(() => {
      command = p.result.current.runtime.sendCommand(
        { type: 'speech.start', message_id: 'owned-start-before-reset' },
        () => ownerCurrent,
      );
    });
    ownerCurrent = false;
    await act(async () => {
      resolveSnapshot(snap(2, { context_version: 6, current_action_index: 4 }));
      await expect(command).resolves.toBe(false);
    });
    expect(p.sockets[0]!.send).not.toHaveBeenCalled();
  });

  it('keeps ordinary command behavior when no preparation guard is supplied', async () => {
    const p = await prepare();
    p.sockets[0]!.send.mockImplementation((raw) => {
      const sent = JSON.parse(raw) as { message_id: string };
      p.sockets[0]!.message({
        type: 'command.ack',
        message_id: sent.message_id,
        snapshot: snap(2),
      });
    });
    await act(async () => {
      await expect(
        p.result.current.runtime.sendCommand({ type: 'match.pause', message_id: 'pause' }),
      ).resolves.toBe(true);
    });
    expect(p.sockets[0]!.send).toHaveBeenCalledOnce();
  });

  it('retires connection ownership synchronously before a failed command can reopen a microphone', async () => {
    const p = await prepare();
    const ownsEpoch = p.result.current.runtime.isCurrentConnection;
    expect(ownsEpoch(1)).toBe(true);
    act(() => p.sockets[0]!.close());
    expect(ownsEpoch(1)).toBe(false);
    await waitFor(() => expect(p.sockets).toHaveLength(2), { timeout: 2000 });
    act(() => p.sockets[1]!.open());
    act(() =>
      p.sockets[1]!.message({ type: 'match.snapshot', payload: snap(2), connection_epoch: 2 }),
    );
    expect(ownsEpoch(1)).toBe(false);
    expect(ownsEpoch(2)).toBe(true);
  });

  it('recovers committed text after a completion event was missed during disconnect', async () => {
    const p = await prepare();
    p.setServerTranscript({
      match_id: 'synthetic-spec307',
      context_version: 2,
      speeches: [{ id: 'committed-a', display_text: '数据库已有最终文字' }],
    });
    act(() => p.sockets[0]!.close());
    await waitFor(() => expect(p.sockets).toHaveLength(2), { timeout: 2000 });
    act(() => p.sockets[1]!.open());
    act(() =>
      p.sockets[1]!.message({ type: 'match.snapshot', payload: snap(2), connection_epoch: 2 }),
    );
    await waitFor(() => expect(p.result.current.runtime.snapshot?.sequence).toBe(2));
    await waitFor(() => expect(p.result.current.transcript.data?.speeches).toHaveLength(1));
  });

  it('rejects an old snapshot together with its transient text', async () => {
    const p = await prepare();
    act(() =>
      p.sockets[0]!.message({
        type: 'match.snapshot',
        payload: snap(10, { current_speech_id: 'speech-b', interim_text: '新辩手正确字幕' }),
      }),
    );
    await waitFor(() => expect(p.result.current.runtime.interimText).toBe('新辩手正确字幕'));
    act(() =>
      p.sockets[0]!.message({
        type: 'match.snapshot',
        payload: snap(9, { current_speech_id: 'speech-a', interim_text: '旧辩手已过期字幕' }),
      }),
    );
    await waitFor(() => expect(p.result.current.runtime.interimText).toBe('新辩手正确字幕'));
    expect(p.result.current.runtime.snapshot?.current_speech_id).toBe('speech-b');
    expect(p.result.current.runtime.snapshot?.sequence).toBe(10);
  });

  it('rejects live text belonging to an older speech and sequence', async () => {
    const p = await prepare();
    act(() =>
      p.sockets[0]!.message({
        type: 'match.snapshot',
        payload: snap(11, {
          current_speech_id: 'speech-b',
          current_speaker_side: 'NEGATIVE',
          current_speaker_seat_no: 3,
          interim_text: '',
        }),
      }),
    );
    act(() =>
      p.sockets[0]!.message({
        type: 'asr.interim',
        sequence: 1,
        payload: { speech_id: 'speech-a', text: '应被丢弃的旧发言' },
      }),
    );
    await waitFor(() => expect(p.result.current.runtime.interimText).toBe(''));
    expect(p.result.current.runtime.snapshot?.current_speech_id).toBe('speech-b');
    expect(p.result.current.runtime.snapshot?.current_speaker_side).toBe('NEGATIVE');
  });

  it('positive control: actual Core cumulative interims retain the previous completed segment', async () => {
    const p = await prepare();
    act(() =>
      p.sockets[0]!.message({
        type: 'asr.interim',
        payload: { speech_id: 'speech-a', segment_no: 1, text: '第一段完整内容' },
      }),
    );
    act(() =>
      p.sockets[0]!.message({
        type: 'asr.segment_final',
        payload: { speech_id: 'speech-a', segment_no: 1, text: '第一段最终内容' },
      }),
    );
    act(() =>
      p.sockets[0]!.message({
        type: 'asr.interim',
        payload: { speech_id: 'speech-a', segment_no: 2, text: '第一段最终内容第二段当前内容' },
      }),
    );
    await waitFor(() =>
      expect(p.result.current.runtime.interimText).toBe('第一段最终内容第二段当前内容'),
    );
    expect(p.result.current.runtime.interimText).toContain('第一段');
    expect(p.result.current.transcript.data?.speeches).toEqual([]);
  });

  it('refreshes withdrawn stage records for watching clients after stage reset', async () => {
    const p = await prepare();
    p.client.setQueryData(key, {
      match_id: 'synthetic-spec307',
      context_version: 2,
      speeches: [{ id: 'reset-stage-row', display_text: '该阶段旧正式记录' }],
    });
    const invalidations = vi.spyOn(p.client, 'invalidateQueries');
    act(() =>
      p.sockets[0]!.message({
        type: 'stage.reset',
        sequence: 3,
        payload: { action_keys: ['1:1'] },
      }),
    );
    await waitFor(() =>
      expect(invalidations).toHaveBeenCalledWith({
        queryKey: ['matches', 'synthetic-spec307', 'snapshot'],
      }),
    );
    await waitFor(() =>
      expect((p.client.getQueryData(key) as { speeches: object[] }).speeches).toHaveLength(0),
    );
  });

  it('positive control: an actual completion event refreshes final transcript', async () => {
    const p = await prepare();
    p.setServerTranscript({
      match_id: 'synthetic-spec307',
      context_version: 2,
      speeches: [{ id: 'committed-a', display_text: '正式文字' }],
    });
    act(() =>
      p.sockets[0]!.message({
        type: 'speech.finished',
        sequence: 2,
        payload: { speech_id: 'speech-a' },
      }),
    );
    await waitFor(() => expect(p.result.current.transcript.data?.speeches).toHaveLength(1));
    expect(p.request.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('shows committed administrator placeholders after speech.skipped', async () => {
    const p = await prepare();
    p.setServerTranscript({
      match_id: 'synthetic-spec307',
      context_version: 2,
      speeches: [
        { id: 'placeholder-a', origin: 'ADMIN_PLACEHOLDER', display_text: 'AI占位未播报' },
      ],
    });
    const invalidations = vi.spyOn(p.client, 'invalidateQueries');
    act(() =>
      p.sockets[0]!.message({
        type: 'speech.skipped',
        sequence: 2,
        payload: { speech_id: 'placeholder-a', origin: 'ADMIN_PLACEHOLDER' },
      }),
    );
    await waitFor(() =>
      expect(invalidations).toHaveBeenCalledWith({
        queryKey: ['matches', 'synthetic-spec307', 'snapshot'],
      }),
    );
    await waitFor(() => expect(p.result.current.transcript.data?.speeches).toHaveLength(1));
  });

  it('keeps a newer WebSocket snapshot when an older in-flight REST response arrives', async () => {
    const p = await prepare();
    let resolve!: (s: MatchSnapshot) => void;
    vi.mocked(matchesApi.snapshot).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    let fetching!: Promise<void>;
    act(() => {
      fetching = p.client.invalidateQueries({
        queryKey: ['matches', 'synthetic-spec307', 'snapshot'],
      });
    });
    act(() =>
      p.sockets[0]!.message({
        type: 'match.snapshot',
        payload: snap(12, { current_speech_id: 'new-speech' }),
      }),
    );
    await waitFor(() => expect(p.result.current.runtime.snapshot?.sequence).toBe(12));
    await act(async () => {
      resolve(snap(2, { current_speech_id: 'older-speech' }));
      await fetching;
    });
    await waitFor(() => expect(p.result.current.runtime.snapshot?.sequence).toBe(12));
    expect(p.result.current.runtime.commandReady).toBe(true);
  });

  it('ignores messages from a retired socket after a new connection is established', async () => {
    const p = await prepare();
    const old = p.sockets[0]!;
    act(() => old.close());
    await waitFor(() => expect(p.sockets).toHaveLength(2), { timeout: 2000 });
    act(() => {
      p.sockets[1]!.open();
      p.sockets[1]!.message({
        type: 'match.snapshot',
        payload: snap(5, { interim_text: '当前连接' }),
        connection_epoch: 2,
      });
    });
    await waitFor(() => expect(p.result.current.runtime.snapshot?.sequence).toBe(5));
    act(() =>
      old.message({
        type: 'match.snapshot',
        payload: snap(100, { interim_text: '旧连接' }),
        connection_epoch: 9,
      }),
    );
    expect(p.result.current.runtime.snapshot?.sequence).toBe(5);
    expect(p.result.current.runtime.interimText).toBe('当前连接');
    expect(p.result.current.runtime.connectionEpoch).toBe(2);
  });

  it('accepts multiple current-speech subtitle revisions at the same sequence without HTTP token polling', async () => {
    const p = await prepare();
    const requests = vi.mocked(matchesApi.snapshot).mock.calls.length;
    act(() => {
      p.sockets[0]!.message({
        type: 'asr.interim',
        sequence: 1,
        payload: { speech_id: 'speech-a', text: '你好' },
      });
      p.sockets[0]!.message({
        type: 'asr.interim',
        sequence: 1,
        payload: { speech_id: 'speech-a', text: '你好世界' },
      });
      for (let token = 0; token < 20; token += 1)
        p.sockets[0]!.message({
          type: 'agent.text_delta',
          sequence: 1,
          payload: { text: '未播放的模型草稿' },
        });
    });
    await waitFor(() => expect(p.result.current.runtime.interimText).toBe('你好世界'));
    expect(vi.mocked(matchesApi.snapshot).mock.calls.length).toBe(requests);
  });

  it('keeps a subtitle revision received while an equal-sequence REST snapshot is in flight', async () => {
    const p = await prepare();
    let resolve!: (s: MatchSnapshot) => void;
    vi.mocked(matchesApi.snapshot).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    let fetching!: Promise<void>;
    act(() => {
      fetching = p.client.invalidateQueries({
        queryKey: ['matches', 'synthetic-spec307', 'snapshot'],
      });
    });
    act(() =>
      p.sockets[0]!.message({
        type: 'asr.interim',
        sequence: 1,
        payload: { speech_id: 'speech-a', text: '最新累计字幕' },
      }),
    );
    await waitFor(() => expect(p.result.current.runtime.interimText).toBe('最新累计字幕'));
    await act(async () => {
      resolve(snap(1, { interim_text: 'HTTP开始前的字幕' }));
      await fetching;
    });
    expect(p.result.current.runtime.interimText).toBe('最新累计字幕');
  });

  it('does not clear the next speaker subtitle when a preceding source finishes late', async () => {
    const p = await prepare();
    act(() =>
      p.sockets[0]!.message({
        type: 'match.snapshot',
        payload: snap(10, { current_speech_id: 'speech-b', interim_text: '新发言正在进行' }),
      }),
    );
    await waitFor(() =>
      expect(p.result.current.runtime.snapshot?.current_speech_id).toBe('speech-b'),
    );
    act(() =>
      p.sockets[0]!.message({
        type: 'speech.finished',
        sequence: 10,
        payload: { speech_id: 'speech-a' },
      }),
    );
    await waitFor(() => expect(p.result.current.runtime.interimText).toBe('新发言正在进行'));
    expect(p.result.current.runtime.snapshot?.current_speech_id).toBe('speech-b');
  });
});
