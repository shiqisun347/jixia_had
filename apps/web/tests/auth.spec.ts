import { expect, test } from '@playwright/test';

test('real registration, profile, logout, login and protected debate flow', async ({ page }) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const username = `e2e_${suffix}`.slice(0, 32);
  const password = 'Browser-E2E-Password-2026';

  await page.goto('/register');
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('真实姓名').fill('浏览器测试用户');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByLabel('确认密码').fill(password);
  await page.getByRole('radio', { name: '头像 16' }).click();
  await page.getByRole('checkbox', { name: /我已阅读并同意/ }).check();
  await page.getByRole('button', { name: '创建账号' }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText('浏览器测试用户')).toBeVisible();

  await page.goto('/profile');
  await expect(page.getByRole('heading', { name: '个人资料' })).toBeVisible();
  await page.getByLabel('真实姓名').fill('浏览器更新用户');
  await page.getByRole('button', { name: '保存姓名' }).click();
  await expect(page.getByRole('status')).toHaveText('资料已保存。');

  await page.getByRole('main').getByRole('button', { name: '退出登录' }).click();
  const logoutDialog = page.getByRole('alertdialog', { name: '确认退出登录？' });
  await expect(logoutDialog).toBeVisible();
  await logoutDialog.getByRole('button', { name: '取消' }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await page.getByRole('main').getByRole('button', { name: '退出登录' }).click();
  await page
    .getByRole('alertdialog', { name: '确认退出登录？' })
    .getByRole('button', { name: '退出登录' })
    .click();
  await expect(page).toHaveURL(/\/$/);

  await page.goto('/debate');
  await expect(page).toHaveURL(/\/login\?return_to=%2Fdebate/);

  await page.getByLabel('用户名').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await page.getByRole('button', { name: '登录并进入' }).click();
  await expect(page).toHaveURL(/\/debate$/);
  await expect(page.getByRole('heading', { name: '请选择一场比赛' })).toBeVisible();
  await expect(page.getByRole('link', { name: '返回公开大厅' })).toHaveAttribute('href', '/lobby');
});
