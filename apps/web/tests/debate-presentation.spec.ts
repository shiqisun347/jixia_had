import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test } from './fixtures';

const matchId = '70000000-0000-4000-8000-000000000304';
const roomId = '40000000-0000-4000-8000-000000000304';
const initialSnapshot = {
  match_id: matchId,
  room_id: roomId,
  status: 'RUNNING',
  action_state: 'HUMAN_SPEAKING',
  sequence: 5,
  current_action_index: 0,
  current_action: {
    stage_position: 1,
    action_position: 1,
    action_kind: 'HUMAN_SPEECH',
    duration_seconds: 30,
  },
  current_speech_id: 'speech-304',
  current_speaker_user_id: 'speaker-a1',
  current_speaker_side: 'AFFIRMATIVE',
  current_speaker_seat_no: 1,
  current_agent_profile_id: null,
  speech_remaining_ms: 20000,
  countdown_remaining_ms: null,
  host_audio_remaining_ms: null,
  free_holder_side: null,
  free_affirmative_remaining_ms: null,
  free_negative_remaining_ms: null,
  hand_queue: [],
  agent_hand_queue: [],
  team_hand_queue: [],
  agent_decisions: [],
  hand_window_open: false,
  error_code: null,
  resume_reasons: [],
  interim_text: '',
  formal_4v4: true,
};
const room = {
  id: roomId,
  code: 'JX0304',
  status: 'RUNNING',
  organizer_user_id: 'organizer',
  topic: {
    title:
      '在人工智能快速发展的今天，我们更应重视效率还是公平？这是一道包含长文字的会议现场测试辩题',
    affirmative_text: '应当优先重视公平与长期共同利益',
    negative_text: '应当优先重视效率与可持续发展',
  },
  rule: {
    name: 'YXH 4v4',
    side_size: 4,
    stages: [{ position: 1, name: '正方一辩立论' }],
    host_identity: { name: '龙风月瑶', provider_voice: 'longanwen_v3.1' },
  },
  members: [],
  seats: ['AFFIRMATIVE', 'NEGATIVE'].flatMap((side) =>
    [1, 2, 3, 4].map((seat_no) => ({
      id: `${side}-${seat_no}`,
      side,
      seat_no,
      occupant_type: seat_no % 2 === 0 ? 'AGENT' : 'HUMAN',
      user_id:
        seat_no % 2 === 0
          ? null
          : side === 'AFFIRMATIVE' && seat_no === 1
            ? 'speaker-a1'
            : `${side}-${seat_no}`,
      occupant_name:
        seat_no % 2 === 0
          ? `${side === 'AFFIRMATIVE' ? (seat_no === 2 ? '执锋' : '博闻') : seat_no === 2 ? '睿断' : '明辨'} Agent`
          : `${side === 'AFFIRMATIVE' ? '正' : '反'}方${seat_no}辩·会议现场长姓名测试`,
      agent_profile_id: seat_no % 2 === 0 ? `${side}-agent-${seat_no}` : null,
    })),
  ),
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript((snapshot) => {
    let current = snapshot;
    const owned = window as unknown as {
      __SPEC304_ALLOW_COMMAND__: boolean;
      __SPEC304_ALLOW_START__: boolean;
      __SPEC304_MICROPHONE_STATES__: boolean[];
    };
    owned.__SPEC304_MICROPHONE_STATES__ = [];
    type Event = { data?: string };
    class OwnedSocket {
      readyState = 1;
      listeners = new Map<string, ((event: Event) => void)[]>();
      constructor() {
        (window as unknown as { __SPEC304_EMIT__: (patch: object) => void }).__SPEC304_EMIT__ = (
          patch,
        ) => {
          current = { ...current, ...patch, sequence: current.sequence + 1 };
          this.emit('message', {
            data: JSON.stringify({ type: 'match.snapshot', connection_epoch: 1, payload: current }),
          });
        };
        queueMicrotask(() => {
          this.emit('open', {});
          this.emit('message', {
            data: JSON.stringify({ type: 'match.snapshot', connection_epoch: 1, payload: current }),
          });
        });
      }
      addEventListener(type: string, listener: (event: Event) => void) {
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
      }
      emit(type: string, event: Event) {
        this.listeners.get(type)?.forEach((listener) => listener(event));
      }
      send(raw: string) {
        if (!owned.__SPEC304_ALLOW_COMMAND__)
          throw new Error('Display tests must not submit match commands');
        const command = JSON.parse(raw) as { type: string; message_id: string };
        if (command.type !== 'speech.start') throw new Error('Unexpected owned command');
        queueMicrotask(() => {
          if (owned.__SPEC304_ALLOW_START__) {
            current = {
              ...current,
              status: 'RUNNING',
              action_state: 'HUMAN_SPEAKING',
              sequence: current.sequence + 1,
            };
            this.emit('message', {
              data: JSON.stringify({
                type: 'command.ack',
                message_id: command.message_id,
                snapshot: current,
              }),
            });
          } else {
            this.emit('message', {
              data: JSON.stringify({
                type: 'command.error',
                code: 'asr_finalization_pending',
                message_id: command.message_id,
              }),
            });
          }
        });
      }
      close() {
        this.readyState = 3;
      }
    }
    window.__JX_MATCH_SOCKET_FACTORY__ = () => new OwnedSocket() as unknown as WebSocket;
    window.__JX_MATCH_AUDIO_OVERRIDE__ = async () => ({
      prepareMicrophone: async () => undefined,
      setMicrophoneEnabled: async (enabled) => {
        owned.__SPEC304_MICROPHONE_STATES__.push(enabled);
      },
      enableAudio: async () => undefined,
      canPlaybackAudio: true,
      disconnect: () => undefined,
    });
    (
      window as unknown as {
        __SPEC304_ORIGINAL_FULLSCREEN__: typeof Element.prototype.requestFullscreen;
      }
    ).__SPEC304_ORIGINAL_FULLSCREEN__ = Element.prototype.requestFullscreen;
    Element.prototype.requestFullscreen = async () => {
      throw new Error('Owned fullscreen refusal');
    };
  }, initialSnapshot);
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: { id: 'spectator', real_name: '大屏观众', role: 'USER', must_change_password: false },
      },
    }),
  );
  await page.route(`**/api/matches/${matchId}/snapshot`, (route) =>
    route.fulfill({ json: initialSnapshot }),
  );
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) => route.fulfill({ json: room }));
  await page.route(`**/api/matches/${matchId}/transcript`, (route) =>
    route.fulfill({ json: { match_id: matchId, context_version: 1, speeches: [] } }),
  );
  await page.route('**/api/users/*/avatar*', (route) =>
    route.fulfill({ status: 302, headers: { location: '/assets/avatars/human-01.webp' } }),
  );
  await page.route(`**/api/matches/${matchId}/livekit-token`, (route) =>
    route.fulfill({
      json: {
        server_url: 'ws://synthetic.invalid',
        participant_token: 'synthetic-unused',
        room_name: 'owned-room',
        expires_in_seconds: 600,
      },
    }),
  );
});

