import type { components } from '@jx/contracts';

import { requestJson } from './auth-api';

export type LobbyRoom = components['schemas']['LobbyRoomResponse'];
export type RoomSnapshot = components['schemas']['RoomSnapshotResponse'];
export type LobbyCatalog = components['schemas']['CatalogResponse'];
export type RoomCreatePayload = components['schemas']['RoomCreateRequest'];
export type RoomJoinPayload = components['schemas']['RoomJoinRequest'];
export type SeatSelectPayload = components['schemas']['SeatSelectRequest'];
export type DeviceCheckPayload = components['schemas']['DeviceCheckRequest'];
export type LiveKitProbeToken = components['schemas']['LiveKitProbeTokenResponse'];
export type RoomCodeLookup = components['schemas']['RoomCodeLookupResponse'];
export type SeatSwap = components['schemas']['SeatSwapResponse'];

export const roomsApi = {
  lobby: () => requestJson<LobbyRoom[]>('/api/lobby/rooms'),
  catalog: () => requestJson<LobbyCatalog>('/api/lobby/catalog'),
  lookup: (code: string) =>
    requestJson<RoomCodeLookup>(`/api/rooms/lookup?code=${encodeURIComponent(code)}`),
  humanTerms: () =>
    requestJson<components['schemas']['TermsResponse']>('/api/legal/human-participation/current'),
  liveKitProbeToken: () =>
    requestJson<LiveKitProbeToken>('/api/device/livekit-token', { method: 'POST' }),
  create: (payload: RoomCreatePayload) =>
    requestJson<RoomSnapshot>('/api/rooms', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  snapshot: (roomId: string) => requestJson<RoomSnapshot>(`/api/rooms/${roomId}/snapshot`),
  join: (roomId: string, payload: RoomJoinPayload) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/join`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  selectSeat: (roomId: string, payload: SeatSelectPayload) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/seat`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  seatSwapRequests: (roomId: string) =>
    requestJson<SeatSwap[]>(`/api/rooms/${roomId}/seat-swap-requests`),
  createSeatSwapRequest: (roomId: string, targetUserId: string) =>
    requestJson<SeatSwap>(`/api/rooms/${roomId}/seat-swap-requests`, {
      method: 'POST',
      body: JSON.stringify({ target_user_id: targetUserId }),
    }),
  respondSeatSwapRequest: (roomId: string, requestId: string, decision: 'ACCEPT' | 'REJECT') =>
    requestJson<SeatSwap>(`/api/rooms/${roomId}/seat-swap-requests/${requestId}/respond`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),
  changeRole: (roomId: string, memberRole: 'DEBATER' | 'SPECTATOR', termsVersion?: string) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/role`, {
      method: 'POST',
      body: JSON.stringify({
        member_role: memberRole,
        human_participation_terms_version: memberRole === 'DEBATER' ? termsVersion : null,
      }),
    }),
  deviceCheck: (roomId: string, payload: DeviceCheckPayload) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/device-check`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  invalidateDeviceCheck: (roomId: string) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/device-check/invalidate`, {
      method: 'POST',
    }),
  ready: (roomId: string, checkVersion: number) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/ready`, {
      method: 'POST',
      body: JSON.stringify({ check_version: checkVersion }),
    }),
  start: (roomId: string) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/start`, { method: 'POST' }),
  leave: (roomId: string) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/leave`, { method: 'POST' }),
  terminate: (roomId: string) =>
    requestJson<RoomSnapshot>(`/api/rooms/${roomId}/terminate`, { method: 'POST' }),
};
