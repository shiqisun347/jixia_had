import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ status: 401, json: { error: { code: 'not_authenticated' } } }),
  );
  await page.route('**/api/legal/platform-terms/current', (route) =>
    route.fulfill({ json: { version: '2026-08-01', title: '平台条款', content: '测试条款' } }),
  );
});

test('registration avatar choices use human-readable names and native selection state', async ({
  page,
}) => {
  await page.goto('/register');
  const first = page.getByRole('radio', { name: '头像 1', exact: true });
  const second = page.getByRole('radio', { name: '头像 2', exact: true });
  await expect(first).toBeChecked();
  await second.check();
  await expect(second).toBeChecked();
  await expect(first).not.toBeChecked();
  await expect(page.getByRole('radio', { name: /human-/ })).toHaveCount(0);
});
