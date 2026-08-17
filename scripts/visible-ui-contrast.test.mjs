import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const checks = [
  {
    path: 'apps/web/src/features/rooms/lobby-page.tsx',
    required: /font-mono text-xs font-bold tracking-\[0\.18em\] text-slate-500/,
    label: 'lobby room code',
  },
  {
    path: 'apps/web/src/features/leaderboard/leaderboard-page.tsx',
    required: /每日排名快照生成后会显示在这里。<\/p>/,
    forbidden: /text-xs text-slate-400">每日排名快照/,
    label: 'leaderboard empty-state explanation',
  },
  {
    path: 'apps/web/src/features/auth/auth-forms.tsx',
    required: /rounded-lg text-slate-500 hover:bg-slate-50 hover:text-slate-700/,
    label: 'password visibility control',
  },
  {
    path: 'apps/web/src/features/admin/admin-controls.tsx',
    required: /rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-800/,
    label: 'admin drawer close control',
  },
];

test('meaningful text and active icons avoid slate-400 on white surfaces', async () => {
  for (const check of checks) {
    const source = await readFile(check.path, 'utf8');
    assert.match(source, check.required, `${check.label} must use the audited visible color`);
    if (check.forbidden) {
      assert.doesNotMatch(source, check.forbidden, `${check.label} regressed to slate-400`);
    }
  }

  const debate = await readFile('apps/web/src/features/debate/debate-page-layout.tsx', 'utf8');
  assert.doesNotMatch(
    debate,
    /text-slate-400/,
    'debate control labels and network explanation must remain readable on white surfaces',
  );
});