test('presentation uses the home brand at conference scale and shows the complete long topic', async ({
  page,
}, testInfo) => {
  const longTopic =
    '面对人工智能不断重塑知识传播与公共讨论的时代，人类与智能体共同参与现场辩论，应当更重视观点表达的多元公平，还是持续追求信息交锋的效率与深度？';
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) =>
    route.fulfill({ json: { ...room, topic: { ...room.topic, title: longTopic } } }),
  );
  await page.goto(`/debate?match_id=${matchId}`);
  await page.getByRole('button', { name: '切换大屏展示' }).click();
  for (const [width, height, logoSize, wordmarkSize] of [
    [1280, 720, 72, 30],
    [1920, 1080, 104, 42],
  ]) {
    await page.setViewportSize({ width, height });
    const header = page.locator('.debate-topic');
    await expect(header.getByRole('heading', { name: longTopic })).toBeVisible();
    const geometry = await header.evaluate((element) => {
      const logo = element.querySelector('.jx-brand__mark')!;
      const wordmark = element.querySelector('.jx-brand__copy strong')!;
      const topic = element.querySelector('h1')!;
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d', { willReadFrequently: true })!;
      const rgb = (css: string) => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = css;
        context.fillRect(0, 0, 1, 1);
        return Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3);
      };
      const luminance = (color: number[]) =>
        color
          .map((channel) => channel / 255)
          .map((channel) =>
            channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
          )
          .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
      const background = luminance(rgb(getComputedStyle(element).backgroundColor));
      const contrast = (node: Element) => {
        const ink = luminance(rgb(getComputedStyle(node).color));
        return (Math.max(background, ink) + 0.05) / (Math.min(background, ink) + 0.05);
      };
      return {
        logo: logo.getBoundingClientRect().width,
        wordmark: parseFloat(getComputedStyle(wordmark).fontSize),
        textWidth: topic.scrollWidth,
        width: topic.clientWidth,
        textHeight: topic.scrollHeight,
        height: topic.clientHeight,
        overflow: document.documentElement.scrollWidth > innerWidth,
        topicContrast: contrast(topic),
        wordmarkContrast: contrast(wordmark),
        secondaryContrast: contrast(element.querySelector('.jx-brand__copy small')!),
      };
    });
    expect(geometry.logo).toBeCloseTo(logoSize, 0);
    expect(geometry.wordmark).toBeCloseTo(wordmarkSize, 0);
    expect(geometry.textWidth).toBeLessThanOrEqual(geometry.width + 1);
    expect(geometry.textHeight).toBeLessThanOrEqual(geometry.height + 1);
    expect(geometry.overflow).toBe(false);
    expect(geometry.topicContrast).toBeGreaterThanOrEqual(4.5);
    expect(geometry.wordmarkContrast).toBeGreaterThanOrEqual(4.5);
    expect(geometry.secondaryContrast).toBeGreaterThanOrEqual(4.5);
    const namesFit = await page.locator('.debate-seat-card').evaluateAll((cards) =>
      cards.every((card) => {
        const name = card.querySelector('strong')!.getBoundingClientRect();
        const position = card.querySelector('p')!.getBoundingClientRect();
        const box = card.getBoundingClientRect();
        return (
          name.top >= box.top &&
          position.bottom <= box.bottom &&
          name.left >= box.left &&
          name.right <= box.right
        );
      }),
    );
    expect(namesFit).toBe(true);
    for (const name of ['执锋 Agent', '博闻 Agent', '睿断 Agent', '明辨 Agent']) {
      await expect(
        page.locator('.debate-seat-card').getByText(name, { exact: true }),
      ).toBeVisible();
    }
    const capturePath = resolve('..', '..', 'profile_output', 'spec307', 'conference-visual');
    mkdirSync(capturePath, { recursive: true });
    await page.screenshot({
      path: resolve(capturePath, `${testInfo.project.name}-${width}x${height}.png`),
    });
  }
});

