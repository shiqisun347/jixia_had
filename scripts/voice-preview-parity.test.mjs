import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const adminRoutes = readFileSync(
  join(process.cwd(), 'apps/core/src/jx_core/admin_routes.py'),
  'utf8',
);

test('admin voice previews apply the configured runtime playback gain off the event loop', () => {
  assert.match(
    adminRoutes,
    /await asyncio\.to_thread\(\s*apply_ogg_opus_gain, bytes\(audio\), voice\.playback_gain\s*\)/,
  );
  assert.match(adminRoutes, /"byte_count": len\(preview_audio\)/);
});
