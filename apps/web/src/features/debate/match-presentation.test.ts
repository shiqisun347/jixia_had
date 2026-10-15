import { describe, expect, it } from 'vitest';
import type { MatchSnapshot } from '@/lib/matches-api';

import { canRequestHand, resolveCenterMode } from './match-presentation';

function snapshot(overrides: Partial<MatchSnapshot> = {}): MatchSnapshot {
  return {
    status: 'RUNNING',
    action_state: 'SPEECH_FINALIZING',
    hand_window_open: true,
    current_action: { action_kind: 'FREE_DEBATE' },
    current_speaker_side: 'AFFIRMATIVE',
    experiment_team_state: { selection_phase: 'COMPETING', selection_remaining_ms: 3000 },
    ...overrides,
  } as unknown as MatchSnapshot;
}

describe('match presentation priority and hand capability', () => {
  it('gives terminal status precedence over stale host and countdown actions', () => {
    expect(
      resolveCenterMode(snapshot({ status: 'FINISHED', action_state: 'HOST_ANNOUNCING' })),
    ).toBe('finished');
    expect(
      resolveCenterMode(snapshot({ status: 'TERMINATED', action_state: 'RESUME_COUNTDOWN' })),
    ).toBe('terminated');
  });

  it('recognizes the legitimate resume countdown while the status remains paused', () => {
    expect(
      resolveCenterMode(snapshot({ status: 'PAUSED', action_state: 'RESUME_COUNTDOWN' })),
    ).toBe('countdown');
  });

  it('permits only the candidate side during the authoritative open window', () => {
    expect(canRequestHand(snapshot(), 'NEGATIVE')).toBe(true);
    expect(canRequestHand(snapshot(), 'AFFIRMATIVE')).toBe(false);
    expect(canRequestHand(snapshot(), undefined)).toBe(false);
  });

  it.each([0, -1])('rejects a window whose effective remaining time is %i', (remaining) => {
    const base = snapshot();
    expect(
      canRequestHand(
        {
          ...base,
          experiment_team_state: {
            ...base.experiment_team_state!,
            selection_remaining_ms: remaining,
          },
        },
        'NEGATIVE',
      ),
    ).toBe(false);
  });

  it.each(['FINISHED', 'TERMINATED', 'PAUSED', 'ERROR'])(
    'rejects stale open flags while %s',
    (status) => {
      expect(canRequestHand(snapshot({ status }), 'NEGATIVE')).toBe(false);
    },
  );
});
