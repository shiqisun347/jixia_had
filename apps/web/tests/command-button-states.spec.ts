import { expect, test, type Page } from '@playwright/test';

const user = {
  id: '00000000-0000-0000-0000-000000000055',
  username: 'button_audit',
  real_name: '按钮审计用户',
  role: 'USER',
  must_change_password: false,
  avatar_version: 0,
  default_avatar_key: 'human-01',
  has_custom_avatar: false,
};

async function assertDisabledCommand(button: ReturnType<Page['getByRole']>) {
  await expect(button).toBeDisabled();
  await expect(button).toHaveCSS('background-color', 'rgb(226, 232, 240)');
  await expect(button).toHaveCSS('border-color', 'rgb(203, 213, 225)');
  await expect(button).toHaveCSS('color', 'rgb(100, 116, 139)');
  await expect(button).toHaveCSS('opacity', '1');
  await expect(button).toHaveCSS('cursor', 'not-allowed');
}

test('dark lobby command uses the same explicit disabled state', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route('**/api/lobby/rooms', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/users/*/avatar**', (route) => route.fulfill({ status: 204 }));
  await page.goto('/lobby');

  await assertDisabledCommand(page.getByRole('button', { name: '加入' }));
});

test('authentication command remains legible while a request is pending', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );
  let finishLogin: (() => void) | undefined;
  await page.route('**/api/auth/login', async (route) => {
    await new Promise<void>((resolve) => {
      finishLogin = resolve;
    });
    await route.fulfill({ status: 401, json: { error: { message: '账号或密码不正确' } } });
  });
  await page.goto('/login');
  await page.getByLabel('用户名').fill('button_audit');
  await page.locator('input[name="password"]').fill('not-a-real-password');
  await page.getByRole('button', { name: '登录并进入' }).click();

  await assertDisabledCommand(page.getByRole('button', { name: '正在登录' }));
  finishLogin?.();
});
