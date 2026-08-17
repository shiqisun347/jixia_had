import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync('apps/web/src/app/matches/[matchId]/page.tsx', 'utf8');
const route = readFileSync('apps/core/src/jx_core/postmatch_routes.py', 'utf8');

test('postmatch judge polling is bounded and terminal-aware', () => {
  assert.match(page, /Date\.now\(\) - startedAt >= 120_000/);
  assert.match(page, /window\.setInterval\(\(\) => void poll\(\), 2_000\)/);
  assert.match(page, /window\.setTimeout\(\(\) => window\.clearInterval\(timer\), 120_000\)/);
  assert.match(page, /postmatchStatus !== 'FINISHED'/);
  assert.match(page, /judgeStatus === 'SUCCEEDED'/);
  assert.match(page, /judgeStatus === 'FAILED'/);
});

test('postmatch retry permission is server-derived', () => {
  assert.match(route, /can_retry_judge: bool = False/);
  assert.match(route, /can_retry_judge_for_viewer/);
  assert.match(route, /judge\/retry/);
});
