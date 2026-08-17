import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

test('Next uses an isolated dev directory while production keeps .next', () => {
  const config = readFileSync('apps/web/next.config.ts', 'utf8');
  const dev = readFileSync('scripts/web-dev.mjs', 'utf8');
  const build = readFileSync('scripts/build-web.mjs', 'utf8');
  const eslint = readFileSync('apps/web/eslint.config.mjs', 'utf8');
  assert.match(config, /distDir: process\.env\.NEXT_DIST_DIR \?\? '\.next'/);
  assert.match(dev, /NEXT_DIST_DIR: '\.next-dev'/);
  assert.match(build, /join\(resolvedWebRoot, '\.next'\)/);
  assert.match(build, /NEXT_DIST_DIR: '\.next'/);
  assert.match(eslint, /['"]\.next-dev\/\*\*['"]/);
});
