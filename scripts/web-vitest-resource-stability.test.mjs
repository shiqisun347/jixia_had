import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

test('ordinary Web unit tests use bounded workers without retries or longer timeouts', () => {
  const config = readFileSync('apps/web/vitest.config.ts', 'utf8');
  assert.match(config, /maxWorkers:\s*2/);
  assert.doesNotMatch(config, /\bretry\s*:/);
  assert.doesNotMatch(config, /\btestTimeout\s*:/);
});
