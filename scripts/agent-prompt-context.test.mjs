import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const runtime = readFileSync('apps/core/src/jx_core/agent/runtime.py', 'utf8');

test('agent prompt carries readable current/next stage and latest display history', () => {
  assert.match(runtime, /"current_stage": current_stage/);
  assert.match(runtime, /"next_stage": next_stage/);
  assert.match(runtime, /"debate_position": _debate_position/);
  assert.match(runtime, /"content": speech\.display_text or ""/);
  assert.match(runtime, /"stage": _stage_name_for_action/);
});
