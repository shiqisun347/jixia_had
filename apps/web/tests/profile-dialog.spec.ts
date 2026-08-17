import { expect, test } from '@playwright/test';

const initialUser = {
  id: '10000000-0000-4000-8000-000000000046',
  username: 'profile_dialog_user',
  real_name: '资料测试用户',
  role: 'ADMIN',
  status: 'ACTIVE',
  must_change_password: false,
  avatar_version: 0,
  default_avatar_key: 'human-03',
  has_custom_avatar: true,
};

test('logged-out legacy profile link keeps its return target', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );

  await page.goto('/profile');

  await expect(page).toHaveURL('/login?return_to=%2Fprofile');
});

test('legacy profile opens the single profile dialog and updates user data', async ({ page }) => {
  let user = { ...initialUser };
  let profileWrites = 0;
  let avatarWrites = 0;
  let passwordWrites = 0;
  let avatarDeleteWrites = 0;
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route('**/api/users/me/summary', (route) =>
    route.fulfill({
      json: {
        current_match: null,
        matches: 2,
        finished_matches: 2,
        wins: 1,
        average_score: 82.5,
        leaderboard_rank: 6,
        recent_matches: [],
        latest_device_check: null,
      },
    }),
  );
  await page.route('**/api/users/*/avatar*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      status: 302,
      headers: { location: '/assets/avatars/human-03.webp' },
    });
  });
  await page.route('**/api/users/me', async (route) => {
    profileWrites += 1;
    const body = route.request().postDataJSON() as { real_name: string };
    user = { ...user, real_name: body.real_name };
    await route.fulfill({ json: { user } });
  });
  await page.route('**/api/users/me/avatar-preset', async (route) => {
    avatarWrites += 1;
    const body = route.request().postDataJSON() as { avatar_key: string };
    user = {
      ...user,
      avatar_version: user.avatar_version + 1,
      default_avatar_key: body.avatar_key,
    };
    await route.fulfill({ json: { user } });
  });
  await page.route('**/api/auth/change-password', async (route) => {
    passwordWrites += 1;
    const body = route.request().postDataJSON() as {
      current_password: string;
      new_password: string;
    };
    expect(body).toEqual({ current_password: 'old-password', new_password: 'new-password' });
    await route.fulfill({ json: { user } });
  });
  await page.route('**/api/users/me/avatar', async (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    avatarDeleteWrites += 1;
    if (avatarDeleteWrites === 1) {
      await route.fulfill({
        status: 503,
        json: { error: { code: 'avatar_unavailable', message: '头像服务暂时不可用' } },
      });
      return;
    }
    user = { ...user, avatar_version: user.avatar_version + 1, has_custom_avatar: false };
    await route.fulfill({ json: { user } });
  });

  await page.goto('/profile');

  await expect(page).toHaveURL('/me?edit=profile');
  const dialog = page.getByRole('dialog', { name: '编辑资料与头像' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('用户端身份：辩手')).toBeVisible();
  await expect(page.getByText('@profile_dialog_user · 辩手')).toBeVisible();
  await expect(page.getByText('@profile_dialog_user · 管理员')).toHaveCount(0);
  await expect
    .poll(async () => {
      const source = await page.locator('header img[alt="稷下"]').getAttribute('src');
      if (!source) return null;
      return new URL(source, page.url()).searchParams.get('url') ?? source;
    })
    .toBe('/assets/logo-ui.webp');

  await dialog.getByLabel('真实姓名').fill('');
  await dialog.getByRole('button', { name: '保存姓名' }).click();
  const realNameInput = dialog.getByLabel('真实姓名');
  await expect(realNameInput).toHaveAttribute('aria-invalid', 'true');
  await expect(realNameInput).toHaveAttribute('aria-describedby', 'profile-real-name-error');
  await expect(dialog.locator('#profile-real-name-error')).toHaveAttribute('role', 'alert');
  await realNameInput.fill('资料更新用户');
  await dialog.getByRole('button', { name: '保存姓名' }).click();
  await expect(dialog.getByText('资料更新用户')).toBeVisible();
  expect(profileWrites).toBe(1);

  await expect(dialog.getByRole('button', { name: '头像 3', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await dialog.getByRole('button', { name: '头像 4', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '头像 4', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(dialog.getByRole('button', { name: /human-/ })).toHaveCount(0);
  await expect(dialog.locator('img[alt="资料更新用户的头像"]')).toHaveAttribute('src', /v=1/);
  expect(avatarWrites).toBe(1);

  await dialog.getByRole('button', { name: '修改密码' }).click();
  const passwordDialog = page.getByRole('dialog', { name: '修改密码' });
  await expect(passwordDialog).toBeVisible();
  await passwordDialog.getByRole('button', { name: '保存新密码' }).click();
  for (const [label, errorId] of [
    [/当前密码/, 'profile-current-password-error'],
    [/^新密码/, 'profile-new-password-error'],
    [/确认新密码/, 'profile-confirm-password-error'],
  ] as const) {
    const input = passwordDialog.getByLabel(label);
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(input).toHaveAttribute('aria-describedby', errorId);
    await expect(passwordDialog.locator(`#${errorId}`)).toHaveAttribute('role', 'alert');
  }
  expect(passwordWrites).toBe(0);
  await passwordDialog.getByLabel('当前密码').fill('old-password');
  await passwordDialog.getByLabel(/^新密码/).fill('new-password');
  await passwordDialog.getByLabel('确认新密码').fill('different-password');
  await passwordDialog.getByRole('button', { name: '保存新密码' }).click();
  await expect(passwordDialog.getByText('两次输入的新密码不一致。')).toBeVisible();
  const confirmPasswordInput = passwordDialog.getByLabel('确认新密码');
  await expect(confirmPasswordInput).toHaveAttribute('aria-invalid', 'true');
  await expect(confirmPasswordInput).toHaveAttribute(
    'aria-describedby',
    'profile-confirm-password-error',
  );
  await expect(passwordDialog.locator('#profile-confirm-password-error')).toHaveAttribute(
    'role',
    'alert',
  );
  expect(passwordWrites).toBe(0);
  await passwordDialog.getByLabel('确认新密码').fill('new-password');
  await passwordDialog.getByRole('button', { name: '保存新密码' }).click();
  await expect(passwordDialog).toHaveCount(0);
  await expect(dialog).toBeVisible();
  expect(passwordWrites).toBe(1);

  await dialog.getByRole('button', { name: '恢复默认头像' }).click();
  let restoreDialog = page.getByRole('alertdialog', { name: '恢复默认头像？' });
  await expect(restoreDialog).toBeVisible();
  expect(avatarDeleteWrites).toBe(0);
  await restoreDialog.getByRole('button', { name: '取消' }).click();
  await expect(restoreDialog).toHaveCount(0);
  expect(avatarDeleteWrites).toBe(0);

  await dialog.getByRole('button', { name: '恢复默认头像' }).click();
  restoreDialog = page.getByRole('alertdialog', { name: '恢复默认头像？' });
  await restoreDialog.getByRole('button', { name: '恢复默认头像' }).evaluate((button) => {
    if (!(button instanceof HTMLButtonElement)) throw new Error('确认控件不是按钮');
    button.click();
    button.click();
  });
  await expect(restoreDialog).toBeVisible();
  await expect(restoreDialog).toContainText('恢复失败，请检查网络后重试。');
  expect(avatarDeleteWrites).toBe(1);
  await restoreDialog.getByRole('button', { name: '恢复默认头像' }).click();
  await expect(restoreDialog).toHaveCount(0);
  await expect(dialog).toBeVisible();
  expect(avatarDeleteWrites).toBe(2);

  await dialog.getByRole('button', { name: '关闭资料编辑' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('main').getByRole('heading', { name: '默认头像' })).toHaveCount(0);
  await page.getByRole('button', { name: /编辑资料/ }).click();
  await expect(page.getByRole('dialog', { name: '编辑资料与头像' })).toBeVisible();
});
