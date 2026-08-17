import type { LobbyRoom, RoomSnapshot } from '@/lib/rooms-api';

type RoomEntryFacts = Pick<
  LobbyRoom | RoomSnapshot,
  'id' | 'status' | 'match_id' | 'viewer_membership_state'
>;

export type RoomEntryKind = 'WAITING_ROOM' | 'LIVE_MATCH' | 'POSTMATCH' | 'CLOSED';

export interface RoomEntryTarget {
  kind: RoomEntryKind;
  href: string;
  label: string;
}

export function resolveRoomEntry(room: RoomEntryFacts): RoomEntryTarget {
  if (room.status === 'FINISHED' && room.match_id) {
    return { kind: 'POSTMATCH', href: `/matches/${room.match_id}`, label: '查看赛后记录' };
  }
  if (room.status === 'TERMINATED') {
    return { kind: 'CLOSED', href: '/lobby', label: '房间已结束' };
  }
  if (
    room.match_id &&
    room.viewer_membership_state === 'ACTIVE' &&
    ['START_PENDING_RUNTIME', 'RUNNING', 'PAUSED'].includes(room.status)
  ) {
    return {
      kind: 'LIVE_MATCH',
      href: `/debate?match_id=${encodeURIComponent(room.match_id)}`,
      label: '返回比赛',
    };
  }
  if (['RUNNING', 'PAUSED', 'START_PENDING_RUNTIME'].includes(room.status)) {
    return {
      kind: 'WAITING_ROOM',
      href: `/rooms/${room.id}`,
      label: room.viewer_membership_state === 'ACTIVE' ? '进入比赛' : '进入观战',
    };
  }
  return {
    kind: 'WAITING_ROOM',
    href: `/rooms/${room.id}`,
    label: room.viewer_membership_state === 'ACTIVE' ? '返回房间' : '进入房间',
  };
}
