import { defineConfig, devices } from '@playwright/test';

const databaseUrl = process.env.TEST_DATABASE_URL;
if (!databaseUrl) throw new Error('TEST_DATABASE_URL is required');

export default defineConfig({
  testDir: './tests',
  testMatch: 'auth.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:3200',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'uv run --directory ../.. --package jx-core jx-core',
      url: 'http://127.0.0.1:8200/health/live',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: 'test',
        DATABASE_URL: databaseUrl,
        CORE_HOST: '127.0.0.1',
        CORE_PORT: '8200',
        CORS_ORIGINS: 'http://127.0.0.1:3200',
        AVATAR_STORAGE_DIR: process.env.AUTH_BROWSER_AVATAR_DIR ?? './data/test-avatars',
      },
    },
    {
      command: 'corepack pnpm exec next dev --hostname 127.0.0.1 --port 3200',
      url: 'http://127.0.0.1:3200/login',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        CORE_API_ORIGIN: 'http://127.0.0.1:8200',
      },
    },
  ],
  projects: [
    {
      name: 'auth-desktop-1440x900',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
});
