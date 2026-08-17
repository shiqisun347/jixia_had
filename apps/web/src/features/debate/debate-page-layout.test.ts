import { describe, expect, it } from 'vitest';

import { freeDebateProgress } from './debate-page-layout';

describe('freeDebateProgress', () => {
  it.each([
    [300_000, 300_000, 100],
    [150_000, 300_000, 50],
    [0, 300_000, 0],
    [450_000, 300_000, 100],
    [-1_000, 300_000, 0],
  ])('maps %i of %i to %i percent', (remaining, total, expected) => {
    expect(freeDebateProgress(remaining, total)).toBe(expected);
  });

  it.each([null, 0, -1])('returns a neutral result for invalid total %s', (total) => {
    expect(freeDebateProgress(100_000, total)).toBeNull();
  });
});
