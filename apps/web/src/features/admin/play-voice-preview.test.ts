import { describe, expect, it, vi } from 'vitest';

import { playVoicePreview } from './play-voice-preview';

describe('playVoicePreview', () => {
  it('generates once and returns the URL when playback is blocked', async () => {
    const generate = vi.fn().mockResolvedValue(undefined);
    await expect(
      playVoicePreview({
        generate,
        makeUrl: () => '/preview?ts=1',
        play: vi.fn().mockRejectedValue(new Error('blocked')),
      }),
    ).resolves.toEqual({ url: '/preview?ts=1', played: false });
    expect(generate).toHaveBeenCalledTimes(1);
  });

  it('replays a cached URL without generating again', async () => {
    const generate = vi.fn();
    const play = vi.fn().mockResolvedValue(undefined);
    await expect(
      playVoicePreview({
        cachedUrl: '/preview?ts=1',
        generate,
        makeUrl: () => '/preview?ts=2',
        play,
      }),
    ).resolves.toEqual({ url: '/preview?ts=1', played: true });
    expect(generate).not.toHaveBeenCalled();
    expect(play).toHaveBeenCalledWith('/preview?ts=1');
  });

  it('keeps generation failures distinct from playback failures', async () => {
    const failure = new Error('generation failed');
    await expect(
      playVoicePreview({
        generate: vi.fn().mockRejectedValue(failure),
        makeUrl: () => '/preview',
        play: vi.fn(),
      }),
    ).rejects.toBe(failure);
  });
});
