import { test as base } from '@playwright/test';

export const test = base.extend({
  page: async ({ page }, applyFixture) => {
    await page.route('**/api/experiments/capabilities', (route) =>
      route.fulfill({
        json: { creation_enabled: true, history_readable: true, target_version: '2.1.0' },
      }),
    );
    await applyFixture(page);
  },
});

export { expect } from '@playwright/test';
export type { Page } from '@playwright/test';
