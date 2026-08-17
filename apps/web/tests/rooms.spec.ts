import { expect, test, type Page } from '@playwright/test';

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  username: 'browser_room_user',
  real_name: '浏览器房间用户',
  role: 'USER',
  must_change_password: false,
  avatar_version: 0,
};
const roomId = '40000000-0000-4000-8000-000000000001';
const ruleId = '20000000-0000-4000-8000-000000000001';
const formal4v4RuleId = '20000000-0000-4000-8000-000000000004';
const topicId = '30000000-0000-4000-8000-000000000001';
const terms = { version: 'human-participation-v1', title: '参赛说明', body: '参赛说明正文' };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/users/*/avatar*', (route) =>
    route.fulfill({ status: 302, headers: { location: '/assets/avatars/human-01.webp' } }),
  );
  await page.route('**/api/rooms/*/seat-swap-requests', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] });
    return route.fallback();
  });
});
const rule = {
  id: ruleId,
  rule_key: 'classic',
  version: 1,
  name: '经典线性赛制',
  description: '测试赛制',
  side_size: 1,
  estimated_seconds: 900,
  status: 'ENABLED',
  audio_reviewed_at: '2026-08-03T08:00:00Z',
};
const formal4v4Rule = {
  ...rule,
  id: formal4v4RuleId,
  rule_key: 'formal-4v4',
  name: '4v4 正式辩论赛',
  side_size: 4,
};
const topic = {
  id: topicId,
  topic_key: 'topic',
  version: 1,
  title: '效率与公平何者更值得优先考虑',
  affirmative_text: '效率优先',
  negative_text: '公平优先',
  status: 'ENABLED',
};

function roomSnapshot(
  ready = false,
  deviceCheck: {
    check_version: number;
    status: string;
    checked_at: string;
    valid_until: string;
    is_valid: boolean;
  } | null = null,
) {
  return {
    id: roomId,
    code: 'JX8K2M',
    title: '浏览器人机辩论实验',
    label: '训练赛',
    status: 'WAITING',
    auto_fill_agents: true,
    organizer_user_id: user.id,
    is_all_agent: false,
    sequence: ready ? 4 : 3,
    topic: { title: topic.title },
    rule: { name: rule.name, side_size: 1 },
    match_id: null,
    viewer_membership_state: 'ACTIVE',
    viewer_member_role: 'DEBATER',
    viewer_ready: ready,
    latest_device_check: deviceCheck,
    members: [
      {
        user_id: user.id,
        member_role: 'DEBATER',
        online: true,
        ready,
        joined_at: '2026-08-03T08:00:00Z',
        real_name: user.real_name,
        default_avatar_key: 'human-01',
        avatar_version: 0,
        has_custom_avatar: false,
      },
    ],
    seats: [
      {
        id: '50000000-0000-4000-8000-000000000001',
        side: 'AFFIRMATIVE',
        seat_no: 1,
        occupant_type: 'HUMAN',
        user_id: user.id,
        agent_profile_id: null,
        occupant_name: user.real_name,
        occupant_avatar_key: 'human-01',
        occupant_avatar_version: 0,
        occupant_has_custom_avatar: false,
      },
      {
        id: '50000000-0000-4000-8000-000000000002',
        side: 'NEGATIVE',
        seat_no: 1,
        occupant_type: 'AGENT',
        user_id: null,
        agent_profile_id: '60000000-0000-4000-8000-000000000001',
        occupant_name: '乾元',
        occupant_avatar_key: 'agent-01',
      },
    ],
  };
}

async function installCommonRoutes(page: Page) {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route('**/api/legal/human-participation/current', (route) =>
    route.fulfill({ json: terms }),
  );
}

test('logged-out visitor is sent to login instead of seeing a false empty lobby', async ({
  page,
}) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );
  let lobbyRequests = 0;
  await page.route('**/api/lobby/rooms', (route) => {
    lobbyRequests += 1;
    return route.fulfill({ json: [] });
  });

  await page.goto('/lobby');
  await expect(page).toHaveURL('/login?return_to=%2Flobby');
  expect(lobbyRequests).toBe(0);
});

