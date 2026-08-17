import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

test('the base waiting-room story uses unique Agent profiles', () => {
  const source = readFileSync('apps/web/src/features/rooms/rooms.stories.tsx', 'utf8');
  const baseRoom = source.slice(
    source.indexOf('const room = {'),
    source.indexOf('const mixed4v4Room'),
  );
  const ids = [...baseRoom.matchAll(/agent_profile_id: '([^']+)'/g)].map((match) => match[1]);
  assert.equal(ids.length, 2);
  assert.equal(new Set(ids).size, ids.length);
});