test('transcript keeps official records and the earlier source visible through malformed refreshes', async ({
  page,
}) => {
  let malformed = false;
  const official = '这段正式文字已经提交，识别异常和下一辩手的申请不能清空它。';
  await page.route(`**/api/matches/${matchId}/transcript`, (route) =>
    route.fulfill({
      json: malformed
        ? { speeches: {} }
        : {
            match_id: matchId,
            context_version: 1,
            speeches: [
              {
                id: 'official-before-error',
                action_key: '1:1',
                side: 'AFFIRMATIVE',
                seat_no: 1,
                speaker_kind: 'HUMAN',
                user_id: 'speaker-a1',
                status: 'FINALIZED',
                display_text: official,
              },
            ],
          },
    }),
  );
  await page.goto(`/debate?match_id=${matchId}`);
  await expect(page.getByTestId('current-debate-stage')).toBeVisible();
  const transcriptButton = page.getByRole('button', { name: '文字记录' });
  if (await transcriptButton.isVisible()) await transcriptButton.click();
  await expect(page.getByText(official).last()).toBeVisible();
  malformed = true;
  await page.evaluate(() =>
    (window as unknown as { __SPEC304_EMIT__: (patch: object) => void }).__SPEC304_EMIT__({
      action_state: 'HUMAN_READY_TO_START',
      current_speech_id: null,
      current_speaker_user_id: 'NEGATIVE-3',
      current_speaker_side: 'NEGATIVE',
      current_speaker_seat_no: 3,
      pending_source: {
        speech_id: 'previous-human-a1',
        speaker_kind: 'HUMAN',
        side: 'AFFIRMATIVE',
        seat_no: 1,
        status: 'FINALIZING',
      },
    }),
  );
  await expect(page.getByText(official).last()).toBeVisible();
  const pending = page.getByTestId('transcript-pending-source').last();
  await expect(pending).toHaveAttribute('data-speech-id', 'previous-human-a1');
  await expect(pending).toContainText('正方一辩');
  await expect(pending).not.toContainText('反方三辩');
  await expect(page.getByText('文字记录同步中断，当前显示上次结果。').last()).toBeVisible();
  await page.evaluate(() =>
    (window as unknown as { __SPEC304_EMIT__: (patch: object) => void }).__SPEC304_EMIT__({
      status: 'ERROR',
      action_state: 'RECOVERY_REQUIRED',
      error_code: 'asr_finalization_failed',
      pending_source: {
        speech_id: 'previous-human-a1',
        speaker_kind: 'HUMAN',
        side: 'AFFIRMATIVE',
        seat_no: 1,
        status: 'FAILED',
      },
    }),
  );
  await expect(pending).toContainText('尚未保存成功');
  await expect(page.getByText(official).last()).toBeVisible();
  await expect(page.locator('.debate-arena')).toBeVisible();
});

