import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const config = readFileSync(join(process.cwd(), 'apps/web/playwright.config.ts'), 'utf8');

test('the ordinary browser suite keeps resource usage bounded without local retries', () => {
  assert.match(config, /fullyParallel:\s*false/);
  assert.match(config, /workers:\s*2/);
  assert.match(config, /retries:\s*process\.env\.CI\s*\?\s*2\s*:\s*0/);
});
