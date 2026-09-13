import { expect, test } from './fixtures';

const batchId = '40000000-0000-4000-8000-000000000001';
const ruleId = '41000000-0000-4000-8000-000000000001';
const matchId = '41500000-0000-4000-8000-000000000001';
const userIds = Array.from(
  { length: 21 },
  (_, index) => `42000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
);
const agentIds = Array.from(
  { length: 6 },
  (_, index) => `43000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
);
const topicIds = Array.from(
  { length: 7 },
  (_, index) => `44000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
);

function batch(status = 'DRAFT') {
  return {
    id: batchId,
    code: 'CHI27_A',
    title: '论文正式实验',
    status,
    schedule_version: status === 'DRAFT' ? 0 : 1,
    rule_id: ruleId,
    format_version_id: null,
    training_room_quota: 3,
    created_by: '00000000-0000-4000-8000-000000000001',
    created_at: '2026-08-21T00:00:00Z',
    updated_at: '2026-08-21T00:00:00Z',
    published_at: null,
    disabled_at: null,
  };
}

function scheduleMatches() {
  return Array.from({ length: 21 }, (_, index) => ({
    id: `45000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
    round_no: Math.floor(index / 3) + 1,
    match_no: (index % 3) + 1,
    topic_id: topicIds[Math.min(Math.floor(index / 3), 6)],
    affirmative_team_id: `46000000-0000-4000-8000-${String((index % 6) + 1).padStart(12, '0')}`,
    negative_team_id: `46000000-0000-4000-8000-${String(((index + 1) % 6) + 1).padStart(12, '0')}`,
    kind: index < 18 ? 'FORMAL' : 'TRAINING',
    status: 'DRAFT',
    schedule_version: 1,
    seats: [],
  }));
}

