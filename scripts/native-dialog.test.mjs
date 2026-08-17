import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const sourceRoot = fileURLToPath(new URL('../apps/web/src/', import.meta.url));
const forbidden = /(?:window\s*\.\s*)?(?:confirm|alert|prompt)\s*\(/;

async function productionSources(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await productionSources(path)));
    else if (
      ['.ts', '.tsx'].includes(extname(entry.name)) &&
      !entry.name.includes('.test.') &&
      !entry.name.includes('.stories.')
    ) {
      files.push(path);
    }
  }
  return files;
}

test('production web source does not use native browser dialogs', async () => {
  const offenders = [];
  for (const file of await productionSources(sourceRoot)) {
    if (forbidden.test(await readFile(file, 'utf8'))) offenders.push(file);
  }
  assert.deepEqual(offenders, []);
});