test('native fullscreen can be exited with Escape', async ({ page }) => {
  await page.goto(`/debate?match_id=${matchId}`);
  const header = page.locator('.debate-topic');
  await expect(header.getByText('房间号', { exact: true })).toBeVisible();
  await expect(header.getByText(room.code, { exact: true })).toBeVisible();
  await expect(header.getByText(/YXH 4v4/)).toBeVisible();
  await page.evaluate(() => {
    Element.prototype.requestFullscreen = (
      window as unknown as {
        __SPEC304_ORIGINAL_FULLSCREEN__: typeof Element.prototype.requestFullscreen;
      }
    ).__SPEC304_ORIGINAL_FULLSCREEN__;
  });
  await page.getByRole('button', { name: '切换大屏展示' }).click();
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement))).toBe(true);
  await expect(header.getByRole('img', { name: '稷下', exact: true })).toBeVisible();
  await expect(header.getByText('房间号', { exact: true })).toHaveCount(0);
  await expect(header.getByText(room.code, { exact: true })).toHaveCount(0);
  await expect(header.getByText(/YXH 4v4/)).toHaveCount(0);
  await expect(header.getByRole('button', { name: '文字记录' })).toHaveCount(0);
  await expect(header.getByRole('button', { name: '切换大屏展示' })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('main.debate-page')).toHaveAttribute('data-presentation-mode', 'false');
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement))).toBe(false);
  await expect(header.getByText('房间号', { exact: true })).toBeVisible();
  await expect(header.getByText(room.code, { exact: true })).toBeVisible();
  await expect(header.getByText(/YXH 4v4/)).toBeVisible();
  await expect(header.getByRole('button', { name: '切换大屏展示' })).toBeVisible();
});

