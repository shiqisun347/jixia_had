import { describe, expect, it, vi } from 'vitest';

import type { MatchSnapshot } from '@/lib/matches-api';

import { HumanMicrophoneStart, type HumanMicrophoneContext } from './human-microphone-start';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fixture() {
  const states: boolean[] = [];
  let audible = false;
  const pending = vi.fn();
  const session = {
    prepareMicrophone: vi.fn(async () => {
      audible = false;
    }),
    setMicrophoneEnabled: vi.fn(async (enabled: boolean) => {
      audible = enabled;
      states.push(enabled);
    }),
  };
  let current: HumanMicrophoneContext = {
    snapshot: {
      match_id: 'match-1',
      status: 'RUNNING',
      action_state: 'HUMAN_READY_TO_START',
      current_speaker_user_id: 'human-1',
      current_speech_id: null,
      context_version: 5,
      current_action_index: 2,
      current_action: { stage_position: 1, action_position: 3 },
    } as MatchSnapshot,
    session,
    epoch: 4,
    userId: 'human-1',
    connectionCurrent: true,
  };
  const controller = new HumanMicrophoneStart(pending);
  const read = () => current;
  const update = (patch: Partial<NonNullable<HumanMicrophoneContext['snapshot']>>) => {
    current = { ...current, snapshot: { ...current.snapshot, ...patch } as MatchSnapshot };
  };
  const acknowledge = vi.fn(async () => {
    update({ action_state: 'HUMAN_SPEAKING', current_speech_id: 'speech-1' });
    return true;
  });
  return {
    controller,
    read,
    update,
    states,
    isAudible: () => audible,
    session,
    pending,
    acknowledge,
    replace: (patch: Partial<HumanMicrophoneContext>) => {
      current = { ...current, ...patch };
    },
  };
}

