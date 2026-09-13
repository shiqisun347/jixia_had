import { expect, test, type Page } from './fixtures';

const ruleId = '66000000-0000-4000-8000-000000000001';
const stageId = '77000000-0000-4000-8000-000000000001';
const freeStageId = '77000000-0000-4000-8000-000000000002';
const agentId = '88000000-0000-4000-8000-000000000001';

const rule = {
  id: ruleId,
  name: '论文实验规则',
  description: '三阶段 4v4 论文实验赛制',
  side_size: 4,
  estimated_seconds: 900,
  status: 'DISABLED',
  config_revision: 2,
  historical_read_only: false,
  topic_policy: 'BOTH',
};

const catalog = {
  models: [{ id: 'model-1', name: 'Qwen', status: 'ENABLED' }],
  voices: [
    {
      id: 'host-1',
      name: '主持人',
      kind: 'HOST',
      provider_voice: 'host',
      rate: 1,
      status: 'ENABLED',
    },
    {
      id: 'voice-1',
      name: '龙安灵希',
      kind: 'AGENT',
      provider_voice: 'agent',
      rate: 1,
      avatar_key: 'agent-01',
      status: 'ENABLED',
    },
  ],
  agents: [],
  topics: [],
  rules: [],
};

const workspace = {
  rule,
  agent_count: 8,
  stages: [
    {
      id: stageId,
      position: 1,
      name: '正方立论',
      stage_kind: 'FIXED_SPEECH',
      duration_seconds: 0,
      start_host_text: '',
      end_host_text: '',
      parameters: {},
      actions: [{ id: 'action-1', side: 'AFFIRMATIVE', seat_no: 1, duration_seconds: 90 }],
      prompts: [
        { purpose: 'SPEECH', template_text: '立论 Prompt {{TOPIC}}', variables: ['TOPIC'] },
      ],
    },
    {
      id: freeStageId,
      position: 2,
      name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      duration_seconds: 360,
      start_host_text: '',
      end_host_text: '',
      parameters: { max_speech_seconds: 30, starting_side: 'AFFIRMATIVE' },
      actions: [],
      prompts: [
        {
          purpose: 'SPEECH',
          template_text: '自由发言 {{DEBATE_HISTORY}}',
          variables: ['DEBATE_HISTORY'],
        },
        {
          purpose: 'DECISION',
          template_text: '自由决策 {{DEBATE_HISTORY}}',
          variables: ['DEBATE_HISTORY'],
        },
      ],
    },
  ],
  judge: {
    enabled: false,
    model_profile_id: null,
    judge_prompt: '',
    include_in_leaderboard: false,
  },
};

const agent = {
  id: agentId,
  rule_id: ruleId,
  name: '龙安灵希',
  model_profile_id: 'model-1',
  voice_profile_id: 'voice-1',
  generation_params: {},
  avatar_key: 'agent-01',
  status: 'ENABLED',
  prompt_override_count: 0,
};

async function mockAdmin(page: Page) {
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
  await page.route('**/api/admin/catalog', (route) => route.fulfill({ json: catalog }));
  await page.route('**/api/admin/rules', (route) => route.fulfill({ json: [rule] }));
  await page.route(`**/api/admin/rules/${ruleId}/workspace`, (route) =>
    route.fulfill({ json: workspace }),
  );
  await page.route(`**/api/admin/rules/${ruleId}/agents`, (route) =>
    route.fulfill({ json: [agent] }),
  );
  await page.route(
    `**/api/admin/rules/${ruleId}/agents/${agentId}/stages/${stageId}/prompts/SPEECH`,
    (route) =>
      route.fulfill({
        json: {
          uses_default: true,
          template_text: '立论 Prompt {{TOPIC}}',
          default_template_text: '立论 Prompt {{TOPIC}}',
        },
      }),
  );
  await page.route(`**/api/admin/rules/${ruleId}/stages/${freeStageId}/prompts/*`, (route) =>
    route.fulfill({ json: { id: 'prompt-1', variables: ['DEBATE_HISTORY'] } }),
  );
}

