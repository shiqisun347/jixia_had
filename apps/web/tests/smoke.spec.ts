import { expect, test, type Page } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/users/*/avatar*', (route) =>
    route.fulfill({ status: 302, headers: { location: '/assets/avatars/human-01.webp' } }),
  );
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );
  await page.route('**/api/lobby/rooms', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/leaderboards', (route) => route.fulfill({ json: [] }));
});

async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
    .toBe(true);
}

async function stubAuthenticatedUser(page: Page) {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user: {
          id: '00000000-0000-4000-8000-000000000001',
          username: 'ui-prototype',
          real_name: '原型测试用户',
          role: 'USER',
          must_change_password: false,
          avatar_version: 0,
        },
      }),
    });
  });
}

test('home prototype is usable at the target desktop viewport', async ({ page }) => {
  await page.goto('/');

  await expect(
    page.getByRole('heading', {
      name: /让人类与 Agent，\s*在声音中交锋与共创/,
    }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: '正在进行' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await expect(page.getByRole('button', { name: '重试登录状态' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '登录' })).toBeVisible();
  await expect(page.getByRole('link', { name: '注册' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '排行榜', exact: true })).toHaveCount(0);
  await expect(page.getByText('每日根据正常完赛评分更新')).toHaveCount(0);
  await expect
    .poll(() =>
      page.locator('#leaderboards').evaluate((element) => element.getBoundingClientRect().height),
    )
    .toBeLessThan(440);
  await expect
    .poll(() =>
      page
        .locator('#leaderboards > div > section')
        .first()
        .evaluate((element) => {
          const box = element.getBoundingClientRect();
          return { height: box.height, width: box.width };
        }),
    )
    .toMatchObject({ height: expect.any(Number), width: expect.any(Number) });
  expect(
    await page
      .locator('#leaderboards > div > section')
      .first()
      .evaluate((element) => element.getBoundingClientRect().height),
  ).toBeLessThanOrEqual(390);
  await expectNoHorizontalOverflow(page);

  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toBeVisible();
});

test('global header keeps horizontal readable navigation in a narrow viewport', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');

  const header = page.locator('.site-header');
  const navigation = page.getByRole('navigation', { name: '主导航' });
  const brand = page.getByRole('link', { name: '返回首页' });
  const account = page.getByRole('link', { name: '登录', exact: true });
  await expect(header).toBeVisible();
  await expect(navigation).toBeVisible();
  await expect(brand).toBeVisible();
  await expect(account).toBeVisible();

  const layout = await page.evaluate(() => {
    const nav = document.querySelector<HTMLElement>('.site-header__nav');
    const brand = document.querySelector<HTMLElement>('.site-header__brand');
    const account = document.querySelector<HTMLElement>('.site-header__account');
    const links = [...document.querySelectorAll<HTMLElement>('.site-header__link')];
    if (!nav || !brand || !account || links.length !== 4) return null;
    const navBox = nav.getBoundingClientRect();
    const brandBox = brand.getBoundingClientRect();
    const accountBox = account.getBoundingClientRect();
    return {
      navTop: navBox.top,
      firstRowBottom: Math.max(brandBox.bottom, accountBox.bottom),
      linkHeights: links.map((link) => link.getBoundingClientRect().height),
      linkWidths: links.map((link) => link.getBoundingClientRect().width),
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    };
  });
  expect(layout).not.toBeNull();
  expect(layout?.navTop).toBeGreaterThanOrEqual(layout?.firstRowBottom ?? 0);
  expect(layout?.linkHeights.every((height) => height < 50)).toBe(true);
  expect(layout?.linkWidths.every((width) => width > 70)).toBe(true);
  expect(layout?.scrollWidth).toBe(layout?.innerWidth);

  await page.getByRole('link', { name: '比赛大厅' }).focus();
  await expect(page.getByRole('link', { name: '比赛大厅' })).toBeFocused();
});

test('auth shell follows the real two-row mobile header height', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/login');
  await expect(page.locator('.site-header')).toBeVisible();
  await expect(page.locator('main')).toBeVisible();
  const metrics = await page.evaluate(() => ({
    headerBottom: document.querySelector('.site-header')?.getBoundingClientRect().bottom ?? 0,
    mainTop: document.querySelector('main')?.getBoundingClientRect().top ?? 0,
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
  }));
  expect(metrics.mainTop).toBe(metrics.headerBottom);
  expect(metrics.documentHeight).toBeLessThanOrEqual(metrics.viewportHeight + 2);
});

test('page toasts stay below the two-row narrow header', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/login?reason=session_expired');
  const header = page.locator('.site-header');
  const viewport = page.locator('[data-toast-placement="page"]');
  await expect(header).toBeVisible();
  const boxes = await page.evaluate(() => {
    const header = document.querySelector('.site-header')?.getBoundingClientRect();
    const toast = document.querySelector('[data-toast-placement="page"]')?.getBoundingClientRect();
    return { headerBottom: header?.bottom ?? 0, toastTop: toast?.top ?? 0 };
  });
  expect(boxes.toastTop).toBeGreaterThanOrEqual(boxes.headerBottom + 12);
  await expect(viewport.getByRole('alert')).toContainText('登录状态已失效');
});

test('home never flashes prototype matches while runtime data is loading', async ({ page }) => {
  await stubAuthenticatedUser(page);
  let releaseRooms: () => void = () => undefined;
  let releaseRankings: () => void = () => undefined;
  await page.route('**/api/lobby/rooms', async (route) => {
    await new Promise<void>((resolve) => {
      releaseRooms = resolve;
    });
    await route.fulfill({ json: [] });
  });
  await page.route('**/api/leaderboards', async (route) => {
    await new Promise<void>((resolve) => {
      releaseRankings = resolve;
    });
    await route.fulfill({ json: { generated_at: null, human: [], agent: [] } });
  });

  await page.goto('/');
  await expect(page.getByRole('status').filter({ hasText: '正在同步比赛信息' })).toBeVisible();
  await expect(page.getByRole('link', { name: /查看比赛：/ })).toHaveCount(0);
  await expect(page.getByText('公平与效率之辩')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '目前没有进行中的比赛' })).toHaveCount(0);

  releaseRooms();
  releaseRankings();
  await expect(page.getByRole('heading', { name: '目前没有进行中的比赛' })).toBeVisible();
});

test('debate route without a match id returns the user to the public lobby', async ({ page }) => {
  await stubAuthenticatedUser(page);
  await page.goto('/debate');

  await expect(page.getByRole('heading', { name: '请选择一场比赛' })).toBeVisible();
  await expect(page.getByText('当前链接没有比赛编号')).toBeVisible();
  await expect(page.getByRole('link', { name: '返回公开大厅' })).toHaveAttribute('href', '/lobby');
  await expectNoHorizontalOverflow(page);
});