describe('human microphone startup ownership', () => {
  it('a disconnected old-session cleanup rejection cannot report a microphone error for its successor', async () => {
    const f = fixture(),
      oldMute = deferred<void>();
    f.session.setMicrophoneEnabled
      .mockImplementationOnce(() => oldMute.promise)
      .mockRejectedValueOnce(new Error('old disconnected cleanup'));
    const oldStart = f.controller.start(f.read, f.acknowledge, vi.fn());
    const replacement = {
      prepareMicrophone: vi.fn().mockResolvedValue(undefined),
      setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
    };
    f.replace({ session: replacement, epoch: 5 });
    await f.controller.sync(f.read(), null);
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).resolves.toBe(true);
    oldMute.reject(new Error('old disconnected mute'));
    await expect(oldStart).resolves.toBe(false);
    expect(f.session.setMicrophoneEnabled).toHaveBeenCalledTimes(2);
    expect(replacement.setMicrophoneEnabled.mock.calls).toEqual([[false], [true]]);
  });

  it('retains a visible cleanup error for the still-current same session', async () => {
    const f = fixture();
    f.session.setMicrophoneEnabled
      .mockRejectedValueOnce(new Error('current mute failed'))
      .mockRejectedValueOnce(new Error('current cleanup failed'));
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).rejects.toThrow(
      'current cleanup failed',
    );
    expect(f.acknowledge).not.toHaveBeenCalled();
  });
  it('waits for every old native unmute to converge muted before preparing a successor', async () => {
    const f = fixture(),
      release = deferred<void>();
    f.session.setMicrophoneEnabled.mockImplementationOnce(async (enabled) => {
      await release.promise;
      f.states.push(enabled);
    });
    const oldUnmute = f.controller.setEnabled(f.session, true);
    await f.controller.stop(f.session);
    const nextStart = f.controller.start(f.read, f.acknowledge, vi.fn());
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(f.session.prepareMicrophone).not.toHaveBeenCalled();
    expect(f.acknowledge).not.toHaveBeenCalled();
    release.resolve();
    await oldUnmute;
    await expect(nextStart).resolves.toBe(true);
    expect(f.states).toEqual([false, true, false, true]);
    expect(f.session.prepareMicrophone).toHaveBeenCalledOnce();
  });
  it('keeps the real muted publication silent while ACK is held, even after a speaking snapshot', async () => {
    const f = fixture();
    const ack = deferred<boolean>();
    const command = vi.fn(() => ack.promise);
    const started = f.controller.start(f.read, command, vi.fn());
    await vi.waitFor(() => expect(command).toHaveBeenCalledOnce(), { interval: 1 });
    try {
      expect(f.isAudible()).toBe(false);
      expect(f.session.prepareMicrophone).toHaveBeenCalledOnce();
      f.update({ action_state: 'HUMAN_SPEAKING', current_speech_id: 'speech-1' });
      await f.controller.sync(f.read(), null);
      expect(f.isAudible()).toBe(false);
    } finally {
      f.update({ action_state: 'HUMAN_SPEAKING', current_speech_id: 'speech-1' });
      ack.resolve(true);
      await started;
    }
    expect(f.states).toEqual([false, true]);
  });

  it('unmutes after a new ACK even if an earlier speech cached enabled microphone intent', async () => {
    const f = fixture();
    await f.controller.setEnabled(f.session, true);
    await f.controller.start(f.read, f.acknowledge, vi.fn());
    expect(f.session.prepareMicrophone).toHaveBeenCalledOnce();
    expect(f.states).toEqual([true, false, true]);
  });

  it('establishes the first reader before requesting Core commit, with no prepublished track', async () => {
    const f = fixture();
    const reader = deferred<void>();
    f.session.prepareMicrophone.mockImplementation(async () => {
      reader.resolve();
    });
    const committed = vi.fn();
    await expect(
      f.controller.start(
        f.read,
        async () => {
          await reader.promise;
          expect(f.states).toEqual([false]);
          return f.acknowledge();
        },
        committed,
      ),
    ).resolves.toBe(true);
    expect(committed).toHaveBeenCalledOnce();
    expect(f.pending.mock.calls).toEqual([[true], [false]]);
    expect(f.states.at(-1)).toBe(true);
  });

  it('preserves the pending microphone when a ready-state rerender runs its media effect', async () => {
    const f = fixture();
    const microphone = deferred<void>();
    f.session.prepareMicrophone.mockImplementation(() => microphone.promise);
    const started = f.controller.start(f.read, f.acknowledge, vi.fn());
    const renderEffect = f.controller.sync(f.read(), null);
    expect(f.states).toEqual([false]);
    expect(f.acknowledge).not.toHaveBeenCalled();
    microphone.resolve();
    await Promise.all([started, renderEffect]);
    expect(f.isAudible()).toBe(true);
    expect(f.session.prepareMicrophone).toHaveBeenCalledOnce();
  });

  it('does not submit a command if permission or publication fails', async () => {
    const f = fixture();
    f.session.prepareMicrophone.mockRejectedValueOnce(new Error('permission denied'));
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).rejects.toThrow(
      'permission denied',
    );
    expect(f.acknowledge).not.toHaveBeenCalled();
    expect(f.states).not.toContain(true);
    expect(f.pending.mock.calls).toEqual([[true], [false]]);
  });

  it.each([
    { status: 'PAUSED' },
    { status: 'TERMINATED' },
    { context_version: 6 },
    { current_action_index: 3 },
    { current_speaker_user_id: 'human-2' },
    { current_speech_id: 'replacement-speech' },
  ])('rejects a retired preparation after microphone await: %j', async (patch) => {
    const f = fixture();
    const microphone = deferred<void>();
    f.session.prepareMicrophone.mockImplementationOnce(() => microphone.promise);
    const started = f.controller.start(f.read, f.acknowledge, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    f.update(patch);
    await f.controller.sync(f.read(), null);
    microphone.resolve();
    await expect(started).resolves.toBe(false);
    expect(f.acknowledge).not.toHaveBeenCalled();
    expect(f.isAudible()).toBe(false);
    expect(f.states).not.toContain(true);
  });

  it('rechecks the socket epoch before sending after a delayed microphone', async () => {
    const f = fixture();
    const microphone = deferred<void>();
    f.session.prepareMicrophone.mockImplementationOnce(() => microphone.promise);
    const started = f.controller.start(f.read, f.acknowledge, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    f.replace({ connectionCurrent: false });
    microphone.resolve();
    await expect(started).resolves.toBe(false);
    expect(f.acknowledge).not.toHaveBeenCalled();
    expect(f.states).not.toContain(true);
  });

  it('closes a rejected start and coalesces repeated clicks during preparation', async () => {
    const f = fixture();
    const ack = deferred<boolean>();
    const command = vi.fn(() => ack.promise);
    const started = f.controller.start(f.read, command, vi.fn());
    await expect(f.controller.start(f.read, command, vi.fn())).resolves.toBe(false);
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    expect(command).toHaveBeenCalledOnce();
    ack.resolve(false);
    await expect(started).resolves.toBe(false);
    expect(f.states).not.toContain(true);
  });

  it('rejects a late ACK after pause and never calls the committed handler', async () => {
    const f = fixture();
    const ack = deferred<boolean>();
    const committed = vi.fn();
    const started = f.controller.start(f.read, () => ack.promise, committed);
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    f.update({ status: 'PAUSED' });
    await f.controller.sync(f.read(), null);
    ack.resolve(true);
    await expect(started).resolves.toBe(false);
    expect(committed).not.toHaveBeenCalled();
    expect(f.states).not.toContain(true);
  });

  it('an old failure cannot mute a new valid claim on the same session', async () => {
    const f = fixture();
    const oldAck = deferred<boolean>();
    const oldStart = f.controller.start(f.read, () => oldAck.promise, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    f.update({ status: 'PAUSED' });
    await f.controller.sync(f.read(), null);
    f.update({ status: 'RUNNING' });
    const nextAck = deferred<boolean>();
    const nextStart = f.controller.start(f.read, () => nextAck.promise, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    const beforeOldFailure = [...f.states];
    oldAck.resolve(false);
    await expect(oldStart).resolves.toBe(false);
    expect(f.states).toEqual(beforeOldFailure);
    f.update({ action_state: 'HUMAN_SPEAKING', current_speech_id: 'new-speech' });
    nextAck.resolve(true);
    await expect(nextStart).resolves.toBe(true);
    expect(f.states.at(-1)).toBe(true);
  });

  it('an old ACK cannot mute a successor that has already committed', async () => {
    const f = fixture();
    const oldAck = deferred<boolean>();
    const oldStart = f.controller.start(f.read, () => oldAck.promise, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    f.update({ status: 'PAUSED' });
    await f.controller.sync(f.read(), null);
    f.update({ status: 'RUNNING' });
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).resolves.toBe(true);
    const beforeOldAck = [...f.states];
    oldAck.resolve(true);
    await expect(oldStart).resolves.toBe(false);
    expect(f.states).toEqual(beforeOldAck);
    expect(f.states.at(-1)).toBe(true);
  });

  it('a captured send guard rejects an old claim even when a new claim has the same public context', async () => {
    const f = fixture();
    const oldAck = deferred<boolean>();
    const oldStart = f.controller.start(f.read, () => oldAck.promise, vi.fn());
    const oldGuard = f.controller.submissionGuard(f.read);
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    expect(oldGuard()).toBe(true);
    f.update({ status: 'PAUSED' });
    await f.controller.sync(f.read(), null);
    f.update({ status: 'RUNNING' });
    const nextAck = deferred<boolean>();
    const nextStart = f.controller.start(f.read, () => nextAck.promise, vi.fn());
    expect(oldGuard()).toBe(false);
    expect(f.controller.submissionGuard(f.read)()).toBe(true);
    oldAck.resolve(false);
    nextAck.resolve(false);
    await Promise.all([oldStart, nextStart]);
  });

  it('never opens a microphone or sends start for another speaker or a paused match', async () => {
    const f = fixture();
    f.update({ current_speaker_user_id: 'human-2' });
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).resolves.toBe(false);
    f.update({ current_speaker_user_id: 'human-1', status: 'PAUSED' });
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).resolves.toBe(false);
    expect(f.session.setMicrophoneEnabled).not.toHaveBeenCalled();
    expect(f.acknowledge).not.toHaveBeenCalled();
  });

  it('a late old publish reconciles to stop and does not resurrect the retired microphone', async () => {
    const f = fixture();
    const microphone = deferred<void>();
    f.session.prepareMicrophone.mockImplementationOnce(() => microphone.promise);
    const started = f.controller.start(f.read, f.acknowledge, vi.fn());
    await f.controller.stop(f.session);
    microphone.resolve();
    await started;
    expect(f.states).not.toContain(true);
    expect(f.acknowledge).not.toHaveBeenCalled();
  });

  it('old session cleanup never disables a replacement session', async () => {
    const f = fixture();
    const oldMic = deferred<void>();
    f.session.prepareMicrophone.mockImplementationOnce(() => oldMic.promise);
    const oldStart = f.controller.start(f.read, f.acknowledge, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalled(), { interval: 1 });
    const replacement = {
      prepareMicrophone: vi.fn().mockResolvedValue(undefined),
      setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
    };
    f.replace({ session: replacement, epoch: 5 });
    await f.controller.sync(f.read(), null);
    const nextStart = f.controller.start(f.read, f.acknowledge, vi.fn());
    oldMic.resolve();
    await Promise.all([oldStart, nextStart]);
    expect(replacement.setMicrophoneEnabled.mock.calls).toEqual([[false], [true]]);
    expect(f.acknowledge).toHaveBeenCalledOnce();
    expect(f.states).not.toContain(true);
  });

  it('a rejected old preparation cannot surface a new microphone failure after a successor commits', async () => {
    const f = fixture(),
      oldPreparation = deferred<void>();
    f.session.prepareMicrophone.mockImplementationOnce(() => oldPreparation.promise);
    const oldStart = f.controller.start(f.read, f.acknowledge, vi.fn());
    await vi.waitFor(() => expect(f.session.prepareMicrophone).toHaveBeenCalledOnce(), {
      interval: 1,
    });
    f.update({ status: 'PAUSED' });
    await f.controller.sync(f.read(), null);
    f.update({ status: 'RUNNING' });
    await expect(f.controller.start(f.read, f.acknowledge, vi.fn())).resolves.toBe(true);
    const beforeOldFailure = [...f.states];
    oldPreparation.reject(new Error('old permission failure'));
    await expect(oldStart).resolves.toBe(false);
    expect(f.states).toEqual(beforeOldFailure);
    expect(f.isAudible()).toBe(true);
  });

  it('a held post-ACK unmute cannot revive audio after pause', async () => {
    const f = fixture();
    const unmuting = deferred<void>();
    const release = deferred<void>();
    let held = false;
    f.session.setMicrophoneEnabled.mockImplementation(async (enabled) => {
      if (enabled && !held) {
        held = true;
        unmuting.resolve();
        await release.promise;
      }
      f.states.push(enabled);
    });
    const started = f.controller.start(f.read, f.acknowledge, vi.fn());
    await unmuting.promise;
    f.update({ status: 'PAUSED' });
    await f.controller.sync(f.read(), null);
    release.resolve();
    await expect(started).resolves.toBe(false);
    expect(f.states.at(-1)).toBe(false);
  });
});