test('admin completes the v2.1 experiment setup workflow', async ({ page }) => {
  let generated = false;
  let rosterSaved = false;
  let published = false;
  let disabled = false;
  let generationPayload: Record<string, unknown> | undefined;
  let importedCsv = '';

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
  await page.route('**/api/experiments/capabilities', (route) =>
    route.fulfill({
      json: { creation_enabled: true, history_readable: true, target_version: '2.1.0' },
    }),
  );
  await page.route('**/api/admin/experiments/batches', (route) =>
    route.fulfill({
      json: [batch(disabled ? 'DISABLED' : published ? 'PUBLISHED' : 'DRAFT')],
    }),
  );
  await page.route(`**/api/admin/experiments/batches/${batchId}/progress`, (route) =>
    route.fulfill({
      json: {
        batch_id: batchId,
        formal_total: 18,
        formal_completed: 0,
        matches: published
          ? [
              {
                scheduled_match_id: scheduleMatches()[0].id,
                round_no: 1,
                match_no: 1,
                kind: 'FORMAL',
                topic_title: '过程还是结果更能体现奋斗的价值',
                affirmative_team_code: 'T01',
                negative_team_code: 'T02',
                match_status: 'RUNNING',
                attempt_id: '41500000-0000-4000-8000-000000000002',
                attempt_no: 1,
                attempt_status: 'RUNNING',
                match_id: matchId,
                completed_annotations: 2,
                total_annotations: 6,
                public_at: null,
              },
            ]
          : [],
      },
    }),
  );
  await page.route('**/api/admin/catalog', (route) =>
    route.fulfill({
      json: {
        models: [],
        voices: [],
        rules: [
          {
            id: ruleId,
            name: '论文实验三阶段赛制',
            status: 'ENABLED',
            side_size: 4,
            audio_reviewed_at: '2026-08-21T00:00:00Z',
          },
        ],
        agents: agentIds.map((id, index) => ({
          id,
          name: `固定 Agent ${index + 1}`,
          status: 'ENABLED',
        })),
        topics: topicIds.map((id, index) => ({
          id,
          title: `实验辩题 ${index + 1}`,
          status: 'ENABLED',
        })),
      },
    }),
  );
  await page.route(`**/api/admin/rules/${ruleId}/agents`, (route) =>
    route.fulfill({
      json: agentIds.map((id, index) => ({
        id,
        name: `固定 Agent ${index + 1}`,
        status: 'ENABLED',
      })),
    }),
  );
  await page.route('**/api/admin/users*', (route) =>
    route.fulfill({
      json: {
        items: userIds.map((id, index) => {
          const code = index < 18 ? `P${String(index + 1).padStart(2, '0')}` : `E0${index - 17}`;
          return { id, username: code, real_name: `参与者 ${code}`, status: 'ACTIVE' };
        }),
        page: 1,
        page_size: 100,
        total: 21,
        total_pages: 1,
      },
    }),
  );
  await page.route('**/api/admin/experiments/accounts/generate', (route) =>
    route.fulfill({
      json: {
        accounts: userIds.map((user_id, index) => {
          const code = index < 18 ? `P${String(index + 1).padStart(2, '0')}` : `E0${index - 17}`;
          return {
            code,
            user_id,
            username: code,
            temporary_password: null,
            created: true,
          };
        }),
        created_count: 21,
        existing_count: 0,
        default_password: 'Jixia2026',
      },
    }),
  );
  await page.route(`**/api/admin/experiments/batches/${batchId}/roster`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        json: rosterSaved
          ? {
              teams: Array.from({ length: 6 }, (_, teamIndex) => ({
                team_code: `T${String(teamIndex + 1).padStart(2, '0')}`,
                agent_profile_id: agentIds[teamIndex],
                members: Array.from({ length: 3 }, (_, memberIndex) => {
                  const index = teamIndex * 3 + memberIndex;
                  return {
                    user_id: userIds[index],
                    participant_code: `P${String(index + 1).padStart(2, '0')}`,
                  };
                }),
              })),
              experts: Array.from({ length: 3 }, (_, index) => ({
                user_id: userIds[index + 18],
                expert_code: `E0${index + 1}`,
              })),
            }
          : { teams: [], experts: [] },
      });
      return;
    }
    rosterSaved = true;
    await route.fulfill({ json: { team_count: 6, participant_count: 18, expert_count: 3 } });
  });
  await page.route('**/api/admin/experiments/prompt-templates', (route) =>
    route.fulfill({
      json: {
        version: 'paper-experiment-v1',
        decision_prompt: '# 决策 Prompt\n{"should_speak": true|false}',
        speech_prompt: '# 发言 Prompt\n{{DEBATE_HISTORY}}',
      },
    }),
  );
  await page.route(`**/api/admin/experiments/batches/${batchId}/schedule`, (route) =>
    route.fulfill({
      json: {
        batch_id: batchId,
        batch_status: 'DRAFT',
        schedule_version: generated ? 1 : 0,
        matches: generated ? scheduleMatches() : [],
      },
    }),
  );
  await page.route(
    `**/api/admin/experiments/batches/${batchId}/schedule/generate`,
    async (route) => {
      generationPayload = (await route.request().postDataJSON()) as Record<string, unknown>;
      generated = true;
      await route.fulfill({
        json: {
          batch_id: batchId,
          batch_status: 'DRAFT',
          schedule_version: 1,
          matches: scheduleMatches(),
        },
      });
    },
  );
  await page.route(`**/api/admin/experiments/batches/${batchId}/schedule/import`, async (route) => {
    const payload = (await route.request().postDataJSON()) as { csv_text: string };
    importedCsv = payload.csv_text;
    await route.fulfill({
      json: {
        batch_id: batchId,
        schedule_version: 1,
        imported_match_count: 21,
        training_match_count: 3,
      },
    });
  });
  await page.route(`**/api/admin/experiments/batches/${batchId}/disable`, async (route) => {
    disabled = true;
    await route.fulfill({
      json: { id: batchId, status: 'DISABLED', disabled_at: '2026-08-21T12:00:00Z' },
    });
  });
  await page.route(`**/api/admin/experiments/batches/${batchId}/publish`, async (route) => {
    published = true;
    await route.fulfill({
      json: {
        batch_id: batchId,
        status: 'PUBLISHED',
        schedule_version: 1,
        formal_match_count: 18,
      },
    });
  });

  await page.goto('/admin/experiments');
  await expect(page.getByRole('heading', { name: '论文实验管理' })).toBeVisible();
  await expect(
    page.getByText(/研究同意|同意书|伦理批准|线下签署|已签署|待签署|签署同意/),
  ).toHaveCount(0);
  await page.getByRole('button', { name: '准备账号' }).click();
  await expect(page.getByText(/统一密码 Jixia2026/)).toBeVisible();
  await expect(page.getByRole('link', { name: '清晰排期表' })).toHaveAttribute(
    'href',
    `/api/admin/experiments/batches/${batchId}/schedule-readable.csv`,
  );
  await page.getByRole('button', { name: 'Agent 与 Prompt' }).click();
  await expect(page.getByRole('heading', { name: 'Agent 与 Prompt 设置' })).toBeVisible();
  await expect(page.getByText('决策 Prompt', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '打开系统日志' })).toHaveAttribute(
    'href',
    '/admin/logs',
  );
  await page.getByRole('button', { name: '关闭', exact: true }).click();

  const agentSelectors = page.getByLabel('固定 Agent');
  for (let index = 0; index < 6; index += 1) {
    await agentSelectors.nth(index).selectOption(agentIds[index]);
  }
  await page.getByRole('button', { name: '保存并继续' }).click();
  await expect(page.getByRole('button', { name: '辩题与排表' })).toBeVisible();

  for (let index = 0; index < 6; index += 1) {
    await page.getByLabel(`第 ${index + 1} 轮`).selectOption(topicIds[index]);
  }
  await page.getByLabel('训练赛独立辩题').selectOption(topicIds[6]);
  await page.getByRole('button', { name: '生成排表' }).click();
  await expect(page.getByText('排表已生成：18 场正式赛、3 场训练赛。')).toBeVisible();
  expect(generationPayload).toEqual({
    topic_ids: topicIds.slice(0, 6),
    training_topic_id: topicIds[6],
  });
  await expect(page.getByRole('link', { name: '导出机器 CSV' })).toHaveAttribute(
    'href',
    `/api/admin/experiments/batches/${batchId}/schedule.csv`,
  );

  await page.locator('input[type="file"]').setInputFiles({
    name: 'schedule.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('schedule_version,kind\n1,FORMAL\n'),
  });
  await expect(page.getByText('已导入 21 场比赛。')).toBeVisible();
  expect(importedCsv).toBe('schedule_version,kind\n1,FORMAL\n');

  await page.getByRole('button', { name: '发布批次' }).click();
  await expect(page.getByRole('heading', { name: '发布这个实验批次？' })).toBeVisible();
  await page.getByRole('button', { name: '确认发布' }).click();
  await expect(page.getByText('批次已发布，参与者现在可以看到预约。')).toBeVisible();
  await expect(page.getByText('过程还是结果更能体现奋斗的价值')).toBeVisible();
  await expect(page.getByText('T01')).toBeVisible();
  await expect(page.getByText('T02')).toBeVisible();
  await expect(page.getByText('第 1 次尝试 · 进行中')).toBeVisible();
  await expect(page.getByRole('link', { name: '数据与请求日志' })).toHaveAttribute(
    'href',
    `/admin/matches/${matchId}`,
  );
  await page.getByRole('button', { name: '停用批次' }).click();
  await expect(page.getByRole('heading', { name: '停用实验批次？' })).toBeVisible();
  await page.getByRole('button', { name: '确认停用' }).click();
  await expect(page.getByText('实验批次已停用，历史数据保持只读。')).toBeVisible();
  expect(disabled).toBe(true);
});

