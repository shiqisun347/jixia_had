import type { MatchSnapshot } from '@/lib/matches-api';
import type { RoomSnapshot } from '@/lib/rooms-api';

export type DebateSeat = RoomSnapshot['seats'][number];

export type DebateCenterMode =
  | 'finished'
  | 'terminated'
  | 'frozen'
  | 'waiting'
  | 'countdown'
  | 'host'
  | 'preparation'
  | 'selection'
  | 'human-ready'
  | 'agent-preparing'
  | 'finalizing'
  | 'speaking'
  | 'idle';

export function resolveCenterMode(snapshot: MatchSnapshot): DebateCenterMode {
  if (snapshot.status === 'FINISHED') return 'finished';
  if (snapshot.status === 'TERMINATED') return 'terminated';
  // Resume countdown retains the original paused/recovery status until elapsed.
  if (snapshot.action_state === 'RESUME_COUNTDOWN') return 'countdown';
  if (['PAUSED', 'SYSTEM_RECOVERY', 'ERROR'].includes(snapshot.status)) return 'frozen';
  if (snapshot.status === 'START_PENDING_RUNTIME') return 'waiting';
  if (snapshot.status === 'START_COUNTDOWN') return 'countdown';
  if (snapshot.status !== 'RUNNING') return 'idle';
  switch (snapshot.action_state) {
    case 'HOST_ANNOUNCING':
      return 'host';
    case 'PREPARING':
      return 'preparation';
    case 'FREE_SELECTING':
      return 'selection';
    case 'HUMAN_READY_TO_START':
      return 'human-ready';
    case 'AGENT_PREPARING':
      return 'agent-preparing';
    case 'SPEECH_FINALIZING':
    case 'AGENT_FINALIZING':
      return 'finalizing';
    case 'HUMAN_SPEAKING':
    case 'AGENT_SPEAKING':
      return 'speaking';
    default:
      return 'idle';
  }
}

export function showsFreeDebateBudget(snapshot: MatchSnapshot): boolean {
  return (
    snapshot.status === 'RUNNING' &&
    snapshot.current_action?.action_kind === 'FREE_DEBATE' &&
    !['host', 'countdown', 'preparation'].includes(resolveCenterMode(snapshot))
  );
}

export function canRequestHand(snapshot: MatchSnapshot | null, side: string | undefined): boolean {
  if (!snapshot || !side || !showsFreeDebateBudget(snapshot) || !snapshot.hand_window_open)
    return false;
  const team = snapshot.experiment_team_state;
  if (team?.selection_phase === 'ALLOCATED') return false;
  if (team?.selection_remaining_ms != null && team.selection_remaining_ms <= 0) return false;
  if (
    team?.selection_phase === 'HUMAN_ONLY_WAIT' &&
    team.human_wait_remaining_ms != null &&
    team.human_wait_remaining_ms <= 0
  )
    return false;
  const candidateSide =
    snapshot.action_state === 'FREE_SELECTING'
      ? snapshot.free_holder_side
      : snapshot.current_speaker_side === 'AFFIRMATIVE'
        ? 'NEGATIVE'
        : 'AFFIRMATIVE';
  return side === candidateSide;
}

export function resolveCurrentSeat(
  seats: DebateSeat[] | undefined,
  snapshot: MatchSnapshot | null | undefined,
): DebateSeat | undefined {
  if (!seats || !snapshot || ['FINISHED', 'TERMINATED'].includes(snapshot.status)) return undefined;
  if (
    ![
      'HUMAN_READY_TO_START',
      'HUMAN_SPEAKING',
      'SPEECH_FINALIZING',
      'AGENT_PREPARING',
      'AGENT_SPEAKING',
      'AGENT_FINALIZING',
    ].includes(snapshot.action_state)
  ) {
    return undefined;
  }

  if (snapshot.current_speaker_user_id) {
    const human = seats.find((seat) => seat.user_id === snapshot.current_speaker_user_id);
    if (human) return human;
  }
  if (snapshot.current_agent_profile_id) {
    const agent = seats.find((seat) => seat.agent_profile_id === snapshot.current_agent_profile_id);
    if (agent) return agent;
  }
  if (snapshot.current_speaker_side && snapshot.current_speaker_seat_no !== null) {
    return seats.find(
      (seat) =>
        seat.side === snapshot.current_speaker_side &&
        seat.seat_no === snapshot.current_speaker_seat_no,
    );
  }
  return undefined;
}