test('temporary auth failure keeps the lobby room-entry query through login', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 503,
      json: { error: { code: 'service_unavailable', message: '登录状态暂时不可用' } },
    }),
  );

  await page.goto('/lobby?join=1');
  await expect(page.getByRole('button', { name: '重试登录状态' })).toBeVisible();
  await page.getByRole('link', { name: '登录' }).click();
  await expect(page).toHaveURL('/login?return_to=%2Flobby%3Fjoin%3D1');
});

test('public lobby shows live server-shaped room data', async ({ page }) => {
  await installCommonRoutes(page);
  await page.route('**/api/lobby/rooms', (route) =>
    route.fulfill({
      json: [
        {
          id: roomId,
          code: 'JX8K2M',
          title: '浏览器人机辩论实验',
          label: '训练赛',
          status: 'WAITING',
          auto_fill_agents: true,
          topic_title: topic.title,
          rule_name: rule.name,
          side_size: 1,
          occupied_seats: 2,
          spectator_count: 0,
          spectator_remaining: 10,
          spectator_capacity_full: false,
          match_id: null,
          viewer_membership_state: 'NONE',
          viewer_member_role: null,
          viewer_ready: false,
        },
      ],
    }),
  );
  await page.goto('/lobby');
  await expect(page.getByRole('heading', { name: '公开大厅' })).toBeVisible();
  await expect(page.getByText(/5 秒同步/)).toHaveCount(0);
  await expect(page.getByText('输入邀请中的 6 位数字房间号。')).toBeVisible();
  await expect(page.getByText(/不区分大小写/)).toHaveCount(0);
  await expect(page.getByText('已同步')).toBeVisible();
  await expect(page.getByText('浏览器人机辩论实验')).toBeVisible();
  await expect(page.getByRole('link', { name: '进入房间' })).toHaveAttribute(
    'href',
    `/rooms/${roomId}`,
  );
});

test('room code input normalizes the code and opens the room', async ({ page }) => {
  await installCommonRoutes(page);
  await page.route('**/api/lobby/rooms', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/rooms/lookup?*', (route) => {
    expect(new URL(route.request().url()).searchParams.get('code')).toBe(' JX8K2M ');
    return route.fulfill({ json: { room_id: roomId, code: 'JX8K2M', status: 'WAITING' } });
  });
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );

  await page.goto('/lobby?join=1');
  await page.getByLabel('房间号').fill(' jx8k2m ');
  await page.getByRole('button', { name: '加入' }).click();
  await expect(page).toHaveURL(`/rooms/${roomId}`);
});

test('logged-out invite link preserves the room code through login', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );
  let lookupRequests = 0;
  await page.route('**/api/rooms/lookup?*', (route) => {
    lookupRequests += 1;
    return route.fulfill({ json: { room_id: roomId, code: 'JX8K2M', status: 'WAITING' } });
  });

  await page.goto('/join/jx8k2m');
  await expect(page).toHaveURL('/login?return_to=%2Fjoin%2Fjx8k2m');
  expect(lookupRequests).toBe(0);
});

