import { describe, expect, it, vi } from 'vitest';

import { mutedMicrophonePreparation } from './muted-microphone-preparation';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function fixture() {
  const order: string[] = [];
  let muted = false;
  const track = {
    mute: vi.fn(async () => {
      muted = true;
      order.push('mute');
    }),
    stop: vi.fn(),
  };
  const publisher = {
    currentTrack: vi.fn((): typeof track | undefined => undefined),
    createTracks: vi.fn(async () => {
      order.push('capture');
      return [track];
    }),
    publish: vi.fn(async () => {
      expect(muted).toBe(true);
      order.push('publish_muted');
    }),
    unpublish: vi.fn(async () => undefined),
  };
  return { track, publisher, order, preparation: mutedMicrophonePreparation(publisher) };
}

describe('real muted microphone publication', () => {
  it('mutes the actual captured track before its first publication, with no generated silence', async () => {
    const f = fixture();
    await f.preparation.prepare();
    expect(f.order).toEqual(['capture', 'mute', 'publish_muted']);
    expect(f.publisher.publish).toHaveBeenCalledWith(f.track);
  });

  it('reuses the existing microphone track while keeping it muted', async () => {
    const f = fixture();
    f.publisher.currentTrack.mockReturnValue(f.track);
    await f.preparation.prepare();
    expect(f.track.mute).toHaveBeenCalledOnce();
    expect(f.publisher.createTracks).not.toHaveBeenCalled();
    expect(f.publisher.publish).not.toHaveBeenCalled();
  });

  it('coalesces permission requests while one real capture is pending', async () => {
    const f = fixture(),
      capture = deferred<(typeof f.track)[]>();
    f.publisher.createTracks.mockReturnValue(capture.promise);
    const first = f.preparation.prepare(),
      second = f.preparation.prepare();
    expect(first).toBe(second);
    expect(f.publisher.createTracks).toHaveBeenCalledOnce();
    capture.resolve([f.track]);
    await Promise.all([first, second]);
    expect(f.publisher.publish).toHaveBeenCalledOnce();
  });

  it('stops a late permission result after the owning Room was closed without publishing', async () => {
    const f = fixture(),
      capture = deferred<(typeof f.track)[]>();
    f.publisher.createTracks.mockReturnValue(capture.promise);
    const pending = f.preparation.prepare();
    f.preparation.close();
    capture.resolve([f.track]);
    await expect(pending).rejects.toThrow('microphone_session_closed');
    expect(f.publisher.publish).not.toHaveBeenCalled();
    expect(f.track.stop).toHaveBeenCalledOnce();
  });

  it('unpublishes only the exact old track when publication finishes after Room cleanup', async () => {
    const f = fixture(),
      publish = deferred<void>(),
      entered = deferred<void>();
    f.publisher.publish.mockImplementation(async () => {
      entered.resolve();
      await publish.promise;
    });
    const pending = f.preparation.prepare();
    await entered.promise;
    f.preparation.close();
    const replacement = { mute: vi.fn(), stop: vi.fn() };
    publish.resolve();
    await expect(pending).rejects.toThrow('microphone_session_closed');
    expect(f.publisher.unpublish).toHaveBeenCalledWith(f.track);
    expect(f.track.stop).toHaveBeenCalledOnce();
    expect(replacement.stop).not.toHaveBeenCalled();
  });

  it('stops an unpublished track if muting or publication fails', async () => {
    for (const phase of ['mute', 'publish'] as const) {
      const f = fixture();
      if (phase === 'mute') f.track.mute.mockRejectedValueOnce(new Error('controlled failure'));
      else f.publisher.publish.mockRejectedValueOnce(new Error('controlled failure'));
      await expect(f.preparation.prepare()).rejects.toThrow('controlled failure');
      expect(f.track.stop).toHaveBeenCalledOnce();
      if (phase === 'mute') expect(f.publisher.publish).not.toHaveBeenCalled();
      else expect(f.publisher.unpublish).toHaveBeenCalledWith(f.track);
    }
  });

  it('rejects a closed owner before requesting another microphone', async () => {
    const f = fixture();
    f.preparation.close();
    await expect(f.preparation.prepare()).rejects.toThrow('microphone_session_closed');
    expect(f.publisher.createTracks).not.toHaveBeenCalled();
  });
});
