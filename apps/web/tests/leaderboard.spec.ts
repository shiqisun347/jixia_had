import { expect, test } from '@playwright/test';

const rankings = {
  generated_at: '2026-08-12T08:00:00Z',
  human: [
    {
      rank: 1,
      participant_id: 'human-1',
      display_name: '林知夏',
      points: 98,
      matches: 8,
      wins: 7,
      average_personal_score: 9.2,
      avatar_key: 'human-01',
    },
    {
      rank: 2,
      participant_id: 'human-2',
      display_name: '陈述安',
      points: 91,
      matches: 7,
      wins: 6,
      average_personal_score: 8.8,
      avatar_key: 'human-02',
    },
    {
      rank: 3,
      participant_id: 'human-3',
      display_name: '周砚',
      points: 84,
      matches: 6,
      wins: 5,
      average_personal_score: 8.3,
      avatar_key: 'human-03',
    },
  ],
  agent: [
    {
      rank: 1,
      participant_id: 'agent-1',
      display_name: '龙安灵希',
      points: 101,
      matches: 9,
      wins: 8,
      average_personal_score: 9.4,
      avatar_key: 'agent-01',
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );
  await page.route('**/api/leaderboards', (route) => route.fulfill({ json: rankings }));
});

test('leaderboard page switches type and filters the current snapshot', async ({ page }) => {
  await page.goto('/leaderboard');
  await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(1);
  await expect(page.getByRole('link', { name: '返回首页' })).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '辩手排行榜' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '排名' })).toBeVisible();
  await expect(page.getByText('林知夏')).toBeVisible();
  await expect(page.locator('img[src="/assets/avatars/human-01.webp"]')).toBeVisible();
  await page.getByPlaceholder('搜索姓名或编号').fill('陈述');
  await expect(page.getByText('陈述安')).toBeVisible();
  await expect(page.getByText('林知夏')).toHaveCount(0);
  await page.getByRole('tab', { name: /Agent 辩手/ }).click();
  await expect(page.getByText('龙安灵希')).toBeVisible();
  await expect(page.locator('img[src="/assets/avatars/agent-01.webp"]')).toBeVisible();
});

test('leaderboard distinguishes loading from an empty snapshot', async ({ page }) => {
  let resolve: (() => void) | undefined;
  const pending = new Promise<void>((done) => {
    resolve = done;
  });
  await page.unroute('**/api/leaderboards');
  await page.route('**/api/leaderboards', async (route) => {
    await pending;
    await route.fulfill({ json: { generated_at: null, human: [], agent: [] } });
  });
  await page.goto('/leaderboard');
  await expect(page.getByRole('status', { name: '正在加载排行榜' })).toHaveText('正在加载排行榜…');
  await expect(page.getByText('暂无匹配的排名')).toHaveCount(0);
  resolve?.();
  await expect(page.getByText('当前暂无可展示的排名')).toBeVisible();
  await expect(page.getByText('每日排名快照生成后会显示在这里。')).toBeVisible();
});

test('leaderboard recovers from a temporary failure without reloading the page', async ({
  page,
}) => {
  let requests = 0;
  let releaseRetry: (() => void) | undefined;
  const retryResponse = new Promise<void>((done) => {
    releaseRetry = done;
  });
  await page.unroute('**/api/leaderboards');
  await page.route('**/api/leaderboards', async (route) => {
    requests += 1;
    if (requests === 1) {
      return route.fulfill({
        status: 503,
        json: { error: { code: 'temporary_failure', message: '排行榜暂时不可用' } },
      });
    }
    await retryResponse;
    return route.fulfill({ json: { generated_at: null, human: [], agent: [] } });
  });

  await page.goto('/leaderboard');
  await expect(page.getByText('排行榜暂时无法加载。')).toBeVisible();
  const retry = page.getByRole('button', { name: '重新加载' });
  await expect(retry).toHaveClass(/bg-blue-600/);
  await expect(retry).toHaveClass(/text-white/);
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByRole('status', { name: '正在加载排行榜' })).toBeVisible();
  releaseRetry?.();
  await expect(page.getByText('当前暂无可展示的排名')).toBeVisible();
  expect(requests).toBe(2);
  await expect(page).toHaveURL(/\/leaderboard$/);
});

test('home exposes podium and leaderboard navigation', async ({ page }) => {
  await page.route('**/api/lobby/rooms', (route) => route.fulfill({ json: [] }));
  await page.goto('/');
  await expect(page.getByRole('link', { name: '排行榜' })).toHaveAttribute('href', '/leaderboard');
  await expect(page.getByTestId('human-podium').getByLabel('第 1 名')).toBeVisible();
  await expect(page.getByTestId('human-podium').getByText('林知夏')).toBeVisible();
  await expect(page.getByTestId('agent-podium').getByText('龙安灵希')).toBeVisible();
  await expect(page.getByRole('link', { name: /查看完整榜单/ }).first()).toBeVisible();
  await expect(page.getByText('我的页面')).toHaveCount(0);
});