test('room creation submits the selected rule, topic and automatic Agent fill', async ({
  page,
}) => {
  await installCommonRoutes(page);
  await page.route('**/api/lobby/catalog', (route) =>
    route.fulfill({
      json: {
        voices: [],
        models: [],
        agents: [],
        topics: [topic],
        rules: [rule, formal4v4Rule],
      },
    }),
  );
  let createRequests = 0;
  await page.route('**/api/rooms', async (route) => {
    createRequests += 1;
    const payload = route.request().postDataJSON();
    expect(payload).toMatchObject({
      title: '浏览器创建测试',
      rule_id: formal4v4RuleId,
      topic_id: topicId,
      human_participation_terms_version: 'human-participation-v1',
    });
    expect(payload).not.toHaveProperty('organizer_seat');
    expect(payload).not.toHaveProperty('fill_empty_with_agents');
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({ json: roomSnapshot() });
  });
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );
  await page.goto('/rooms/create');
  await expect(page.getByText('Agent 阵容与选席')).toHaveCount(0);
  await expect(page.getByText(/完整 Agent 阵容/)).toHaveCount(0);
  await expect(page.getByText('比赛信息', { exact: true })).toBeVisible();
  await expect(page.getByRole('group', { name: /2\s*选择辩题/ })).toBeVisible();
  await page.getByLabel('比赛名称').fill('浏览器创建测试');
  await expect(page.getByLabel('赛制')).toHaveValue(formal4v4RuleId);
  await expect(page.getByLabel('赛制').getByRole('option', { name: '选择已启用赛制' })).toHaveCount(
    0,
  );
  await page.getByRole('button', { name: '创建并进入房间' }).click();
  const topicSelect = page.getByLabel(/选择辩题/);
  await expect(topicSelect).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('#create-topic-error')).toHaveText('请选择辩题');
  expect(createRequests).toBe(0);
  await topicSelect.selectOption(topicId);
  await expect(page.locator('#create-topic-error')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '创建并进入房间' })).toBeVisible();
  await page.getByRole('button', { name: '创建并进入房间' }).evaluate((button) => {
    const form = button.closest('form');
    form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await expect(page).toHaveURL(`/rooms/${roomId}?created=1`);
  expect(createRequests).toBe(1);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await expect(page.getByRole('heading', { name: '浏览器人机辩论实验' })).toBeVisible();
  await expect(page.getByRole('region', { name: '邀请加入' })).toBeVisible();
  await expect(page.getByText(/Agent 席位恢复/)).toHaveCount(0);
  await expect(page.getByLabel('邀请链接')).toHaveValue(/\/join\/JX8K2M$/);
  await expect(page.getByRole('button', { name: '开始比赛' })).toBeDisabled();
  await expect(page.getByText('浏览器房间用户尚未完成设备检测与准备')).toBeVisible();
  await expect(page.getByRole('button', { name: '退出房间' })).toHaveCSS(
    'color',
    'rgb(201, 53, 67)',
  );
  await expect(page.getByRole('button', { name: '切换为观众' })).toHaveClass(/bg-blue-600/);
  await expect(page.getByRole('button', { name: '切换为观众' })).toHaveClass(/text-white/);
  await expect(
    page.getByRole('button', { name: '反方 1 辩，选择此席位' }).locator('span').last(),
  ).toHaveClass(/bg-blue-600/);
  const canScrollBeforeSync = await page.evaluate(
    () => document.documentElement.scrollHeight > window.innerHeight,
  );
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const userScrollPosition = await page.evaluate(() => window.scrollY);
  if (canScrollBeforeSync) expect(userScrollPosition).toBeGreaterThan(0);
  await page.waitForTimeout(1_600);
  expect(await page.evaluate(() => window.scrollY)).toBe(userScrollPosition);
});

test('room creation keeps the form and allows retry after a failed request', async ({ page }) => {
  await installCommonRoutes(page);
  await page.route('**/api/lobby/catalog', (route) =>
    route.fulfill({
      json: {
        voices: [],
        models: [],
        agents: [],
        topics: [topic],
        rules: [formal4v4Rule],
      },
    }),
  );
  let createRequests = 0;
  await page.route('**/api/rooms', async (route) => {
    createRequests += 1;
    if (createRequests === 1) {
      await route.fulfill({
        status: 503,
        json: { error: { code: 'room_create_unavailable', message: '房间创建服务暂时不可用' } },
      });
      return;
    }
    await route.fulfill({ json: roomSnapshot() });
  });
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );

  await page.goto('/rooms/create');
  await page.getByLabel('比赛名称').fill('失败后重试测试');
  await page.getByLabel('选择辩题').selectOption(topicId);
  await page.getByRole('button', { name: '创建并进入房间' }).click();
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toContainText(
    '房间创建服务暂时不可用',
  );
  await expect(page.getByLabel('比赛名称')).toHaveValue('失败后重试测试');
  await page.getByRole('button', { name: '创建并进入房间' }).click();
  await expect(page).toHaveURL(`/rooms/${roomId}?created=1`);
  expect(createRequests).toBe(2);
});

