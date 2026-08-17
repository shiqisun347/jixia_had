import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const files = [
  'apps/web/src/features/debate/debate-page-layout.tsx',
  'apps/web/src/features/rooms/room-preparation-view.tsx',
  'apps/web/src/features/auth/me-page.tsx',
  'apps/web/src/features/auth/profile-dialog.tsx',
  'apps/web/src/features/auth/auth-forms.tsx',
  'apps/web/src/features/leaderboard/leaderboard-page.tsx',
  'apps/web/src/features/home/home-prototype.tsx',
  'apps/web/src/app/admin/agents/page.tsx',
  'apps/web/src/app/admin/voices/page.tsx',
];

test('all audited human and Agent identity avatars use the shared circular shape', async () => {
  for (const path of files) {
    const source = await readFile(path, 'utf8');
    const avatarLines = source
      .split('\n')
      .filter((line) => /avatar|occupant/i.test(line) && /className/.test(line));
    for (const line of avatarLines) {
      assert.doesNotMatch(
        line,
        /rounded-(?:xl|2xl|3xl|\[[^\]]+\])/,
        `${path} contains a non-circular identity avatar class: ${line.trim()}`,
      );
    }
  }
});

test('the shared identity avatar and avatar picker remain circular', async () => {
  const css = await readFile('apps/web/src/app/globals.css', 'utf8');
  assert.match(css, /\.jx-identity-avatar\s*\{[^}]*border-radius:\s*50%/s);
  assert.match(css, /\.avatar-preset\s*\{[^}]*border-radius:\s*50%/s);
});
