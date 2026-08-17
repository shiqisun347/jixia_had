import { expect, test } from '@playwright/test';

const baseUser = {
  id: '00000000-0000-0000-0000-000000000054',
  username: 'header_user',
  real_name: '林知夏',
  role: 'USER',
  must_change_password: false,
  avatar_version: 0,
  default_avatar_key: 'human-01',
  has_custom_avatar: false,
};

async function mockSession(page: import('@playwright/test').Page, role: 'USER' | 'ADMIN') {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: {
          ...baseUser,
          role,
          real_name: role === 'ADMIN' ? '平台管理员' : baseUser.real_name,
        },
      },
    }),
  );
  await page.route('**/api/users/*/avatar**', (route) => route.fulfill({ status: 204 }));
}

test('regular user account control links directly to the personal page', async ({ page }) => {
  await mockSession(page, 'USER');
  await page.goto('/guide');

  const account = page.getByRole('link', { name: '进入林知夏的个人页面' });
  await expect(account).toBeVisible();
  await expect(account).toHaveAttribute('href', '/me');
  await expect(page.getByRole('link', { name: '管理后台' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '修改密码' })).toHaveCount(0);

  await page.getByRole('button', { name: '退出登录' }).click();
  await expect(page.getByRole('alertdialog', { name: '确认退出登录？' })).toBeVisible();
});

test('administrator account control exposes only user and admin destinations', async ({ page }) => {
  await mockSession(page, 'ADMIN');
  await page.goto('/guide');

  const account = page.getByRole('button', { name: '打开平台管理员的账号菜单' });
  await expect(account).toBeVisible();
  await account.click();
  await expect(page.getByRole('link', { name: '我的页面' })).toHaveAttribute('href', '/me');
  await expect(page.getByRole('link', { name: '管理后台' })).toHaveAttribute('href', '/admin');
  await expect(page.getByRole('link', { name: '修改密码' })).toHaveCount(0);
});
