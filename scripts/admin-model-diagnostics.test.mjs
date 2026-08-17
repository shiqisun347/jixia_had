import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const route = readFileSync('apps/core/src/jx_core/admin_routes.py', 'utf8');
const page = readFileSync('apps/web/src/app/admin/matches/page.tsx', 'utf8');

test('model diagnostics stay scoped to one match and bounded', () => {
  assert.match(route, /AgentGeneration\.match_id == match_id/);
  assert.match(route, /limit: int = Query\(default=25, ge=1, le=100\)/);
  assert.match(route, /AgentGeneration\.created_at\.desc\(\)/);
});

test('admin diagnostics are loaded on demand and avoid secret fields', () => {
  assert.match(page, /enabled: Boolean\(diagnosticMatch\)/);
  assert.match(page, /enabled: expanded/);
  assert.match(page, /脱敏输入快照/);
  assert.doesNotMatch(route, /api_key:.*AgentGenerationView/);
  assert.doesNotMatch(route, /provider_response:.*AgentGenerationView/);
});
