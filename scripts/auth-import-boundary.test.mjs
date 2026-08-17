import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { test } from 'node:test';

import { projectRoot } from './build-web.mjs';

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : [path];
  });
}

test('production Web source imports auth modules directly instead of the broad barrel', () => {
  const sourceRoot = join(projectRoot, 'apps/web/src');
  const violations = sourceFiles(sourceRoot)
    .filter((path) => ['.ts', '.tsx'].includes(extname(path)))
    .filter((path) => !path.endsWith('/features/auth/index.ts'))
    .filter((path) => !/\.(?:test|stories)\.tsx?$/.test(path))
    .filter((path) => /from\s+['"]@\/features\/auth['"]/.test(readFileSync(path, 'utf8')))
    .map((path) => relative(projectRoot, path));

  assert.deepEqual(violations, []);
});