test('starting a room is single-flight across the lock and runtime requests', async ({ page }) => {
  await installCommonRoutes(page);
  const matchId = '90000000-0000-4000-8000-000000000001';
  const waiting = roomSnapshot(true);
  const starting = { ...waiting, status: 'START_PENDING_RUNTIME', sequence: 4 };
  let currentSnapshot = waiting;
  let startRequests = 0;
  let runtimeRequests = 0;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: currentSnapshot }),
  );
  await page.route(`**/api/rooms/${roomId}/start`, async (route) => {
    startRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 250));
    currentSnapshot = starting;
    await route.fulfill({ json: starting });
  });
  await page.route(`**/api/rooms/${roomId}/runtime-start`, async (route) => {
    runtimeRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.fulfill({ json: { match_id: matchId } });
  });

  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: '开始比赛' }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => startRequests).toBe(1);
  await expect.poll(() => runtimeRequests).toBe(1);
  await expect(page).toHaveURL(`/debate?match_id=${matchId}`);
});

test('a failed room lock releases start and allows the complete flow to retry', async ({
  page,
}) => {
  await installCommonRoutes(page);
  const matchId = '90000000-0000-4000-8000-000000000001';
  const waiting = roomSnapshot(true);
  const starting = { ...waiting, status: 'START_PENDING_RUNTIME', sequence: 4 };
  let currentSnapshot = waiting;
  let startRequests = 0;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: currentSnapshot }),
  );
  await page.route(`**/api/rooms/${roomId}/start`, async (route) => {
    startRequests += 1;
    if (startRequests === 1) {
      await route.fulfill({
        status: 503,
        json: { error: { code: 'room_start_failed', message: '暂时无法开始比赛' } },
      });
      return;
    }
    currentSnapshot = starting;
    await route.fulfill({ json: starting });
  });
  await page.route(`**/api/rooms/${roomId}/runtime-start`, (route) =>
    route.fulfill({ json: { match_id: matchId } }),
  );

  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: '开始比赛' }).click();
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toContainText(
    '暂时无法开始比赛',
  );
  await expect(page.getByRole('button', { name: '开始比赛' })).toBeEnabled();
  await page.getByRole('button', { name: '开始比赛' }).click();
  await expect(page).toHaveURL(`/debate?match_id=${matchId}`);
  expect(startRequests).toBe(2);
});

test('runtime start failure resumes without locking the room again', async ({ page }) => {
  await installCommonRoutes(page);
  const matchId = '90000000-0000-4000-8000-000000000001';
  const waiting = roomSnapshot(true);
  const starting = { ...waiting, status: 'START_PENDING_RUNTIME', sequence: 4 };
  let currentSnapshot = waiting;
  let startRequests = 0;
  let runtimeRequests = 0;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: currentSnapshot }),
  );
  await page.route(`**/api/rooms/${roomId}/start`, (route) => {
    startRequests += 1;
    currentSnapshot = starting;
    return route.fulfill({ json: starting });
  });
  await page.route(`**/api/rooms/${roomId}/runtime-start`, async (route) => {
    runtimeRequests += 1;
    if (runtimeRequests === 1) {
      await route.fulfill({
        status: 503,
        json: { error: { code: 'runtime_start_failed', message: '运行时启动失败' } },
      });
      return;
    }
    await route.fulfill({ json: { match_id: matchId } });
  });

  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: '开始比赛' }).click();
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toContainText(
    '运行时启动失败',
  );
  await expect(page.getByRole('button', { name: '继续启动比赛' })).toBeEnabled();
  await page.getByRole('button', { name: '继续启动比赛' }).click();
  await expect(page).toHaveURL(`/debate?match_id=${matchId}`);
  expect(startRequests).toBe(1);
  expect(runtimeRequests).toBe(2);
});

