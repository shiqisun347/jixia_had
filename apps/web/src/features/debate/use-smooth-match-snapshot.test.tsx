import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MatchSnapshot } from '@/lib/matches-api';

import { useSmoothMatchSnapshot } from './use-smooth-match-snapshot';

const snapshot = (overrides: Partial<MatchSnapshot> = {}) =>
  ({
    status: 'RUNNING',
    action_state: 'HUMAN_SPEAKING',
    sequence: 5,
    speech_remaining_ms: 10_000,
    countdown_remaining_ms: null,
    free_affirmative_remaining_ms: 30_000,
    free_negative_remaining_ms: 30_000,
    current_speaker_side: 'AFFIRMATIVE',
    ...overrides,
  }) as MatchSnapshot;

describe('smooth match timing', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('decrements speech and the active free-debate side locally', () => {
    vi.useFakeTimers();
    let now = 1_000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { result } = renderHook(() => useSmoothMatchSnapshot(snapshot()));

    now = 2_200;
    act(() => vi.advanceTimersByTime(1_200));

    expect(result.current?.speech_remaining_ms).toBe(8_800);
    expect(result.current?.free_affirmative_remaining_ms).toBe(28_800);
    expect(result.current?.free_negative_remaining_ms).toBe(30_000);
  });

  it('freezes timing while paused', () => {
    vi.useFakeTimers();
    let now = 1_000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { result } = renderHook(() =>
      useSmoothMatchSnapshot(snapshot({ status: 'PAUSED', action_state: 'RECOVERY_REQUIRED' })),
    );

    now = 4_000;
    act(() => vi.advanceTimersByTime(3_000));

    expect(result.current?.speech_remaining_ms).toBe(10_000);
  });

  it('expires an authoritative hand window while waiting for a delayed state event', () => {
    vi.useFakeTimers();
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { result } = renderHook(() =>
      useSmoothMatchSnapshot(
        snapshot({
          action_state: 'SPEECH_FINALIZING',
          hand_window_open: true,
          experiment_team_state: {
            opportunity_id: 'next-opportunity',
            opportunity_generation: 2,
            selection_phase: 'COMPETING',
            agent_status: 'WAITING',
            selection_remaining_ms: 3000,
            human_wait_remaining_ms: null,
          },
        }),
      ),
    );
    now = 4100;
    act(() => vi.advanceTimersByTime(3100));
    expect(result.current?.experiment_team_state?.selection_remaining_ms).toBe(0);
    expect(result.current?.action_state).toBe('SPEECH_FINALIZING');
    expect(result.current?.speech_remaining_ms).toBe(10000);
  });

  it('decrements the authoritative start or resume countdown without sending commands', () => {
    vi.useFakeTimers();
    let now = 10;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { result } = renderHook(() =>
      useSmoothMatchSnapshot(
        snapshot({
          status: 'START_COUNTDOWN',
          action_state: 'NOT_STARTED',
          speech_remaining_ms: null,
          countdown_remaining_ms: 2_500,
        }),
      ),
    );

    now = 1_010;
    act(() => vi.advanceTimersByTime(1_000));

    expect(result.current?.countdown_remaining_ms).toBe(1_500);
  });
});
