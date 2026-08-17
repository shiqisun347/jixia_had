import { describe, expect, it } from 'vitest';

import { shouldConnectMatchAudio } from './match-audio-policy';

describe('match audio connection policy', () => {
  it('waits for a snapshot and skips terminal matches', () => {
    expect(shouldConnectMatchAudio(null)).toBe(false);
    expect(shouldConnectMatchAudio('FINISHED')).toBe(false);
    expect(shouldConnectMatchAudio('TERMINATED')).toBe(false);
  });

  it('keeps audio available for active and recoverable matches', () => {
    expect(shouldConnectMatchAudio('RUNNING')).toBe(true);
    expect(shouldConnectMatchAudio('PAUSED')).toBe(true);
    expect(shouldConnectMatchAudio('SYSTEM_RECOVERY')).toBe(true);
    expect(shouldConnectMatchAudio('ERROR')).toBe(true);
  });
});
