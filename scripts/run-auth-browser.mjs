import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { playwrightEnvironment } from './playwright-environment.mjs';

const databaseUrl = process.env.TEST_DATABASE_URL;
if (!databaseUrl) {
  console.error('TEST_DATABASE_URL is required.');
  process.exit(2);
}
if (databaseUrl === process.env.DATABASE_URL) {
  console.error('TEST_DATABASE_URL must not equal DATABASE_URL.');
  process.exit(2);
}

const avatarDirectory = mkdtempSync(join(tmpdir(), 'jx-auth-browser-'));
const environment = {
  ...playwrightEnvironment(),
  TEST_DATABASE_URL: databaseUrl,
  AUTH_BROWSER_AVATAR_DIR: avatarDirectory,
};

try {
  execFileSync('uv', ['run', '--package', 'jx-core', 'alembic', 'upgrade', 'head'], {
    stdio: 'inherit',
    env: { ...environment, DATABASE_URL: databaseUrl },
  });
  execFileSync('uv', ['run', '--package', 'jx-core', 'python', 'scripts/reset-auth-test-db.py'], {
    stdio: 'inherit',
    env: environment,
  });
  const result = spawnSync(
    'corepack',
    [
      'pnpm',
      '--filter',
      '@jx/web',
      'exec',
      'playwright',
      'test',
      '--config',
      'playwright.auth.config.ts',
    ],
    { stdio: 'inherit', env: environment },
  );
  process.exitCode = result.status ?? 1;
} finally {
  try {
    execFileSync('uv', ['run', '--package', 'jx-core', 'python', 'scripts/reset-auth-test-db.py'], {
      stdio: 'inherit',
      env: environment,
    });
  } finally {
    rmSync(avatarDirectory, { recursive: true, force: true });
  }
}
