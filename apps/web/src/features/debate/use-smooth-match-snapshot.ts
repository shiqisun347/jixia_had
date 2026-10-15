'use client';

import { useEffect, useState } from 'react';

import type { MatchSnapshot } from '@/lib/matches-api';

const SPEAKING_STATES = new Set(['HUMAN_SPEAKING', 'AGENT_SPEAKING']);

function reduceRemaining(value: number | null | undefined, elapsed: number) {
  return value === null || value === undefined ? null : Math.max(0, value - elapsed);
}

export function useSmoothMatchSnapshot(snapshot: MatchSnapshot | null): MatchSnapshot | null {
  const status = snapshot?.status ?? 'NOT_STARTED';
  const actionState = snapshot?.action_state ?? 'NOT_STARTED';
  const sequence = snapshot?.sequence ?? -1;
  const team = snapshot?.experiment_team_state;
  const timingKey = `${sequence}:${status}:${actionState}:${snapshot?.speech_remaining_ms ?? ''}:${snapshot?.countdown_remaining_ms ?? ''}:${snapshot?.free_affirmative_remaining_ms ?? ''}:${snapshot?.free_negative_remaining_ms ?? ''}:${team?.selection_remaining_ms ?? ''}:${team?.human_wait_remaining_ms ?? ''}`;
  const [clock, setClock] = useState({ key: '', at: 0, now: 0 });
  const speechTicks =
    status === 'RUNNING' && (SPEAKING_STATES.has(actionState) || actionState === 'PREPARING');
  const countdownTicks = status === 'START_COUNTDOWN' || actionState === 'RESUME_COUNTDOWN';
  const selectionTicks =
    status === 'RUNNING' &&
    Boolean(snapshot?.hand_window_open) &&
    ((team?.selection_remaining_ms ?? 0) > 0 || (team?.human_wait_remaining_ms ?? 0) > 0);

  useEffect(() => {
    const startedAt = performance.now();
    const update = () =>
      setClock((current) => ({
        key: timingKey,
        at: current.key === timingKey ? current.at : startedAt,
        now: performance.now(),
      }));
    const initial = window.setTimeout(update, 0);
    const timer =
      speechTicks || countdownTicks || selectionTicks ? window.setInterval(update, 200) : null;
    return () => {
      window.clearTimeout(initial);
      if (timer !== null) window.clearInterval(timer);
    };
  }, [countdownTicks, speechTicks, selectionTicks, timingKey]);

  const elapsed = clock.key === timingKey ? Math.max(0, clock.now - clock.at) : 0;
  const speechElapsed = speechTicks ? elapsed : 0;
  const countdownElapsed = countdownTicks ? elapsed : 0;
  const activeFreeSide =
    speechTicks && SPEAKING_STATES.has(actionState) ? snapshot?.current_speaker_side : null;

  if (!snapshot) return null;

  return {
    ...snapshot,
    experiment_team_state: team
      ? {
          ...team,
          selection_remaining_ms: reduceRemaining(
            team.selection_remaining_ms,
            selectionTicks ? elapsed : 0,
          ),
          human_wait_remaining_ms: reduceRemaining(
            team.human_wait_remaining_ms,
            selectionTicks ? elapsed : 0,
          ),
        }
      : team,
    speech_remaining_ms: reduceRemaining(snapshot.speech_remaining_ms, speechElapsed),
    countdown_remaining_ms: reduceRemaining(snapshot.countdown_remaining_ms, countdownElapsed),
    free_affirmative_remaining_ms: reduceRemaining(
      snapshot.free_affirmative_remaining_ms,
      activeFreeSide === 'AFFIRMATIVE' ? speechElapsed : 0,
    ),
    free_negative_remaining_ms: reduceRemaining(
      snapshot.free_negative_remaining_ms,
      activeFreeSide === 'NEGATIVE' ? speechElapsed : 0,
    ),
  };
}
