import { requestJson } from './auth-api';

export type ShowcaseParticipant = {
  id: string;
  display_name: string;
  kind: string;
  side: string;
  seat_no: number;
};

export type Showcase = {
  match_id: string;
  phase: 'JUDGE_REVIEW' | 'AUDIENCE_VOTE' | 'AUDIENCE_RESULTS' | 'AWARDS' | 'CLOSED';
  published: boolean;
  display_payload: Record<string, unknown>;
  participants: ShowcaseParticipant[];
  judge: {
    id: string;
    comments: string;
    winner_side: string;
    best_participant_id: string;
    is_published: boolean;
    published_at: string | null;
  } | null;
  audience: {
    total_votes: number;
    winner_counts: Record<string, number>;
    ranking: Array<{
      rank: number;
      participant_id: string;
      display_name: string;
      side: string;
      seat_no: number;
      points: number;
      ranking_votes: number;
    }>;
  };
  can_manage: boolean;
  has_voted: boolean;
};

export const showcaseApi = {
  get: (matchId: string) => requestJson<Showcase>(`/api/matches/${matchId}/showcase`),
  phase: (
    matchId: string,
    payload: {
      phase: Showcase['phase'];
      published: boolean;
      display_payload?: Record<string, unknown>;
    },
  ) =>
    requestJson<Showcase>(`/api/matches/${matchId}/showcase/phase`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  judge: (
    matchId: string,
    payload: {
      comments: string;
      winner_side: string;
      best_participant_id: string;
      published: boolean;
    },
  ) =>
    requestJson<Showcase>(`/api/matches/${matchId}/showcase/judge`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  vote: (matchId: string, payload: { winner_side: string; ranking: string[] }) =>
    requestJson<Showcase>(`/api/matches/${matchId}/showcase/vote`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
