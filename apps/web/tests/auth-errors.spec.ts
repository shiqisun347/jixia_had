import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ status: 401, json: { error: { code: 'not_authenticated' } } }),
  );
  await page.route('**/api/legal/platform-terms/current', (route) =>
    route.fulfill({ json: { version: '2026-08-01', title: '平台条款', content: '测试条款' } }),
  );
});

test('login field errors are announced and associated with their controls', async ({ page }) => {
  await page.goto('/login');
  const password = page.getByLabel('密码', { exact: true });
  await expect(password).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: '显示密码' }).click();
  await expect(password).toHaveAttribute('type', 'text');
  await expect(page.getByRole('button', { name: '隐藏密码' })).toBeVisible();
  await page.getByRole('button', { name: '登录并进入' }).click();

  const username = page.getByLabel('用户名');
  await expect(username).toHaveAttribute('aria-invalid', 'true');
  await expect(username).toHaveAttribute('aria-describedby', 'login-username-error');
  await expect(page.locator('#login-username-error')).toHaveAttribute('role', 'alert');
  await expect(password).toHaveAttribute('aria-invalid', 'true');
  await expect(password).toHaveAttribute('aria-describedby', 'password-error');
  await expect(page.locator('#password-error')).toHaveAttribute('role', 'alert');
  await expect(page.getByLabel('密码', { exact: true })).toBeVisible();
});

test('register mismatch and terms errors are associated with their controls', async ({ page }) => {
  await page.goto('/register');
  await page.getByLabel('用户名').fill('error_user');
  await page.getByLabel('真实姓名').fill('错误测试');
  await page.getByLabel('密码', { exact: true }).fill('password-one');
  await page.getByLabel('确认密码').fill('password-two');
  await page.getByRole('button', { name: '创建账号' }).click();

  const confirmation = page.getByLabel('确认密码');
  await expect(page.getByLabel('密码', { exact: true })).toBeVisible();
  const terms = page.getByRole('checkbox', { name: /我已阅读并同意/ });
  await expect(confirmation).toHaveAttribute('aria-invalid', 'true');
  await expect(confirmation).toHaveAttribute('aria-describedby', 'confirm_password-error');
  await expect(page.locator('#confirm_password-error')).toHaveAttribute('role', 'alert');
  await expect(terms).toHaveAttribute('aria-invalid', 'true');
  await expect(terms).toHaveAttribute('aria-describedby', 'register-accepted-error');
  await expect(page.locator('#register-accepted-error')).toHaveAttribute('role', 'alert');
});

test('registration keeps the form and retries a failed terms request in place', async ({
  page,
}) => {
  let attempts = 0;
  await page.unroute('**/api/legal/platform-terms/current');
  await page.route('**/api/legal/platform-terms/current', async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({ status: 503, json: { error: { message: '条款暂时不可用' } } });
      return;
    }
    await route.fulfill({
      json: { version: '2026-08-01', title: '平台条款', content: '测试条款' },
    });
  });
  await page.goto('/register');
  await page.getByLabel('用户名').fill('keep_user');
  await page.getByLabel('真实姓名').fill('保留填写');
  const termsError = page.getByTestId('register-terms-error');
  await expect(termsError).toContainText('平台条款暂时无法加载');
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '创建账号' })).toBeDisabled();
  await page.getByRole('button', { name: '重新加载条款' }).click();
  await expect(termsError).toHaveCount(0);
  await expect(page.getByLabel('用户名')).toHaveValue('keep_user');
  await expect(page.getByLabel('真实姓名')).toHaveValue('保留填写');
  await expect(page.getByRole('button', { name: '创建账号' })).toBeEnabled();
  expect(attempts).toBe(2);
});
