import { expect, test } from './fixtures';

test('admin saves normalized topic provenance from the topic drawer', async ({ page }) => {
  let submittedPayload: Record<string, unknown> | undefined;

  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: {
          id: '00000000-0000-4000-8000-000000000001',
          username: 'admin',
          real_name: '系统管理员',
          role: 'ADMIN',
          status: 'ACTIVE',
          must_change_password: false,
          default_avatar_key: 'human-01',
          avatar_version: 0,
          has_custom_avatar: false,
        },
      },
    }),
  );
  await page.route('**/api/admin/catalog', (route) =>
    route.fulfill({ json: { models: [], voices: [], rules: [], agents: [], topics: [] } }),
  );
  await page.route('**/api/admin/catalog/topics', async (route) => {
    submittedPayload = (await route.request().postDataJSON()) as Record<string, unknown>;
    await route.fulfill({
      json: {
        id: '44000000-0000-4000-8000-000000000001',
        topic_key: 'cedar-c1',
        version: 1,
        status: 'ENABLED',
        ...submittedPayload,
      },
    });
  });

  await page.goto('/admin/topics');
  await expect(page.getByRole('heading', { name: '辩题管理' })).toBeVisible();
  await page.getByRole('button', { name: '添加辩题' }).click();

  await page.getByLabel('辩题标题').fill('  过程还是结果更能体现奋斗的价值  ');
  await page.getByLabel('正方立场').fill('  过程比结果更能体现奋斗的价值  ');
  await page.getByLabel('反方立场').fill('  结果比过程更能体现奋斗的价值  ');
  await page.getByLabel('原始来源文本').fill('  2019华语辩论世界杯完整赛事视频_P56_59  ');
  await page.getByLabel('CEDAR ID（可选）').fill('  CEDAR-C1  ');
  await page.getByRole('button', { name: '保存辩题' }).click();

  await expect(page.getByText('辩题已创建。')).toBeVisible();
  expect(submittedPayload).toEqual({
    title: '过程还是结果更能体现奋斗的价值',
    affirmative_text: '过程比结果更能体现奋斗的价值',
    negative_text: '结果比过程更能体现奋斗的价值',
    source_text: '2019华语辩论世界杯完整赛事视频_P56_59',
    cedar_id: 'CEDAR-C1',
  });
});