test('admin creates edits and deletes an experiment draft', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let batches: ReturnType<typeof batch>[] = [];
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
  await page.route('**/api/experiments/capabilities', (route) =>
    route.fulfill({
      json: { creation_enabled: true, history_readable: true, target_version: '2.1.0' },
    }),
  );
  await page.route('**/api/admin/catalog', (route) =>
    route.fulfill({
      json: {
        models: [],
        voices: [],
        agents: [],
        topics: [],
        rules: [
          {
            id: ruleId,
            name: '论文实验三阶段赛制',
            status: 'ENABLED',
            side_size: 4,
            audio_reviewed_at: '2026-08-21T00:00:00Z',
          },
        ],
      },
    }),
  );
  await page.route(`**/api/admin/rules/${ruleId}/agents`, (route) =>
    route.fulfill({
      json: agentIds.map((id, index) => ({
        id,
        name: `固定 Agent ${index + 1}`,
        status: 'ENABLED',
      })),
    }),
  );
  await page.route('**/api/admin/users*', (route) =>
    route.fulfill({ json: { items: [], page: 1, page_size: 100, total: 0, total_pages: 0 } }),
  );
  await page.route('**/api/admin/experiments/batches', async (route) => {
    if (route.request().method() === 'POST') {
      const payload = (await route.request().postDataJSON()) as {
        code: string;
        title: string;
        rule_id: string;
        training_room_quota: number;
      };
      batches = [
        {
          ...batch('DRAFT'),
          code: payload.code,
          title: payload.title,
          rule_id: payload.rule_id,
          format_version_id: null,
          training_room_quota: payload.training_room_quota,
        },
      ];
      await route.fulfill({ json: batches[0] });
      return;
    }
    await route.fulfill({ json: batches });
  });
  await page.route(`**/api/admin/experiments/batches/${batchId}`, async (route) => {
    if (route.request().method() === 'PATCH') {
      const payload = (await route.request().postDataJSON()) as {
        code: string;
        title: string;
        rule_id: string;
        training_room_quota: number;
      };
      batches = [{ ...batches[0], ...payload }];
      await route.fulfill({ json: batches[0] });
      return;
    }
    batches = [];
    await route.fulfill({ json: { id: batchId, status: 'DELETED' } });
  });
  await page.route(`**/api/admin/experiments/batches/${batchId}/roster`, (route) =>
    route.fulfill({ json: { teams: [], experts: [] } }),
  );
  await page.route(`**/api/admin/experiments/batches/${batchId}/schedule`, (route) =>
    route.fulfill({
      json: { batch_id: batchId, batch_status: 'DRAFT', schedule_version: 0, matches: [] },
    }),
  );

  await page.goto('/admin/experiments');
  await expect(page.getByText('暂无批次')).toBeVisible();
  await page.getByRole('button', { name: '新建批次' }).click();
  await page.getByLabel('批次编号').fill('paper_2026');
  await page.getByLabel('批次名称').fill('首轮实验');
  await page.getByLabel('实验赛制').selectOption(ruleId);
  await page.getByRole('button', { name: '创建草稿' }).click();
  await expect(page.getByRole('heading', { name: 'PAPER_2026 · 首轮实验' })).toBeVisible();

  await page.getByRole('button', { name: '编辑基本信息' }).click();
  await page.getByLabel('批次名称').fill('正式实验批次');
  await page.getByRole('button', { name: '保存修改' }).click();
  await expect(page.getByRole('heading', { name: 'PAPER_2026 · 正式实验批次' })).toBeVisible();

  await page.getByRole('button', { name: '删除草稿' }).click();
  await expect(page.getByRole('heading', { name: '删除这个实验草稿？' })).toBeVisible();
  await page.getByRole('button', { name: '删除草稿' }).last().click();
  await expect(page.getByText('暂无批次')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('participant enters a fixed room without an online research consent step', async ({
  page,
}) => {
  const scheduledMatchId = '47000000-0000-4000-8000-000000000001';
  const roomId = '48000000-0000-4000-8000-000000000001';
  let enterPayload: Record<string, unknown> | undefined;

  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: {
          id: userIds[0],
          username: 'P01',
          real_name: 'P01',
          role: 'USER',
          status: 'ACTIVE',
          must_change_password: false,
          default_avatar_key: 'human-01',
          avatar_version: 0,
          has_custom_avatar: false,
        },
      },
    }),
  );
  await page.route('**/api/experiments/appointments', (route) =>
    route.fulfill({
      json: [
        {
          scheduled_match_id: scheduledMatchId,
          batch_id: batchId,
          batch_title: '论文正式实验',
          round_no: 1,
          match_no: 1,
          topic_id: topicIds[0],
          scheduled_at: '2026-08-23T10:00:00Z',
          kind: 'FORMAL',
          status: 'SCHEDULED',
          side: 'AFFIRMATIVE',
          seat_no: 1,
          room_id: null,
          attempt_id: null,
        },
      ],
    }),
  );
  await page.route('**/api/experiments/annotation-tasks', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/experiments/expert-tasks', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/legal/platform-terms/current', (route) =>
    route.fulfill({
      json: { version: 'human-participation-v1', title: '参赛说明', body: '参赛说明正文' },
    }),
  );
  await page.route('**/api/legal/human-participation/current', (route) =>
    route.fulfill({
      json: { version: 'human-participation-v1', title: '参赛说明', body: '参赛说明正文' },
    }),
  );
  await page.route(`**/api/users/${userIds[0]}/avatar*`, (route) =>
    route.fulfill({ status: 404, json: { error: { code: 'avatar_not_found' } } }),
  );
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ status: 404, json: { error: { code: 'room_not_found' } } }),
  );
  await page.route(`**/api/experiments/appointments/${scheduledMatchId}/enter`, async (route) => {
    enterPayload = (await route.request().postDataJSON()) as Record<string, unknown>;
    await route.fulfill({
      json: {
        scheduled_match_id: scheduledMatchId,
        attempt_id: '49000000-0000-4000-8000-000000000001',
        attempt_no: 1,
        room_id: roomId,
        room_code: '123456',
        member_role: 'DEBATER',
      },
    });
  });

  await page.goto('/experiments');
  await expect(page.getByRole('heading', { name: '我的实验安排' })).toBeVisible();
  await expect(
    page.getByText(/研究同意|同意书|伦理批准|线下签署|已签署|待签署|签署同意/),
  ).toHaveCount(0);
  await page.getByRole('button', { name: '进入固定房间' }).click();
  await expect(page).toHaveURL(`/rooms/${roomId}`);
  expect(enterPayload).toEqual({ human_participation_terms_version: 'human-participation-v1' });
});
