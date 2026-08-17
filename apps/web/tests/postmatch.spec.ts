import { expect, test, type Page } from '@playwright/test';

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  username: 'postmatch_user',
  real_name: '赛后验收用户',
  role: 'USER',
  must_change_password: false,
  avatar_version: 0,
};
const matchId = '70000000-0000-4000-8000-000000000001';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/users/*/avatar*', (route) =>
    route.fulfill({ status: 302, headers: { location: '/assets/avatars/human-01.webp' } }),
  );
});

async function installPostmatchRoutes(page: Page) {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) =>
    route.fulfill({
      json: {
        match_id: matchId,
        status: 'FINISHED',
        title: '完整比赛验收',
        label: '训练赛',
        display_topic: '效率与公平何者更值得优先考虑',
        admin_note: null,
        context_version: 3,
        speeches: [],
        participants: [],
        submissions: [],
        files: [],
        judge: null,
      },
    }),
  );
}

function postmatchWithSpeech() {
  return {
    match_id: matchId,
    status: 'FINISHED',
    title: '文字审阅验收',
    label: '训练赛',
    display_topic: '测试提交保护',
    admin_note: null,
    context_version: 3,
    speeches: [
      {
        id: 'speech-1',
        speaker_kind: 'HUMAN',
        side: 'AFFIRMATIVE',
        seat_no: 1,
        display_text: '原始正式文字',
        asr_raw_final_text: '原始正式文字',
        user_id: user.id,
      },
    ],
    participants: [],
    submissions: [],
    files: [],
    judge: null,
  };
}

test('postmatch review keeps a clear route back to the lobby', async ({ page }) => {
  await installPostmatchRoutes(page);
  await page.goto(`/matches/${matchId}`);

  await expect(page.getByRole('heading', { name: '完整比赛验收' })).toBeVisible();
  await expect(page.getByRole('link', { name: '返回大厅' })).toHaveAttribute('href', '/lobby');
  await expect(page.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/');
});

test('postmatch load failure can be retried without leaving the page', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  let requests = 0;
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) => {
    requests += 1;
    if (requests === 1) {
      return route.fulfill({
        status: 503,
        json: { error: { code: 'temporary_failure', message: '赛后服务暂时不可用' } },
      });
    }
    return route.fulfill({
      json: {
        match_id: matchId,
        status: 'FINISHED',
        title: '重试后的赛后记录',
        label: '训练赛',
        display_topic: '测试重试',
        admin_note: null,
        context_version: 1,
        speeches: [],
        participants: [],
        submissions: [],
        files: [],
        judge: null,
      },
    });
  });
  await page.goto(`/matches/${matchId}`);
  await expect(page.getByText('赛后记录暂时无法加载，请稍后重试。')).toBeVisible();
  await page.getByRole('button', { name: '重新加载' }).click();
  await expect(page.getByRole('heading', { name: '重试后的赛后记录' })).toBeVisible();
  expect(requests).toBe(2);
});

test('postmatch blocks submission while a speech edit is unsaved', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  let submitRequests = 0;
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) =>
    route.fulfill({ json: postmatchWithSpeech() }),
  );
  await page.route(`**/api/matches/${matchId}/transcripts/submit`, (route) => {
    submitRequests += 1;
    return route.fulfill({ json: postmatchWithSpeech() });
  });

  await page.goto(`/matches/${matchId}`);
  await page.getByRole('button', { name: '修改我的文字' }).click();
  await expect(page.getByRole('button', { name: '请先保存或取消编辑' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认并提交我的文字' })).toHaveCount(0);
  expect(submitRequests).toBe(0);

  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认并提交我的文字' })).toBeEnabled();
});

test('postmatch enables submission after the edited speech is saved', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  let submitted = false;
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) =>
    route.fulfill({ json: postmatchWithSpeech() }),
  );
  await page.route(`**/api/matches/${matchId}/speeches/speech-1/display-text`, (route) =>
    route.fulfill({
      json: {
        match_id: matchId,
        context_version: 4,
        speeches: [],
      },
    }),
  );
  await page.route(`**/api/matches/${matchId}/transcripts/submit`, (route) => {
    submitted = true;
    return route.fulfill({
      json: {
        ...postmatchWithSpeech(),
        context_version: 4,
        submissions: [{ user_id: user.id, submitted_at: '2026-08-14T02:30:00Z' }],
      },
    });
  });

  await page.goto(`/matches/${matchId}`);
  await page.getByRole('button', { name: '修改我的文字' }).click();
  await page.getByRole('textbox', { name: '修改本人发言文字' }).fill('修改后的正式文字');
  await page.getByRole('button', { name: '保存修改' }).click();
  await expect(page.getByText('修改后的正式文字')).toBeVisible();
  await page.getByRole('button', { name: '确认并提交我的文字' }).click();
  await expect(page.getByRole('button', { name: '我的文字已提交' })).toBeDisabled();
  expect(submitted).toBe(true);
});

test('postmatch refreshes an in-flight judge result until it succeeds', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  let requests = 0;
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) => {
    requests += 1;
    return route.fulfill({
      json: {
        ...postmatchWithSpeech(),
        judge:
          requests === 1
            ? { status: 'RUNNING', result: null, error_code: null }
            : {
                status: 'SUCCEEDED',
                result: {
                  winner: 'AFFIRMATIVE',
                  team_scores: {
                    AFFIRMATIVE: {
                      argument: 30,
                      rebuttal: 25,
                      evidence: 20,
                      teamwork: 15,
                      expression: 10,
                    },
                    NEGATIVE: {
                      argument: 20,
                      rebuttal: 20,
                      evidence: 20,
                      teamwork: 20,
                      expression: 10,
                    },
                  },
                },
                error_code: null,
              },
        can_retry_judge: false,
      },
    });
  });
  await page.goto(`/matches/${matchId}`);
  await expect(page.getByText('AI 裁判正在评分…')).toBeVisible();
  await expect(page.getByText('正方获胜')).toBeVisible({ timeout: 5_000 });
  expect(requests).toBeGreaterThanOrEqual(2);
});

test('judge failure exposes one retry action only to authorized viewers', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) =>
    route.fulfill({
      json: {
        ...postmatchWithSpeech(),
        judge: { status: 'FAILED', result: null, error_code: 'judge_unavailable' },
        can_retry_judge: true,
      },
    }),
  );
  let retries = 0;
  await page.route(`**/api/matches/${matchId}/judge/retry`, (route) => {
    retries += 1;
    return route.fulfill({
      json: {
        ...postmatchWithSpeech(),
        judge: { status: 'RUNNING', result: null, error_code: null },
        can_retry_judge: false,
      },
    });
  });
  await page.goto(`/matches/${matchId}`);
  const retry = page.getByRole('button', { name: '重新评分' });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByText('AI 裁判正在评分…')).toBeVisible();
  expect(retries).toBe(1);
});

test('terminated postmatch does not expose or poll judge retry', async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  let requests = 0;
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) => {
    requests += 1;
    return route.fulfill({
      json: {
        ...postmatchWithSpeech(),
        status: 'TERMINATED',
        judge: null,
        can_retry_judge: false,
      },
    });
  });
  await page.goto(`/matches/${matchId}`);
  await expect(page.getByText('比赛已终止，不进行 AI 评分。')).toBeVisible();
  await expect(page.getByRole('button', { name: '重新评分' })).toHaveCount(0);
  await page.waitForTimeout(2_200);
  expect(requests).toBe(1);
});

test('completed judge result is prominent and does not overflow the viewport', async ({
  page,
}, testInfo) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route(`**/api/matches/${matchId}/postmatch`, (route) =>
    route.fulfill({
      json: {
        ...postmatchWithSpeech(),
        title: '人工智能能否提升公共决策质量',
        display_topic: '人工智能的广泛应用提升了还是降低了公共决策质量',
        judge: {
          status: 'SUCCEEDED',
          error_code: null,
          result: {
            winner: 'AFFIRMATIVE',
            team_scores: {
              AFFIRMATIVE: {
                argument: 28,
                rebuttal: 23,
                evidence: 18,
                teamwork: 14,
                expression: 9,
              },
              NEGATIVE: {
                argument: 26,
                rebuttal: 22,
                evidence: 17,
                teamwork: 13,
                expression: 9,
              },
            },
            team_comments: {
              AFFIRMATIVE: '论证结构完整，回应及时，能将技术效率与公共责任结合。',
              NEGATIVE: '风险意识充分，但部分论据之间的因果联系还可以进一步加强。',
            },
            participants: [],
          },
        },
        can_retry_judge: false,
      },
    }),
  );
  await page.goto(`/matches/${matchId}`);
  await expect(page.getByText('正方获胜')).toBeVisible();
  const geometry = await page.evaluate(() => ({
    viewport: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
  }));
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewport);
  if (testInfo.project.name === 'compact-1280x720') {
    await page.screenshot({ path: '/tmp/jixia-postmatch-123.png', fullPage: true });
  }
});
