import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { projectRoot } from './build-web.mjs';

const sourceLogo = join(projectRoot, 'logo.png');
const preservedWebLogo = join(projectRoot, 'apps/web/public/assets/logo.png');
const interfaceLogo = join(projectRoot, 'apps/web/public/assets/logo-ui.webp');

test('the interface logo is a bounded 192px WebP while the supplied PNG remains unchanged', () => {
  const interfaceBytes = readFileSync(interfaceLogo);
  const vp8Header = interfaceBytes.indexOf(Buffer.from('VP8 '));

  assert.equal(interfaceBytes.subarray(0, 4).toString('ascii'), 'RIFF');
  assert.equal(interfaceBytes.subarray(8, 12).toString('ascii'), 'WEBP');
  assert.notEqual(vp8Header, -1);
  assert.equal(interfaceBytes.readUInt16LE(vp8Header + 14) & 0x3fff, 192);
  assert.equal(interfaceBytes.readUInt16LE(vp8Header + 16) & 0x3fff, 192);
  assert.ok(statSync(interfaceLogo).size < 100 * 1024);
  assert.deepEqual(readFileSync(preservedWebLogo), readFileSync(sourceLogo));
});

test('the read-only Web runtime serves pre-optimized images without a disk cache', () => {
  const nextConfig = readFileSync(join(projectRoot, 'apps/web/next.config.ts'), 'utf8');

  assert.match(nextConfig, /images:\s*\{\s*unoptimized:\s*true\s*\}/);
});
