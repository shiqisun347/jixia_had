import { describe, expect, it } from 'vitest';

import { resolveRoomEntry } from './room-entry';

function room(overrides: Partial<Parameters<typeof resolveRoomEntry>[0]> = {}) {
  return {
    id: 'room-1',
    status: 'WAITING',
    match_id: null,
    viewer_membership_state: 'NONE' as const,
    ...overrides,
  };
}

describe('resolveRoomEntry', () => {
  it('keeps a waiting room as a non-mutating room page', () => {
    expect(resolveRoomEntry(room())).toMatchObject({ kind: 'WAITING_ROOM', href: '/rooms/room-1' });
    expect(resolveRoomEntry(room({ viewer_membership_state: 'ACTIVE' })).label).toBe('返回房间');
  });

  it('sends active members to the real match route', () => {
    expect(
      resolveRoomEntry(
        room({ status: 'RUNNING', match_id: 'match-1', viewer_membership_state: 'ACTIVE' }),
      ),
    ).toMatchObject({ kind: 'LIVE_MATCH', href: '/debate?match_id=match-1' });
  });

  it('sends new viewers through the room gateway', () => {
    expect(resolveRoomEntry(room({ status: 'PAUSED', match_id: 'match-1' }))).toMatchObject({
      kind: 'WAITING_ROOM',
      label: '进入观战',
    });
  });

  it('never routes a terminated room to a stale match', () => {
    expect(resolveRoomEntry(room({ status: 'TERMINATED', match_id: 'match-1' }))).toMatchObject({
      kind: 'CLOSED',
      href: '/lobby',
    });
  });
});
