import type { components } from '@jx/contracts';

import { requestJson } from './auth-api';

export type MatchSnapshot = components['schemas']['MatchSnapshotResponse'];
export type MatchCommand = components['schemas']['MatchCommandRequest'];
export type MatchLiveKitToken = components['schemas']['MatchLiveKitTokenResponse'];
export type MatchTranscript = components['schemas']['TranscriptResponse'];

export const matchesApi = {
  startRuntime: (roomId: string) =>
    requestJson<MatchSnapshot>(`/api/rooms/${roomId}/runtime-start`, { method: 'POST' }),
  snapshot: (matchId: string) => requestJson<MatchSnapshot>(`/api/matches/${matchId}/snapshot`),
  command: (matchId: string, payload: MatchCommand) =>
    requestJson<MatchSnapshot>(`/api/matches/${matchId}/command`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  liveKitToken: (matchId: string) =>
    requestJson<MatchLiveKitToken>(`/api/matches/${matchId}/livekit-token`, { method: 'POST' }),
  transcript: (matchId: string) =>
    requestJson<MatchTranscript>(`/api/matches/${matchId}/transcript`),
  updateDisplayText: (matchId: string, speechId: string, displayText: string) =>
    requestJson<MatchTranscript>(`/api/matches/${matchId}/speeches/${speechId}/display-text`, {
      method: 'PATCH',
      body: JSON.stringify({ display_text: displayText }),
    }),
};
