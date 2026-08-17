import assert from 'node:assert/strict';
import { test } from 'node:test';

import { playwrightEnvironment } from './playwright-environment.mjs';

test('Playwright environment removes NO_COLOR without mutating its source', () => {
  const source = { NO_COLOR: '1', PATH: '/bin' };

  const result = playwrightEnvironment(source);

  assert.deepEqual(result, { PATH: '/bin' });
  assert.deepEqual(source, { NO_COLOR: '1', PATH: '/bin' });
});

test('Playwright environment preserves FORCE_COLOR and unrelated variables', () => {
  assert.deepEqual(
    playwrightEnvironment({
      FORCE_COLOR: '1',
      NO_COLOR: '1',
      TEST_DATABASE_URL: 'postgres://test',
    }),
    { FORCE_COLOR: '1', TEST_DATABASE_URL: 'postgres://test' },
  );
  assert.deepEqual(playwrightEnvironment({ PATH: '/bin' }), { PATH: '/bin' });
});
