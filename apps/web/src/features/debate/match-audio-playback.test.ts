import { describe, expect, it } from 'vitest';

import {
  classifyRemoteAudioSource,
  humanAudioMuteStorageKey,
  parseMutedHumanUserIds,
  serializeMutedHumanUserIds,
  shouldMuteRemoteAudio,
} from './match-audio-playback';

const matchId = '70000000-0000-4000-8000-000000000001';
const userId = '10000000-0000-4000-8000-000000000001';

describe('remote match audio classification', () => {
  it('recognizes current-match human and agent identities', () => {
    expect(
      classifyRemoteAudioSource(`jx-human-${matchId}-${userId}-4-ab12cd34`, matchId, [userId]),
    ).toEqual({ kind: 'HUMAN', userId });
    expect(classifyRemoteAudioSource(`jx-agent-${matchId}-ab12cd34`, matchId, [userId])).toEqual({
      kind: 'AGENT',
    });
  });

  it('does not treat unknown users, malformed identities, or another match as human', () => {
    expect(classifyRemoteAudioSource(`jx-human-${matchId}-unknown-1-x`, matchId, [userId])).toEqual(
      {
        kind: 'OTHER',
      },
    );
    expect(classifyRemoteAudioSource('user-legacy', matchId, [userId])).toEqual({ kind: 'OTHER' });
    expect(
      classifyRemoteAudioSource(`jx-human-other-match-${userId}-1-x`, matchId, [userId]),
    ).toEqual({ kind: 'OTHER' });
  });

  it('recognizes the same human after a LiveKit reconnect changes the suffix', () => {
    const first = classifyRemoteAudioSource(`jx-human-${matchId}-${userId}-4-first`, matchId, [
      userId,
    ]);
    const reconnected = classifyRemoteAudioSource(
      `jx-human-${matchId}-${userId}-5-second`,
      matchId,
      [userId],
    );
    expect(first).toEqual({ kind: 'HUMAN', userId });
    expect(reconnected).toEqual(first);
  });
});

describe('remote match audio mute policy', () => {
  const mutedUsers = new Set([userId]);

  it('mutes only selected humans when global output remains enabled', () => {
    expect(shouldMuteRemoteAudio(false, { kind: 'HUMAN', userId }, mutedUsers)).toBe(true);
    expect(
      shouldMuteRemoteAudio(false, { kind: 'HUMAN', userId: 'another-user' }, mutedUsers),
    ).toBe(false);
    expect(shouldMuteRemoteAudio(false, { kind: 'AGENT' }, mutedUsers)).toBe(false);
    expect(shouldMuteRemoteAudio(false, { kind: 'OTHER' }, mutedUsers)).toBe(false);
  });

  it('lets global output mute override every source', () => {
    expect(shouldMuteRemoteAudio(true, { kind: 'HUMAN', userId }, new Set())).toBe(true);
    expect(shouldMuteRemoteAudio(true, { kind: 'AGENT' }, new Set())).toBe(true);
    expect(shouldMuteRemoteAudio(true, { kind: 'OTHER' }, new Set())).toBe(true);
  });
});

describe('per-match human mute persistence', () => {
  it('uses a match-scoped key and round-trips unique user ids', () => {
    expect(humanAudioMuteStorageKey(matchId)).toContain(matchId);
    expect(parseMutedHumanUserIds(serializeMutedHumanUserIds([userId, userId]))).toEqual([userId]);
  });

  it('ignores invalid stored values', () => {
    expect(parseMutedHumanUserIds('{invalid')).toEqual([]);
    expect(parseMutedHumanUserIds(JSON.stringify([userId, null, 4, '']))).toEqual([userId]);
  });
});