test('ready debater changes seat without another device check', async ({ page }) => {
  await installCommonRoutes(page);
  const validCheck = {
    check_version: 3,
    status: 'PASS',
    checked_at: '2026-08-10T09:00:00Z',
    valid_until: '2099-08-10T09:30:00Z',
    is_valid: true,
  };
  const initial = roomSnapshot(true, validCheck);
  const switched = {
    ...initial,
    sequence: 5,
    seats: [
      {
        ...initial.seats[0],
        occupant_type: 'AGENT',
        user_id: null,
        agent_profile_id: '60000000-0000-4000-8000-000000000001',
        occupant_name: '乾元',
      },
      {
        ...initial.seats[1],
        occupant_type: 'HUMAN',
        user_id: user.id,
        agent_profile_id: null,
        occupant_name: user.real_name,
      },
    ],
  };
  let deviceCalls = 0;
  let currentSnapshot: typeof initial | typeof switched = initial;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: currentSnapshot }),
  );
  await page.route(`**/api/rooms/${roomId}/seat`, (route) => {
    currentSnapshot = switched;
    return route.fulfill({ json: switched });
  });
  await page.route(`**/api/rooms/${roomId}/device-check`, (route) => {
    deviceCalls += 1;
    return route.fulfill({ status: 500 });
  });

  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: '反方 1 辩，选择此席位' }).click();
  await expect(page.getByRole('button', { name: '反方 1 辩，我的席位' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByText(/设备检测仍有效，无需重复检测/)).toBeVisible();
  expect(deviceCalls).toBe(0);
});

test('human debater can verify devices and become ready', async ({ page }) => {
  await installCommonRoutes(page);
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function play() {
      queueMicrotask(() => this.dispatchEvent(new Event('ended')));
    };
    window.__JX_SPEAKER_PROBE_OVERRIDE__ = async () => true;
    window.__JX_DEVICE_PROBE_OVERRIDE__ = async () => ({
      status: 'PASS',
      rttP95Ms: 82,
      packetLossP95Percent: 0.4,
      connectionQuality: 'excellent',
      samples: 16,
      inputPeak: 0.08,
      recordingBlob: new Blob(['browser-device-probe'], { type: 'audio/webm' }),
    });
  });
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );
  await page.route(`**/api/rooms/${roomId}/device-check`, (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    expect(payload).not.toHaveProperty('check_version');
    return route.fulfill({
      json: roomSnapshot(false, {
        check_version: 1,
        status: 'PASS',
        checked_at: '2026-08-10T09:00:00Z',
        valid_until: '2099-08-10T09:30:00Z',
        is_valid: true,
      }),
    });
  });
  await page.route(`**/api/rooms/${roomId}/ready`, (route) =>
    route.fulfill({ json: roomSnapshot(true) }),
  );
  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: /开始设备检测/ }).click();
  await expect(page.locator('aside').getByText('已准备', { exact: true })).toBeVisible();
});

test('an organizer occupying a human seat can run the same device preparation flow', async ({
  page,
}) => {
  await installCommonRoutes(page);
  const organizerRoom = {
    ...roomSnapshot(),
    viewer_member_role: 'ORGANIZER',
    members: roomSnapshot().members.map((member) => ({
      ...member,
      member_role: 'ORGANIZER',
    })),
  };
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: organizerRoom }),
  );

  await page.goto(`/rooms/${roomId}`);

  await expect(page.getByRole('button', { name: /开始设备检测/ })).toBeVisible();
  await expect(page.getByRole('region', { name: '入场进度' })).toContainText('检测设备');
});

