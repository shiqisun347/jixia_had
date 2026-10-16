import type { components } from '@jx/contracts';

import { ApiClientError, requestJson } from './auth-api';

export type MatchSnapshot = components['schemas']['MatchSnapshotResponse'];
export type MatchCommand = components['schemas']['MatchCommandRequest'];
export type MatchLiveKitToken = components['schemas']['MatchLiveKitTokenResponse'];
export type MatchTranscript = components['schemas']['TranscriptResponse'];

function transcriptResponse(value: MatchTranscript, matchId: string): MatchTranscript {
  // Validate the fields consumed by render. A proxy can return valid JSON with
  // the wrong shape; rejecting the read lets Query retain the last good data.
  if (
    !value ||
    value.match_id !== matchId ||
    !Number.isSafeInteger(value.context_version) ||
    !Array.isArray(value.speeches) ||
    !value.speeches.every(
      (speech) =>
        speech &&
        typeof speech.id === 'string' &&
        typeof speech.action_key === 'string' &&
        ['AFFIRMATIVE', 'NEGATIVE'].includes(speech.side) &&
        Number.isSafeInteger(speech.seat_no) &&
        (speech.display_text === null || typeof speech.display_text === 'string'),
    )
  ) {
    throw new ApiClientError(200, {
      error: { code: 'invalid_api_response', message: '服务返回异常，请稍后重试。' },
    });
  }
  return value;
}

export function newestMatchTranscript(
  current: MatchTranscript | undefined,
  incoming: MatchTranscript,
): MatchTranscript {
  return current && current.context_version > incoming.context_version ? current : incoming;
}

export const matchesApi = {
  prepare: (roomId: string) =>
    requestJson<MatchSnapshot>(`/api/rooms/${roomId}/prepare-match`, { method: 'POST' }),
  startRuntime: (roomId: string) =>
    requestJson<MatchSnapshot>(`/api/rooms/${roomId}/runtime-start`, { method: 'POST' }),
  snapshot: (matchId: string) => requestJson<MatchSnapshot>(`/api/matches/${matchId}/snapshot`),
  command: (matchId: string, payload: MatchCommand) =>
    requestJson<MatchSnapshot>(`/api/matches/${matchId}/command`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  liveKitToken: (matchId: string, connectionEpoch?: number | null) =>
    requestJson<MatchLiveKitToken>(`/api/matches/${matchId}/livekit-token`, {
      method: 'POST',
      body: connectionEpoch ? JSON.stringify({ connection_epoch: connectionEpoch }) : undefined,
    }),
  transcript: async (matchId: string) =>
    transcriptResponse(
      await requestJson<MatchTranscript>(`/api/matches/${matchId}/transcript`),
      matchId,
    ),
  updateDisplayText: async (matchId: string, speechId: string, displayText: string) =>
    transcriptResponse(
      await requestJson<MatchTranscript>(
        `/api/matches/${matchId}/speeches/${speechId}/display-text`,
        {
          method: 'PATCH',
          body: JSON.stringify({ display_text: displayText }),
        },
      ),
      matchId,
    ),
};
