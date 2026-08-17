import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const pages = [
  'apps/web/src/features/leaderboard/leaderboard-page.tsx',
  'apps/web/src/features/rooms/lobby-page.tsx',
  'apps/web/src/features/rooms/create-room-page.tsx',
  'apps/web/src/features/rooms/join-room-page.tsx',
  'apps/web/src/features/rooms/room-page.tsx',
  'apps/web/src/features/auth/auth-loading.tsx',
  'apps/web/src/features/auth/protected-user-page.tsx',
  'apps/web/src/features/auth/protected-debate.tsx',
  'apps/web/src/features/auth/me-page.tsx',
  'apps/web/src/app/guide/page.tsx',
  'apps/web/src/app/admin/layout.tsx',
  'apps/web/src/app/matches/[matchId]/page.tsx',
];

test('normal pages use the header-aware viewport baseline', async () => {
  const css = await readFile('apps/web/src/app/globals.css', 'utf8');
  assert.match(
    css,
    /\.jx-page-viewport\s*\{[^}]*min-height:\s*calc\(100dvh - var\(--jx-header-height\) - 1px\)/s,
  );
  for (const path of pages) {
    const source = await readFile(path, 'utf8');
    assert.match(source, /jx-page-viewport/, `${path} must use the shared viewport baseline`);
  }
});

test('audited pages no longer hard-code the desktop header height', async () => {
  for (const path of pages) {
    const source = await readFile(path, 'utf8');
    assert.doesNotMatch(
      source,
      /100dvh-5\.2rem/,
      `${path} has a mobile-incorrect fixed header height`,
    );
  }
});