test('device check save failure explains the next action and remains retryable', async ({
  page,
}) => {
  await installCommonRoutes(page);
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function play() {
      queueMicrotask(() => this.dispatchEvent(new Event('ended')));
    };
    window.__JX_SPEAKER_PROBE_OVERRIDE__ = async () => true;
    window.__JX_DEVICE_PROBE_OVERRIDE__ = async () => ({
      status: 'PASS',
      rttP95Ms: 82,
      packetLossP95Percent: 0.4,
      connectionQuality: 'excellent',
      samples: 16,
      inputPeak: 0.08,
      recordingBlob: new Blob(['browser-device-probe'], { type: 'audio/webm' }),
    });
  });
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );
  await page.route(`**/api/rooms/${roomId}/device-check`, (route) =>
    route.fulfill({ status: 500, body: 'Internal Server Error' }),
  );
  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: /开始设备检测/ }).click();
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toContainText(
    '检测结果保存失败，请重新检测后再试。',
  );
  await expect(page.getByRole('button', { name: /重新检测/ })).toBeEnabled();
});

test('ready failure keeps the saved check available for a short retry', async ({ page }) => {
  await installCommonRoutes(page);
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function play() {
      queueMicrotask(() => this.dispatchEvent(new Event('ended')));
    };
    window.__JX_SPEAKER_PROBE_OVERRIDE__ = async () => true;
    window.__JX_DEVICE_PROBE_OVERRIDE__ = async () => ({
      status: 'PASS',
      rttP95Ms: 82,
      packetLossP95Percent: 0.4,
      connectionQuality: 'excellent',
      samples: 16,
      inputPeak: 0.08,
      recordingBlob: new Blob(['browser-device-probe'], { type: 'audio/webm' }),
    });
  });
  const savedCheck = {
    check_version: 1,
    status: 'PASS',
    checked_at: '2026-08-10T09:00:00Z',
    valid_until: '2099-08-10T09:30:00Z',
    is_valid: true,
  };
  let currentSnapshot = roomSnapshot();
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: currentSnapshot }),
  );
  await page.route(`**/api/rooms/${roomId}/device-check`, (route) => {
    currentSnapshot = roomSnapshot(false, savedCheck);
    return route.fulfill({ json: currentSnapshot });
  });
  await page.route(`**/api/rooms/${roomId}/ready`, (route) =>
    route.fulfill({ status: 500, body: 'Internal Server Error' }),
  );
  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: /开始设备检测/ }).click();
  await expect(page.getByText('上次检测仍有效')).toBeVisible();
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toContainText(
    '检测已保存，但准备状态更新失败。请点击“直接使用检测并准备”重试。',
  );
  await expect(page.getByRole('button', { name: '直接使用检测并准备' })).toBeEnabled();
});

test('a new user enters a running room through the spectator gateway', async ({ page }) => {
  await installCommonRoutes(page);
  const matchId = '90000000-0000-4000-8000-000000000001';
  const runningRoom = {
    ...roomSnapshot(),
    status: 'RUNNING',
    match_id: matchId,
    viewer_membership_state: 'NONE',
    viewer_member_role: null,
    viewer_ready: false,
    members: [],
  };
  const joinedRoom = {
    ...runningRoom,
    viewer_membership_state: 'ACTIVE',
    viewer_member_role: 'SPECTATOR',
    members: [
      {
        user_id: user.id,
        member_role: 'SPECTATOR',
        online: true,
        ready: false,
        joined_at: '2026-08-10T09:00:00Z',
        real_name: user.real_name,
      },
    ],
  };
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: runningRoom }),
  );
  await page.route(`**/api/rooms/${roomId}/join`, (route) => route.fulfill({ json: joinedRoom }));
  await page.goto(`/rooms/${roomId}`);
  await page.getByRole('button', { name: '作为观众进入比赛' }).click();
  await expect(page).toHaveURL(`/debate?match_id=${matchId}`);
});