test('an ordinary organizer receives no administrator recovery actions', async ({ page }) => {
  let adminRequests = 0;
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: {
          id: 'organizer',
          role: 'USER',
          real_name: '普通房主',
          must_change_password: false,
        },
      },
    }),
  );
  await page.route('**/api/admin/matches/**/control-target', (route) => {
    adminRequests += 1;
    return route.fulfill({ status: 403, json: {} });
  });
  await page.goto(`/debate?match_id=${matchId}`);
  await page.getByRole('button', { name: '打开应急控制台' }).click();
  await expect(page.getByRole('heading', { name: '应急控制台' })).toBeVisible();
  await expect(page.getByRole('region', { name: '管理员应急恢复' })).toHaveCount(0);
  expect(adminRequests).toBe(0);
});

test('a delayed server event does not extend the displayed hand window', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: {
          id: 'speaker-a1',
          role: 'USER',
          real_name: '申请方辩手',
          must_change_password: false,
        },
      },
    }),
  );
  await page.goto(`/debate?match_id=${matchId}`);
  await expect(page.getByText('发言计时', { exact: true })).toBeVisible();
  await page.evaluate(() =>
    (window as unknown as { __SPEC304_EMIT__: (patch: object) => void }).__SPEC304_EMIT__({
      action_state: 'FREE_SELECTING',
      current_action: { stage_position: 3, action_kind: 'FREE_DEBATE', duration_seconds: 300 },
      current_speaker_user_id: null,
      current_speaker_side: null,
      free_holder_side: 'AFFIRMATIVE',
      hand_window_open: true,
      free_affirmative_remaining_ms: 300000,
      free_negative_remaining_ms: 300000,
      experiment_team_state: {
        opportunity_id: 'owned-next',
        opportunity_generation: 2,
        selection_phase: 'COMPETING',
        agent_status: 'WAITING',
        selection_remaining_ms: 1000,
        human_wait_remaining_ms: null,
      },
    }),
  );
  await expect(page.getByRole('button', { name: '申请发言', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '申请发言', exact: true })).toHaveCount(0);
  await expect(page.getByTestId('free-selection-stage')).toBeVisible();
});

test('a pending ASR handoff leaves the microphone off until a later acknowledged retry', async ({
  page,
}) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: {
          id: 'speaker-a1',
          role: 'USER',
          real_name: '下一位真人',
          must_change_password: false,
        },
      },
    }),
  );
  await page.goto(`/debate?match_id=${matchId}`);
  await expect(page.getByText('发言计时', { exact: true })).toBeVisible();
  await page.evaluate(() => {
    (window as unknown as { __SPEC304_EMIT__: (patch: object) => void }).__SPEC304_EMIT__({
      action_state: 'HUMAN_READY_TO_START',
    });
    (window as unknown as { __SPEC304_ALLOW_COMMAND__: boolean }).__SPEC304_ALLOW_COMMAND__ = true;
  });
  await expect(page.getByRole('button', { name: '开始发言', exact: true })).toBeEnabled();
  await page.evaluate(() => {
    (
      window as unknown as { __SPEC304_MICROPHONE_STATES__: boolean[] }
    ).__SPEC304_MICROPHONE_STATES__ = [];
  });
  await page.getByRole('button', { name: '开始发言', exact: true }).click();
  await expect(page.getByRole('heading', { name: '等待上一段整理完成' })).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: '上一段发言仍在整理' })).toBeVisible();
  await expect(page.getByText('发言计时', { exact: true })).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      (
        window as unknown as { __SPEC304_MICROPHONE_STATES__: boolean[] }
      ).__SPEC304_MICROPHONE_STATES__.includes(true),
    ),
  ).toBe(false);
  await page.evaluate(() => {
    (window as unknown as { __SPEC304_ALLOW_START__: boolean }).__SPEC304_ALLOW_START__ = true;
  });
  await page.getByRole('button', { name: '开始发言', exact: true }).click();
  await expect(page.getByText('发言计时', { exact: true })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() =>
        (
          window as unknown as { __SPEC304_MICROPHONE_STATES__: boolean[] }
        ).__SPEC304_MICROPHONE_STATES__.at(-1),
      ),
    )
    .toBe(true);
});