test('admin opens a rule workspace and edits one stage in a focused drawer', async ({ page }) => {
  await mockAdmin(page);
  await page.goto('/admin/rules');

  await expect(page.getByRole('heading', { name: '赛制规则' })).toBeVisible();
  await page.getByRole('link', { name: /打开工作区/ }).click();
  await expect(page).toHaveURL(`/admin/rules/${ruleId}`);
  await expect(page.getByRole('heading', { name: '论文实验规则' })).toBeVisible();

  await page.getByRole('button', { name: /正方立论/ }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer).toBeVisible();
  await expect(drawer.getByLabel('阶段名称')).toHaveValue('正方立论');
  await expect(drawer.getByLabel('阵营')).toHaveValue('AFFIRMATIVE');
  await expect(drawer.getByLabel('席位')).toHaveValue('1');
  await expect(drawer.locator('textarea').nth(2)).toHaveValue('立论 Prompt {{TOPIC}}');
});

test('admin saves both free-debate Prompt slots independently', async ({ page }) => {
  await mockAdmin(page);
  await page.goto(`/admin/rules/${ruleId}`);

  await page.getByRole('button', { name: /自由辩论/ }).click();
  let drawer = page.getByRole('dialog');
  const speechRequest = page.waitForRequest((request) =>
    request.url().endsWith(`/stages/${freeStageId}/prompts/SPEECH`),
  );
  await expect(drawer.locator('textarea').nth(2)).toHaveValue('自由发言 {{DEBATE_HISTORY}}');
  await drawer.locator('textarea').nth(2).fill('确认自由发言 {{DEBATE_HISTORY}}');
  await page.getByRole('button', { name: '保存 Prompt' }).click();
  expect((await speechRequest).postDataJSON()).toEqual({
    template_text: '确认自由发言 {{DEBATE_HISTORY}}',
  });
  await expect(page.getByText('阶段 Prompt 已保存')).toBeVisible();

  await page.getByRole('button', { name: /自由辩论/ }).click();
  drawer = page.getByRole('dialog');
  await drawer.getByLabel('Prompt 槽位').selectOption('DECISION');
  const decisionRequest = page.waitForRequest((request) =>
    request.url().endsWith(`/stages/${freeStageId}/prompts/DECISION`),
  );
  await expect(drawer.locator('textarea').nth(2)).toHaveValue('自由决策 {{DEBATE_HISTORY}}');
  await drawer.locator('textarea').nth(2).fill('确认自由决策 {{DEBATE_HISTORY}}');
  await page.getByRole('button', { name: '保存 Prompt' }).click();
  expect((await decisionRequest).postDataJSON()).toEqual({
    template_text: '确认自由决策 {{DEBATE_HISTORY}}',
  });
});

test('admin selects a rule before editing its private Agent pool', async ({ page }) => {
  await mockAdmin(page);
  await page.goto('/admin/agents');

  await expect(page.getByText('先选择一套赛制规则，再管理它的 Agent 池。')).toBeVisible();
  await expect(page.getByText('龙安灵希')).toHaveCount(0);
  await page.getByLabel('赛制规则').selectOption(ruleId);
  await expect(page.getByRole('cell', { name: '龙安灵希' }).first()).toBeVisible();

  await page.getByRole('button', { name: /编辑/ }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer).toContainText('编辑 Agent · 龙安灵希');
  await expect(drawer.getByLabel('模型')).toHaveValue('model-1');
  await expect(drawer.getByLabel('名称')).toHaveCount(0);
  await expect(drawer.getByLabel('音色')).toHaveCount(0);
  await expect(drawer.getByLabel('使用赛制默认 Prompt')).toBeChecked();
});

test('legacy format bookmarks redirect to the current rule workspace', async ({ page }) => {
  await mockAdmin(page);

  await page.goto('/admin/formats');
  await expect(page).toHaveURL('/admin/rules');
  await expect(page.getByRole('heading', { name: '赛制规则' })).toBeVisible();
  await expect(page.getByRole('link', { name: '赛制中心' })).toHaveCount(0);

  await page.goto('/admin/formats/legacy-version');
  await expect(page).toHaveURL('/admin/rules');
});