test('a paused debater can reopen the device check without being redirected to the match', async ({
  page,
}) => {
  await installCommonRoutes(page);
  const matchId = '90000000-0000-4000-8000-000000000001';
  const pausedRoom = {
    ...roomSnapshot(true, {
      check_version: 4,
      status: 'PASS',
      checked_at: '2026-08-10T08:00:00Z',
      valid_until: '2026-08-10T08:30:00Z',
      is_valid: false,
    }),
    status: 'PAUSED',
    match_id: matchId,
  };
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: pausedRoom }),
  );

  await page.goto(`/rooms/${roomId}?recheck=1`);

  await expect(page).toHaveURL(`/rooms/${roomId}?recheck=1`);
  await expect(page.getByRole('heading', { name: '恢复前设备复检' })).toBeVisible();
  await expect(page.getByRole('button', { name: /开始设备检测/ })).toBeVisible();
  await expect(page.getByRole('link', { name: '返回比赛' })).toHaveAttribute(
    'href',
    `/debate?match_id=${matchId}`,
  );
  await expect(page.getByRole('button', { name: '退出房间' })).toHaveCount(0);
});

test('re-entering a waiting room reuses a valid device check', async ({ page }) => {
  await installCommonRoutes(page);
  const reusable = {
    check_version: 7,
    status: 'PASS',
    checked_at: '2026-08-10T09:00:00Z',
    valid_until: '2099-08-10T09:30:00Z',
    is_valid: true,
  };
  let deviceCheckCalls = 0;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot(false, reusable) }),
  );
  await page.route(`**/api/rooms/${roomId}/device-check`, (route) => {
    deviceCheckCalls += 1;
    return route.fulfill({ status: 500, json: { message: '不应重新检测' } });
  });
  await page.route(`**/api/rooms/${roomId}/ready`, (route) => {
    expect(route.request().postDataJSON()).toEqual({ check_version: reusable.check_version });
    return route.fulfill({ json: roomSnapshot(true, reusable) });
  });
  await page.goto(`/rooms/${roomId}`);
  await expect(page.getByText('上次检测仍有效')).toBeVisible();
  await page.getByRole('button', { name: '直接使用检测并准备' }).click();
  await expect(page.locator('aside').getByText('已准备', { exact: true })).toBeVisible();
  expect(deviceCheckCalls).toBe(0);
});

test('waiting room exit uses the shared confirmation and hides implementation details', async ({
  page,
}) => {
  await installCommonRoutes(page);
  let leaveRequests = 0;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );
  await page.route(`**/api/rooms/${roomId}/leave`, (route) => {
    leaveRequests += 1;
    return route.fulfill({ json: { ...roomSnapshot(), status: 'TERMINATED' } });
  });

  await page.goto(`/rooms/${roomId}`);
  await expect(page.getByText(/每 1\.5 秒|同步服务端状态/)).toHaveCount(0);
  await page.getByRole('button', { name: '退出房间' }).click();
  const dialog = page.getByRole('alertdialog', { name: '退出并关闭房间？' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '取消' }).click();
  expect(leaveRequests).toBe(0);

  await page.getByRole('button', { name: '退出房间' }).click();
  await page
    .getByRole('alertdialog', { name: '退出并关闭房间？' })
    .getByRole('button', { name: '退出并关闭房间' })
    .click();
  await expect(page).toHaveURL('/lobby');
  expect(leaveRequests).toBe(1);
});

test('seat swap synchronization reports one transient failure and recovers automatically', async ({
  page,
}) => {
  await installCommonRoutes(page);
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: roomSnapshot() }),
  );
  await page.unroute('**/api/rooms/*/seat-swap-requests');
  let requests = 0;
  await page.route(`**/api/rooms/${roomId}/seat-swap-requests`, (route) => {
    requests += 1;
    if (requests === 1) {
      return route.fulfill({
        status: 503,
        json: { error: { code: 'temporarily_unavailable', message: '暂时不可用' } },
      });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto(`/rooms/${roomId}`);
  await expect(page.getByText('席位交换状态暂时无法同步，系统会自动重试。')).toBeVisible();
  await expect.poll(() => requests).toBeGreaterThanOrEqual(2);
  await expect(page.getByText('席位交换状态暂时无法同步，系统会自动重试。')).toHaveCount(1);
});
