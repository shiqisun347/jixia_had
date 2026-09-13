import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const routes = readFileSync('apps/core/src/jx_core/matches/routes.py', 'utf8');

test('ordinary match commands use room identity rather than global admin role', () => {
  assert.match(routes, /def match_page_permissions/);
  assert.match(routes, /async def _match_page_permissions/);
  assert.match(routes, /privileged, authorized = await _match_page_permissions/);
  assert.match(routes, /experiment_controller = bool\(/);
  assert.match(routes, /actor_role == "ADMIN"/);
  assert.doesNotMatch(routes, /privileged = context\.role == "ADMIN" or room\.organizer_user_id/);
});
