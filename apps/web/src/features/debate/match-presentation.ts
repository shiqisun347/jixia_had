import type { MatchSnapshot } from '@/lib/matches-api';
import type { RoomSnapshot } from '@/lib/rooms-api';

export type DebateSeat = RoomSnapshot['seats'][number];

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