test('presentation fills each viewport and terminal state discards old speech controls', async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
  await page.goto(`/debate?match_id=${matchId}`);
  await expect(page.getByText('发言计时', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '切换大屏展示' }).click();
  const main = page.locator('main.debate-page');
  const header = main.locator('.debate-topic');
  await expect(main).toHaveAttribute('data-presentation-mode', 'true');
  await expect(main.getByRole('img', { name: '稷下', exact: true })).toBeVisible();
  for (const [width, height] of [
    [1280, 720],
    [1920, 1080],
    [2560, 1440],
    [3840, 2160],
    [3440, 1440],
    [1024, 768],
    [375, 812],
  ]) {
    await page.setViewportSize({ width, height });
    await expect(header.getByText('房间号', { exact: true })).toHaveCount(0);
    await expect(header.getByText(room.code, { exact: true })).toHaveCount(0);
    await expect(header.getByText(/YXH 4v4/)).toHaveCount(0);
    await expect(header.getByRole('button', { name: '文字记录' })).toHaveCount(0);
    await expect(header.getByRole('button', { name: '切换大屏展示' })).toHaveCount(0);
    const geometry = await main.evaluate((element) => {
      const grid = element.querySelector('[data-testid="debate-content-grid"]')!;
      const arena = element.querySelector('.debate-arena')!;
      return {
        width: element.getBoundingClientRect().width,
        gridWidth: grid.getBoundingClientRect().width,
        arenaWidth: arena.getBoundingClientRect().width,
        pageOverflow: document.documentElement.scrollWidth > innerWidth,
        seats: [...element.querySelectorAll('.debate-seat-card')].map((seat) => ({
          top: seat.getBoundingClientRect().top,
          bottom: seat.getBoundingClientRect().bottom,
        })),
      };
    });
    expect(geometry.width).toBe(width);
    expect(geometry.gridWidth - geometry.arenaWidth).toBeLessThan(4);
    expect(geometry.pageOverflow).toBe(false);
    expect(geometry.seats).toHaveLength(8);
    expect(geometry.seats.every((seat) => seat.top >= 0 && seat.bottom <= height)).toBe(true);
    const output = resolve(
      '../../profile_output/spec304/screens-clean-header',
      testInfo.project.name,
    );
    mkdirSync(output, { recursive: true });
    await page.screenshot({ path: resolve(output, `speaking-${width}x${height}.png`) });
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.evaluate(() =>
    (window as unknown as { __SPEC304_EMIT__: (patch: object) => void }).__SPEC304_EMIT__({
      status: 'FINISHED',
      action_state: 'HOST_ANNOUNCING',
      speech_remaining_ms: null,
      free_holder_side: 'NEGATIVE',
      free_affirmative_remaining_ms: 1000,
      free_negative_remaining_ms: 2000,
    }),
  );
  const center = page.getByTestId('debate-center-stage');
  await expect(center.getByTestId('terminal-stage')).toBeVisible();
  await expect(center.getByText('--:--')).toHaveCount(0);
  await expect(center.getByText('等待发言者')).toHaveCount(0);
  await expect(center.getByTestId('free-debate-clock')).toHaveCount(0);
  await expect(center.getByRole('link', { name: '查看赛后记录' })).toBeVisible();
  await page.screenshot({
    path: resolve(
      '../../profile_output/spec304/screens-clean-header',
      testInfo.project.name,
      'finished-1920x1080.png',
    ),
  });
  await page.keyboard.press('Escape');
  await expect(main).toHaveAttribute('data-presentation-mode', 'false');
  await expect(header.getByText('房间号', { exact: true })).toBeVisible();
  await expect(header.getByText(room.code, { exact: true })).toBeVisible();
  await expect(header.getByText(/YXH 4v4/)).toBeVisible();
  await expect(header.locator('button').filter({ hasText: '文字记录' })).toHaveCount(1);
  await expect(header.getByRole('button', { name: '切换大屏展示' })).toBeVisible();
});
