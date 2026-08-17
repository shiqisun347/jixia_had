import { expect, test } from '@playwright/test';

declare global {
  interface Window {
    __JX_MATCH_SNAPSHOT__: Record<string, unknown>;
    __JX_CLOSE_MATCH_SOCKET__?: () => void;
    __JX_MATCH_SOCKET_COUNT__?: number;
    __JX_EMIT_AGENT_SNAPSHOT__?: () => void;
    __JX_EMIT_COUNTDOWN_SNAPSHOT__?: () => void;
    __JX_EMIT_READY_SNAPSHOT__?: () => void;
    __JX_EMIT_ERROR_SNAPSHOT__?: () => void;
    __JX_EMIT_HUMAN_FINISHED_SNAPSHOT__?: () => void;
    __JX_FAIL_NEXT_COMMAND__?: boolean;
    __JX_FAILED_COMMAND_COUNT__?: number;
    __JX_MICROPHONE_STATES__?: boolean[];
    __JX_OUTPUT_MUTED_STATES__?: boolean[];
    __JX_COMMAND_SEQUENCES__?: number[];
  }
}

const userId = '10000000-0000-4000-8000-000000000001';
const matchId = '70000000-0000-4000-8000-000000000001';
const roomId = '40000000-0000-4000-8000-000000000001';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/users/*/avatar*', (route) =>
    route.fulfill({ status: 302, headers: { location: '/assets/avatars/human-01.webp' } }),
  );
});

test('logged-out direct match link keeps the match id through login', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'not_authenticated', message: '请先登录' } },
    }),
  );

  await page.goto(`/debate?match_id=${matchId}`);
  await expect(page).toHaveURL(
    `/login?return_to=${encodeURIComponent(`/debate?match_id=${matchId}`)}`,
  );
});

const room = {
  id: roomId,
  code: 'JX8K2M',
  title: '实时线性比赛验收',
  label: '训练赛',
  status: 'RUNNING',
  organizer_user_id: userId,
  is_all_agent: false,
  sequence: 5,
  topic: {
    title: '在人工智能快速发展的今天，我们更应重视效率还是公平？',
    affirmative_text: '我们更应重视公平',
    negative_text: '我们更应重视效率',
  },
  rule: {
    name: '4v4 正式辩论赛',
    side_size: 4,
    stages: [
      { position: 1, name: '正方一辩立论' },
      { position: 2, name: '反方一辩立论' },
      { position: 3, name: '自由辩论' },
    ],
  },
  members: [
    {
      user_id: userId,
      member_role: 'DEBATER',
      online: true,
      ready: true,
      joined_at: '2026-08-03T08:00:00Z',
      real_name: '实时测试用户',
    },
  ],
  seats: [
    {
      id: '50000000-0000-4000-8000-000000000001',
      side: 'AFFIRMATIVE',
      seat_no: 1,
      occupant_type: 'HUMAN',
      user_id: userId,
      agent_profile_id: null,
      occupant_name: '实时测试用户',
      occupant_avatar_key: 'human-01',
      occupant_avatar_version: 0,
    },
    ...[2, 3, 4].map((seatNo) => ({
      id: `50000000-0000-4000-8000-00000000000${seatNo}`,
      side: 'AFFIRMATIVE',
      seat_no: seatNo,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: `60000000-0000-4000-8000-00000000000${seatNo}`,
      occupant_name: `Agent-正${seatNo}`,
      occupant_avatar_key: `agent-0${seatNo}`,
    })),
    ...[1, 2, 3, 4].map((seatNo) => ({
      id: `50000000-0000-4000-8000-00000000001${seatNo}`,
      side: 'NEGATIVE',
      seat_no: seatNo,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: `60000000-0000-4000-8000-00000000001${seatNo}`,
      occupant_name: `Agent-反${seatNo}`,
      occupant_avatar_key: `agent-0${seatNo + 4}`,
    })),
  ],
};

function snapshot(actionState: string) {
  return {
    match_id: matchId,
    room_id: roomId,
    status: 'RUNNING',
    action_state: actionState,
    sequence: actionState === 'HUMAN_SPEAKING' ? 4 : 3,
    current_action_index: 0,
    current_action: {
      stage_position: 1,
      action_position: 1,
      action_kind: 'HUMAN_SPEECH',
      duration_seconds: 30,
      side: 'AFFIRMATIVE',
      seat_no: 1,
      speaker_user_id: userId,
      host_audio_path: null,
    },
    current_speech_id:
      actionState === 'HUMAN_SPEAKING' ? '80000000-0000-4000-8000-000000000001' : null,
    current_speaker_user_id: userId,
    speech_remaining_ms: actionState === 'HUMAN_SPEAKING' ? 30_000 : 30_000,
  };
}

test('real runtime page connects and starts human speech from server state', async ({ page }) => {
  test.setTimeout(60_000);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.addInitScript(() => {
    class FakeSocket {
      static OPEN = 1;
      readyState = 1;
      listeners = new Map<string, ((event: { data?: string }) => void)[]>();
      constructor() {
        window.__JX_MATCH_SOCKET_COUNT__ = (window.__JX_MATCH_SOCKET_COUNT__ ?? 0) + 1;
        window.__JX_CLOSE_MATCH_SOCKET__ = () => {
          this.readyState = 3;
          this.emit('close', {});
        };
        window.__JX_EMIT_AGENT_SNAPSHOT__ = () => {
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              payload: {
                ...window.__JX_MATCH_SNAPSHOT__,
                status: 'RUNNING',
                action_state: 'AGENT_SPEAKING',
                sequence: 5,
                current_speaker_user_id: null,
                current_agent_profile_id: '60000000-0000-4000-8000-000000000012',
                current_speaker_side: 'NEGATIVE',
                current_speaker_seat_no: 2,
                speech_remaining_ms: 12_000,
                countdown_remaining_ms: null,
              },
            }),
          });
        };
        window.__JX_EMIT_COUNTDOWN_SNAPSHOT__ = () => {
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              payload: {
                ...window.__JX_MATCH_SNAPSHOT__,
                status: 'PAUSED',
                action_state: 'RESUME_COUNTDOWN',
                sequence: 6,
                current_speaker_user_id: null,
                current_agent_profile_id: '60000000-0000-4000-8000-000000000012',
                current_speaker_side: 'NEGATIVE',
                current_speaker_seat_no: 2,
                speech_remaining_ms: 12_000,
                countdown_remaining_ms: 3_000,
              },
            }),
          });
        };
        window.__JX_EMIT_READY_SNAPSHOT__ = () => {
          window.__JX_MATCH_SNAPSHOT__ = {
            ...window.__JX_MATCH_SNAPSHOT__,
            status: 'RUNNING',
            action_state: 'HUMAN_READY_TO_START',
            sequence: Number(window.__JX_MATCH_SNAPSHOT__.sequence) + 1,
            error_code: null,
          };
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              payload: window.__JX_MATCH_SNAPSHOT__,
            }),
          });
        };
        window.__JX_EMIT_ERROR_SNAPSHOT__ = () => {
          window.__JX_MATCH_SNAPSHOT__ = {
            ...window.__JX_MATCH_SNAPSHOT__,
            status: 'SYSTEM_RECOVERY',
            action_state: 'RECOVERY_REQUIRED',
            error_code: 'tts_stream_interrupted',
          };
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              payload: window.__JX_MATCH_SNAPSHOT__,
            }),
          });
        };
        window.__JX_EMIT_HUMAN_FINISHED_SNAPSHOT__ = () => {
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              payload: {
                ...window.__JX_MATCH_SNAPSHOT__,
                status: 'RUNNING',
                action_state: 'SPEECH_FINALIZING',
                sequence: 7,
                current_speaker_user_id: null,
              },
            }),
          });
        };
        queueMicrotask(() => {
          this.emit('open', {});
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              connection_epoch: 1,
              payload: {
                match_id: '70000000-0000-4000-8000-000000000001',
                room_id: '40000000-0000-4000-8000-000000000001',
                status: 'RUNNING',
                action_state: 'HUMAN_READY_TO_START',
                sequence: 3,
                current_action_index: 0,
                current_action: {
                  stage_position: 1,
                  action_position: 1,
                  action_kind: 'HUMAN_SPEECH',
                  duration_seconds: 30,
                  side: 'AFFIRMATIVE',
                  seat_no: 1,
                  speaker_user_id: '10000000-0000-4000-8000-000000000001',
                  host_audio_path: null,
                },
                current_speech_id: null,
                current_speaker_user_id: '10000000-0000-4000-8000-000000000001',
                speech_remaining_ms: 30_000,
              },
            }),
          });
        });
      }
      addEventListener(type: string, listener: (event: { data?: string }) => void) {
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
      }
      emit(type: string, event: { data?: string }) {
        for (const listener of this.listeners.get(type) ?? []) listener(event);
      }
      send(raw: string) {
        const command = JSON.parse(raw);
        window.__JX_COMMAND_SEQUENCES__?.push(command.expected_sequence);
        if (window.__JX_FAIL_NEXT_COMMAND__) {
          window.__JX_FAIL_NEXT_COMMAND__ = false;
          window.__JX_FAILED_COMMAND_COUNT__ = (window.__JX_FAILED_COMMAND_COUNT__ ?? 0) + 1;
          queueMicrotask(() => {
            this.readyState = 3;
            this.emit('close', {});
          });
          return;
        }
        this.emit('message', {
          data: JSON.stringify({
            type: 'command.ack',
            message_id: command.message_id,
            sequence: Number(window.__JX_MATCH_SNAPSHOT__.sequence) + 1,
            snapshot: {
              ...window.__JX_MATCH_SNAPSHOT__,
              sequence: Number(window.__JX_MATCH_SNAPSHOT__.sequence) + 1,
              action_state:
                command.type === 'match.pause'
                  ? 'RECOVERY_REQUIRED'
                  : command.type === 'match.resume'
                    ? 'RESUME_COUNTDOWN'
                    : command.type === 'speech.start'
                      ? 'HUMAN_SPEAKING'
                      : window.__JX_MATCH_SNAPSHOT__.action_state,
              status:
                command.type === 'match.pause' || command.type === 'match.resume'
                  ? 'PAUSED'
                  : window.__JX_MATCH_SNAPSHOT__.status,
              error_code: command.type === 'match.pause' ? 'tts_stream_interrupted' : null,
            },
          }),
        });
        window.__JX_MATCH_SNAPSHOT__ = {
          ...window.__JX_MATCH_SNAPSHOT__,
          sequence: Number(window.__JX_MATCH_SNAPSHOT__.sequence) + 1,
        };
        if (command.type === 'speech.start') {
          this.emit('message', {
            data: JSON.stringify({
              type: 'match.snapshot',
              payload: {
                ...window.__JX_MATCH_SNAPSHOT__,
                action_state: 'HUMAN_SPEAKING',
                sequence: 4,
                current_speech_id: '80000000-0000-4000-8000-000000000001',
              },
            }),
          });
          this.emit('message', {
            data: JSON.stringify({
              type: 'asr.interim',
              payload: { text: '这是当前正在识别的实时发言' },
            }),
          });
        }
      }
      close() {
        this.readyState = 3;
      }
    }
    window.__JX_MATCH_SNAPSHOT__ = {
      match_id: '70000000-0000-4000-8000-000000000001',
      room_id: '40000000-0000-4000-8000-000000000001',
      status: 'RUNNING',
      action_state: 'HUMAN_READY_TO_START',
      sequence: 3,
      current_action_index: 0,
      current_action: {
        stage_position: 1,
        action_position: 1,
        action_kind: 'HUMAN_SPEECH',
        duration_seconds: 30,
        side: 'AFFIRMATIVE',
        seat_no: 1,
        speaker_user_id: '10000000-0000-4000-8000-000000000001',
        host_audio_path: null,
      },
      current_speech_id: null,
      current_speaker_user_id: '10000000-0000-4000-8000-000000000001',
      speech_remaining_ms: 30_000,
    };
    window.__JX_MICROPHONE_STATES__ = [];
    window.__JX_OUTPUT_MUTED_STATES__ = [];
    window.__JX_COMMAND_SEQUENCES__ = [];
    window.__JX_MATCH_AUDIO_OVERRIDE__ = async () => ({
      setMicrophoneEnabled: async (enabled) => {
        window.__JX_MICROPHONE_STATES__?.push(enabled);
      },
      enableAudio: async () => undefined,
      setOutputMuted: (muted) => {
        window.__JX_OUTPUT_MUTED_STATES__?.push(muted);
      },
      disconnect: () => undefined,
      getNetworkStats: async () => ({ rttMs: null, packetLossPercent: null }),
    });
    window.__JX_MATCH_SOCKET_FACTORY__ = () => new FakeSocket() as unknown as WebSocket;
  });
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { user: { id: userId, real_name: '实时测试用户', must_change_password: false } },
    }),
  );
  await page.route(`**/api/matches/${matchId}/snapshot`, (route) =>
    route.fulfill({ json: snapshot('HUMAN_READY_TO_START') }),
  );
  let roomSnapshotRequests = 0;
  await page.route(`**/api/rooms/${roomId}/snapshot`, (route) => {
    roomSnapshotRequests += 1;
    return route.fulfill({
      json: {
        ...room,
        members: room.members.map((member) => ({
          ...member,
          online: roomSnapshotRequests > 1,
        })),
      },
    });
  });
  const transcript = {
    match_id: matchId,
    context_version: 18,
    speeches: Array.from({ length: 18 }, (_, index) => ({
      id: `80000000-0000-4000-8000-${String(index + 10).padStart(12, '0')}`,
      match_id: matchId,
      action_key: `1:${index + 1}`,
      user_id: index === 0 ? userId : null,
      speaker_kind: index === 0 ? 'HUMAN' : 'AGENT',
      agent_profile_id:
        index === 0 ? null : `60000000-0000-4000-8000-${String(index + 10).padStart(12, '0')}`,
      generation_id: null,
      side: index % 2 === 0 ? 'AFFIRMATIVE' : 'NEGATIVE',
      seat_no: (index % 4) + 1,
      status: 'FINALIZED',
      asr_raw_final_text: `第 ${index + 1} 条正式发言。`,
      display_text: `第 ${index + 1} 条正式发言用于验证文字记录独立滚动。`,
      audio_duration_ms: 8_000,
      finalized_at: '2026-08-12T00:00:00Z',
      audio_truncated: false,
    })),
  };
  let transcriptSaveCount = 0;
  await page.route(`**/api/matches/${matchId}/speeches/*/display-text`, async (route) => {
    transcriptSaveCount += 1;
    if (transcriptSaveCount === 1) {
      await route.fulfill({
        status: 503,
        json: { error: { code: 'service_unavailable', message: '文字服务暂时不可用，请重试' } },
      });
      return;
    }
    const body = route.request().postDataJSON() as { display_text: string };
    await route.fulfill({
      json: {
        ...transcript,
        context_version: 19,
        speeches: transcript.speeches.map((speech, index) =>
          index === 0 ? { ...speech, display_text: body.display_text } : speech,
        ),
      },
    });
  });
  let transcriptRequests = 0;
  await page.route(`**/api/matches/${matchId}/transcript`, (route) => {
    transcriptRequests += 1;
    if (transcriptRequests === 1) {
      return route.fulfill({
        status: 503,
        json: { error: { code: 'service_unavailable', message: '文字记录暂时不可用' } },
      });
    }
    return route.fulfill({ json: transcript });
  });
  await page.route(`**/api/matches/${matchId}/livekit-token`, (route) =>
    route.fulfill({
      json: {
        server_url: 'ws://localhost:7880',
        participant_token: 'test',
        room_name: `jx-match-${matchId}`,
        expires_in_seconds: 600,
      },
    }),
  );
  await page.goto(`/debate?match_id=${matchId}`);
  await expect(page.getByRole('heading', { name: '轮到你发言了！' })).toBeVisible();
  await expect.poll(() => roomSnapshotRequests).toBeGreaterThan(1);
  await expect(page.getByTestId('participant-presence').first()).toHaveAttribute(
    'aria-label',
    '在线',
  );
  const initialTranscriptButton = page.getByRole('button', { name: '文字记录' });
  if (await initialTranscriptButton.isVisible()) await initialTranscriptButton.click();
  await expect(page.getByText('文字记录暂时无法加载。').last()).toBeVisible();
  await page.getByRole('button', { name: '重新同步' }).last().click();
  await expect.poll(() => transcriptRequests).toBe(2);
  await expect(page.getByText('第 1 条正式发言用于验证文字记录独立滚动。').last()).toBeVisible();
  const closeTranscriptButton = page.getByRole('button', { name: '关闭文字记录' });
  if (await closeTranscriptButton.isVisible()) await closeTranscriptButton.click();
  await expect(page.getByText('比赛连接状态')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '查看网络状态' })).toBeVisible();
  await expect(page.getByTestId('current-debate-stage')).toHaveText('当前阶段 · 正方一辩立论');
  const controlGroups = [
    page.getByTestId('match-controls-audio'),
    page.getByTestId('match-controls-speech'),
    page.getByTestId('match-controls-system'),
  ];
  const controlBoxes = await Promise.all(controlGroups.map((group) => group.boundingBox()));
  expect(controlBoxes.every(Boolean)).toBe(true);
  expect(controlBoxes[0]!.x).toBeLessThan(controlBoxes[1]!.x);
  expect(controlBoxes[1]!.x).toBeLessThan(controlBoxes[2]!.x);
  const muteOutputButton = page.getByRole('button', { name: '静音本机声音' });
  await expect(muteOutputButton).toHaveAttribute('aria-pressed', 'false');
  const microphoneStatesBeforeMute = await page.evaluate(() => window.__JX_MICROPHONE_STATES__);
  const commandSequencesBeforeMute = await page.evaluate(() => window.__JX_COMMAND_SEQUENCES__);
  await muteOutputButton.click();
  const restoreOutputButton = page.getByRole('button', { name: '恢复本机声音' });
  await expect(restoreOutputButton).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => page.evaluate(() => window.__JX_OUTPUT_MUTED_STATES__)).toEqual([true]);
  await expect(page.locator('audio').last()).toHaveJSProperty('muted', true);
  expect(await page.evaluate(() => window.__JX_MICROPHONE_STATES__)).toEqual(
    microphoneStatesBeforeMute,
  );
  expect(await page.evaluate(() => window.__JX_COMMAND_SEQUENCES__)).toEqual(
    commandSequencesBeforeMute,
  );
  await restoreOutputButton.click();
  await expect(page.getByRole('button', { name: '静音本机声音' })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
  await expect
    .poll(() => page.evaluate(() => window.__JX_OUTPUT_MUTED_STATES__))
    .toEqual([true, false]);
  await expect(page.locator('audio').last()).toHaveJSProperty('muted', false);
  const networkStatusTrigger = page.getByRole('button', { name: '查看网络状态' });
  await networkStatusTrigger.click();
  await expect(page.getByRole('dialog', { name: '网络状态' })).toBeVisible();
  await expect(page.getByRole('button', { name: '关闭网络状态' })).toBeFocused();
  await expect(page.getByText('最近更新')).toBeVisible();
  await expect(page.getByText('暂无数据')).toHaveCount(2);
  await expect(page.getByText(/^\d{1,2}:\d{2}:\d{2}$/)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '网络状态' })).toHaveCount(0);
  await expect(networkStatusTrigger).toBeFocused();
  await page.evaluate(() => window.__JX_CLOSE_MATCH_SOCKET__?.());
  await expect.poll(() => page.evaluate(() => window.__JX_MATCH_SOCKET_COUNT__ ?? 0)).toBe(2);
  await expect(page.getByRole('button', { name: '查看网络状态' })).toBeVisible();
  await page.getByRole('button', { name: '暂停比赛' }).click();
  const pauseDialog = page.getByRole('alertdialog', { name: '暂停整场比赛？' });
  await expect(pauseDialog).toBeVisible();
  await pauseDialog.getByRole('button', { name: '确认暂停' }).click();
  await expect(page.getByRole('heading', { name: '比赛已安全暂停' })).toBeVisible();
  await page.evaluate(() => window.__JX_EMIT_ERROR_SNAPSHOT__?.());
  await expect(
    page.getByText('实时服务发生异常，比赛已暂停，请检查设备后申请恢复。'),
  ).toBeVisible();
  await page.getByRole('button', { name: '申请恢复' }).click();
  await expect(page.getByRole('heading', { name: '3 秒后恢复比赛' })).toBeVisible();
  await expect(page.getByText('恢复倒计时', { exact: true })).toBeVisible();
  await expect(page.getByText('恢复倒计时进行中')).toBeVisible();
  await expect(page.getByRole('button', { name: '申请恢复' })).toHaveCount(0);
  await expect(page.getByText('实时服务发生异常，比赛已暂停，请检查设备后申请恢复。')).toHaveCount(
    0,
  );
  await expect.poll(() => page.evaluate(() => window.__JX_COMMAND_SEQUENCES__)).toEqual([3, 4]);
  await page.evaluate(() => window.__JX_EMIT_READY_SNAPSHOT__?.());
  const startButton = page.getByRole('button', { name: '开始发言' });
  await expect(startButton).toBeEnabled();
  await startButton.click();
  await expect(page.getByRole('heading', { name: '麦克风已开启' })).toBeVisible();
  await expect(page.getByRole('button', { name: '提前结束发言' })).toBeVisible();
  const humanPresence = page.getByTestId('participant-presence').first();
  await expect(humanPresence).toHaveAttribute('aria-label', '在线');
  await expect(page.getByTestId('participant-presence')).toHaveCount(2);
  await expect
    .poll(async () => {
      const presenceBox = await humanPresence.boundingBox();
      const avatarBox = await humanPresence.locator('..').boundingBox();
      return Boolean(
        presenceBox &&
        avatarBox &&
        presenceBox.x + presenceBox.width > avatarBox.x + avatarBox.width &&
        presenceBox.y + presenceBox.height > avatarBox.y + avatarBox.height,
      );
    })
    .toBe(true);
  await expect(page.locator('.debate-arena > section')).not.toContainText(
    '这是当前正在识别的实时发言',
  );
  await expect(page.locator('.debate-topbar')).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const layout = document.querySelector<HTMLElement>('.debate-page');
        const topic = document.querySelector<HTMLElement>('.debate-topic');
        if (!layout || !topic) return false;
        const layoutBox = layout.getBoundingClientRect();
        const topicBox = topic.getBoundingClientRect();
        return topicBox.top - layoutBox.top <= 9;
      }),
    )
    .toBe(true);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const layout = document.querySelector<HTMLElement>('.debate-page');
        const arena = document.querySelector<HTMLElement>('.debate-arena');
        const controls = document.querySelector<HTMLElement>('.debate-controls');
        if (!layout || !arena || !controls) return false;
        return (
          layout.scrollHeight <= layout.clientHeight &&
          layout.getBoundingClientRect().bottom <= window.innerHeight &&
          arena.getBoundingClientRect().bottom <= window.innerHeight &&
          controls.getBoundingClientRect().bottom <= window.innerHeight &&
          document.documentElement.scrollHeight <= window.innerHeight
        );
      }),
    )
    .toBe(true);

  const finishButton = page.getByRole('button', { name: '提前结束发言' });
  await finishButton.click();
  const staleFinishDialog = page.getByRole('alertdialog', { name: '提前结束本次发言？' });
  await expect(staleFinishDialog).toBeVisible();
  await page.evaluate(() => window.__JX_EMIT_HUMAN_FINISHED_SNAPSHOT__?.());
  await expect(staleFinishDialog).toHaveCount(0);
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('status')).toContainText(
    '比赛状态已变化，未执行该操作',
  );
  await page.evaluate(() => window.__JX_EMIT_READY_SNAPSHOT__?.());
  await page.getByRole('button', { name: '开始发言' }).click();
  await expect(finishButton).toBeVisible();

  await page.evaluate(() => {
    window.__JX_FAIL_NEXT_COMMAND__ = true;
  });
  await finishButton.click();
  const finishDialog = page.getByRole('alertdialog', { name: '提前结束本次发言？' });
  await expect(finishDialog).toBeVisible();
  await finishDialog.getByRole('button', { name: '取消' }).click();
  await expect(finishDialog).toHaveCount(0);
  expect(await page.evaluate(() => window.__JX_FAILED_COMMAND_COUNT__ ?? 0)).toBe(0);

  await finishButton.click();
  await page
    .getByRole('alertdialog', { name: '提前结束本次发言？' })
    .getByRole('button', { name: '结束发言' })
    .evaluate((button) => {
      if (!(button instanceof HTMLButtonElement)) throw new Error('确认控件不是按钮');
      button.click();
      button.click();
    });
  await expect(page.getByRole('region', { name: '操作提示' }).getByRole('alert')).toContainText(
    '比赛指令未执行，请检查实时连接后重试。',
  );
  await expect.poll(() => page.evaluate(() => window.__JX_FAILED_COMMAND_COUNT__ ?? 0)).toBe(1);
  await expect
    .poll(() => page.evaluate(() => window.__JX_MICROPHONE_STATES__?.slice(-2)))
    .toEqual([false, true]);
  await expect.poll(() => page.evaluate(() => window.__JX_MATCH_SOCKET_COUNT__ ?? 0)).toBe(3);
  await expect(page.getByRole('button', { name: '查看网络状态' })).toBeVisible();

  const transcriptButton = page.getByRole('button', { name: '文字记录' });
  if (await transcriptButton.isVisible()) await transcriptButton.click();
  await expect(page.getByText('实时识别文字').last()).toBeVisible();
  const transcriptRegion = page
    .locator(
      '.debate-transcript:visible, [role="dialog"][aria-labelledby="transcript-drawer-title"]',
    )
    .filter({ hasText: '文字记录' })
    .last();
  await expect(transcriptRegion.getByText('这是当前正在识别的实时发言').last()).toBeVisible();
  await expect
    .poll(() =>
      transcriptRegion
        .locator('.overflow-y-auto')
        .evaluate((element) => element.scrollHeight > element.clientHeight),
    )
    .toBe(true);

  const editableSpeech = transcriptRegion.locator(
    '[data-speech-id="80000000-0000-4000-8000-000000000010"]',
  );
  await editableSpeech.getByRole('button', { name: '修改我的文字' }).click();
  const transcriptDraft = editableSpeech.getByRole('textbox', { name: '修改本人发言文字' });
  await transcriptDraft.fill('  修正后的真人发言  ');
  await editableSpeech.getByRole('button', { name: '保存' }).evaluate((button) => {
    if (!(button instanceof HTMLButtonElement)) throw new Error('保存控件不是按钮');
    button.click();
    button.click();
  });
  await expect(
    page
      .getByRole('region', { name: '操作提示' })
      .getByRole('alert')
      .filter({ hasText: '文字服务暂时不可用，请重试' }),
  ).toBeVisible();
  await expect(transcriptDraft).toHaveValue('  修正后的真人发言  ');
  expect(transcriptSaveCount).toBe(1);

  await editableSpeech.getByRole('button', { name: '保存' }).click();
  await expect(
    page
      .getByRole('region', { name: '操作提示' })
      .getByRole('status')
      .filter({ hasText: '文字修改已保存' }),
  ).toBeVisible();
  await expect(editableSpeech).toContainText('修正后的真人发言');
  expect(transcriptSaveCount).toBe(2);

  const closeTranscriptAfterEdit = page.getByRole('button', { name: '关闭文字记录' });
  if (await closeTranscriptAfterEdit.isVisible()) await closeTranscriptAfterEdit.click();

  await page.evaluate(() => window.__JX_EMIT_AGENT_SNAPSHOT__?.());
  await expect(page.getByRole('heading', { name: 'Agent-反2' })).toBeVisible();
  await expect(page.getByText('Agent 正在发言')).toBeVisible();

  await page.evaluate(() => window.__JX_EMIT_COUNTDOWN_SNAPSHOT__?.());
  const countdown = page.getByRole('status', { name: '三秒倒计时' });
  await expect(countdown).toContainText(/^[1-3]$/);
  const firstCountdownValue = Number(await countdown.textContent());
  await expect
    .poll(async () => Number(await countdown.textContent()), { timeout: 1_500 })
    .toBeLessThanOrEqual(firstCountdownValue);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([
    'Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
    'Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
  ]);
});
