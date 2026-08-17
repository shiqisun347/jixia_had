import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { http, HttpResponse } from 'msw';
import { expect, userEvent, waitFor, within } from 'storybook/test';

import { CreateRoomPage, JoinRoomPage, LobbyPage, RoomPage } from './index';

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  username: 'debater_demo',
  real_name: '林知行',
  role: 'USER',
  must_change_password: false,
  avatar_version: 0,
};

const rule = {
  id: '20000000-0000-4000-8000-000000000001',
  rule_key: 'new-debate',
  version: 1,
  name: '经典线性赛制',
  description: '立论、质询、自由辩论与总结。',
  side_size: 2,
  estimated_seconds: 1800,
  status: 'ENABLED',
  audio_reviewed_at: '2026-08-03T08:00:00Z',
};

const formal4v4Rule = {
  ...rule,
  id: '20000000-0000-4000-8000-000000000004',
  rule_key: 'formal-4v4',
  name: '4v4 正式辩论赛',
  side_size: 4,
};

const topic = {
  id: '30000000-0000-4000-8000-000000000001',
  topic_key: 'ai-creator',
  version: 1,
  title: 'AI 的迅猛发展提升了还是降低了人类创作者存在的意义',
  affirmative_text: '提升了人类创作者存在的意义',
  negative_text: '降低了人类创作者存在的意义',
  status: 'ENABLED',
};

const roomId = '40000000-0000-4000-8000-000000000001';
const room = {
  id: roomId,
  code: 'JX8K2M',
  title: '春季人机辩论实验',
  label: '训练赛',
  status: 'WAITING',
  auto_fill_agents: true,
  organizer_user_id: user.id,
  is_all_agent: false,
  sequence: 3,
  topic: { title: topic.title },
  rule: { name: rule.name, side_size: 2 },
  match_id: null,
  viewer_membership_state: 'ACTIVE',
  viewer_member_role: 'DEBATER',
  viewer_ready: false,
  latest_device_check: null,
  members: [
    {
      user_id: user.id,
      member_role: 'DEBATER',
      online: true,
      ready: false,
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
      side: 'AFFIRMATIVE',
      seat_no: 2,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: '60000000-0000-4000-8000-000000000002',
      occupant_name: '坤元',
      occupant_avatar_key: 'agent-02',
    },
    {
      id: '50000000-0000-4000-8000-000000000003',
      side: 'NEGATIVE',
      seat_no: 1,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: '60000000-0000-4000-8000-000000000001',
      occupant_name: '乾元',
      occupant_avatar_key: 'agent-01',
    },
    {
      id: '50000000-0000-4000-8000-000000000004',
      side: 'NEGATIVE',
      seat_no: 2,
      occupant_type: 'EMPTY',
      user_id: null,
      agent_profile_id: null,
      occupant_name: null,
    },
  ],
};

const mixed4v4Room = {
  ...room,
  title: '4v4 人机混合辩论准备场',
  sequence: 8,
  rule: { name: formal4v4Rule.name, side_size: 4 },
  viewer_member_role: 'ORGANIZER',
  members: room.members.map((member) => ({ ...member, member_role: 'ORGANIZER' })),
  seats: (['AFFIRMATIVE', 'NEGATIVE'] as const).flatMap((side, sideIndex) =>
    Array.from({ length: 4 }, (_, index) => {
      const seatNo = index + 1;
      const own = side === 'AFFIRMATIVE' && seatNo === 1;
      const avatarNo = sideIndex * 4 + seatNo;
      return {
        id: `50000000-0000-4000-8${sideIndex}00-00000000000${seatNo}`,
        side,
        seat_no: seatNo,
        occupant_type: own ? 'HUMAN' : 'AGENT',
        user_id: own ? user.id : null,
        agent_profile_id: own ? null : `60000000-0000-4000-8${sideIndex}00-00000000000${seatNo}`,
        occupant_name: own
          ? user.real_name
          : `Agent-${side === 'AFFIRMATIVE' ? '正' : '反'}${seatNo}`,
        occupant_avatar_key: own ? 'human-01' : `agent-${String(avatarNo).padStart(2, '0')}`,
        occupant_avatar_version: own ? 0 : null,
        occupant_has_custom_avatar: false,
      };
    }),
  ),
};

const authenticated = http.get('*/api/auth/me', () => HttpResponse.json({ user }));
const userAvatar = http.get('*/api/users/:userId/avatar', ({ request }) =>
  HttpResponse.redirect(new URL('/assets/avatars/human-01.webp', request.url).toString(), 302),
);
const terms = http.get('*/api/legal/human-participation/current', () =>
  HttpResponse.json({ version: 'human-participation-v1', title: '参赛说明', body: '参赛说明正文' }),
);
const lobby = http.get('*/api/lobby/rooms', () =>
  HttpResponse.json([
    {
      id: roomId,
      code: room.code,
      title: room.title,
      label: room.label,
      status: room.status,
      auto_fill_agents: true,
      topic_title: topic.title,
      rule_name: rule.name,
      side_size: 2,
      occupied_seats: 3,
      spectator_count: 1,
      spectator_remaining: 9,
      spectator_capacity_full: false,
      match_id: null,
      viewer_membership_state: 'ACTIVE',
      viewer_member_role: 'DEBATER',
      viewer_ready: false,
    },
  ]),
);
const catalog = http.get('*/api/lobby/catalog', () =>
  HttpResponse.json({
    voices: [],
    models: [],
    agents: [
      {
        id: '60000000-0000-4000-8000-000000000001',
        name: '乾元',
        status: 'ENABLED',
        model_profile_id: '70000000-0000-4000-8000-000000000001',
        voice_profile_id: '80000000-0000-4000-8000-000000000001',
      },
      {
        id: '60000000-0000-4000-8000-000000000002',
        name: '坤元',
        status: 'ENABLED',
        model_profile_id: '70000000-0000-4000-8000-000000000001',
        voice_profile_id: '80000000-0000-4000-8000-000000000002',
      },
    ],
    topics: [topic],
    rules: [rule, formal4v4Rule],
  }),
);
const snapshot = http.get('*/api/rooms/:roomId/snapshot', () => HttpResponse.json(room));
const noSeatSwapRequests = http.get('*/api/rooms/:roomId/seat-swap-requests', () =>
  HttpResponse.json([]),
);

const meta = {
  title: 'Rooms/004 Flows',
  parameters: {
    layout: 'fullscreen',
    nextjs: { appDirectory: true },
    msw: { handlers: [authenticated, terms] },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const PublicLobby: Story = {
  render: () => <LobbyPage />,
  parameters: { msw: { handlers: [authenticated, lobby] } },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: '公开大厅' })).toBeVisible();
    await expect(await canvas.findByText('春季人机辩论实验')).toBeVisible();
    await expect(await canvas.findByText('输入邀请中的 6 位数字房间号。')).toBeVisible();
  },
};

export const PublicLobbySyncInterrupted: Story = {
  render: () => <LobbyPage />,
  parameters: {
    msw: {
      handlers: [
        authenticated,
        http.get('*/api/lobby/rooms', () =>
          HttpResponse.json(
            { error: { code: 'service_unavailable', message: '大厅暂时不可用，请稍后重试' } },
            { status: 503 },
          ),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('button', { name: '同步中断 · 重新同步' })).toBeVisible();
    await expect(await canvas.findByText('大厅暂时没有加载出来')).toBeVisible();
  },
};

export const CreateRoom: Story = {
  render: () => <CreateRoomPage />,
  parameters: { msw: { handlers: [authenticated, terms, catalog] } },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: '创建一场辩论' })).toBeVisible();
    await waitFor(async () => {
      await expect(await canvas.findByLabelText('赛制')).toHaveValue(formal4v4Rule.id);
    });
    await expect(canvas.queryByRole('option', { name: '选择已启用赛制' })).not.toBeInTheDocument();
    await expect(
      await canvas.findByText('先确定赛制和辩题。系统会填满空席，创建后再选择你的辩手席位。'),
    ).toBeVisible();
    await expect(canvas.queryByText('我的席位')).not.toBeInTheDocument();
    await userEvent.type(await canvas.findByLabelText('比赛名称'), '缺少辩题的测试');
    await userEvent.click(await canvas.findByRole('button', { name: '创建并进入房间' }));
    const topic = await canvas.findByRole('combobox', { name: /选择辩题/ });
    await expect(topic).toHaveAttribute('aria-invalid', 'true');
    const topicError = await canvas.findByRole('alert');
    await expect(topicError).toHaveTextContent('请选择辩题');
    await expect(topicError).toBeVisible();
  },
};

export const WaitingRoom: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: { handlers: [authenticated, terms, userAvatar, snapshot, noSeatSwapRequests] },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: room.title })).toBeVisible();
    await expect(await canvas.findByText('设备检测与准备')).toBeVisible();
    await expect((await canvas.findAllByText(/乾元/))[0]).toBeVisible();
    await expect((await canvas.findAllByText(/坤元/))[0]).toBeVisible();
  },
};

export const Mixed4v4Preparation: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: {
      handlers: [
        authenticated,
        terms,
        userAvatar,
        noSeatSwapRequests,
        http.get('*/api/rooms/:roomId/snapshot', () => HttpResponse.json(mixed4v4Room)),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: mixed4v4Room.title })).toBeVisible();
    await expect(
      await canvas.findAllByRole('button', { name: /[正反]方 [1-4] 辩，/ }),
    ).toHaveLength(8);
    await expect(await canvas.findByRole('button', { name: /开始设备检测/ })).toBeVisible();
    await expect(canvas.getByRole('button', { name: '开始比赛' })).toBeDisabled();
    await expect(canvas.getByText('林知行尚未完成设备检测与准备')).toBeVisible();
  },
};

export const InviteExpandedAfterCreation: Story = {
  render: () => <RoomPage created roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: { handlers: [authenticated, terms, userAvatar, snapshot, noSeatSwapRequests] },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('region', { name: '邀请加入' })).toBeVisible();
    expect((await canvas.findByLabelText('邀请链接')).getAttribute('value')).toMatch(
      /\/join\/JX8K2M$/,
    );
  },
};

export const OneClickDevicePass: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: {
      handlers: [
        authenticated,
        terms,
        userAvatar,
        snapshot,
        noSeatSwapRequests,
        http.post('*/api/rooms/:roomId/device-check', () =>
          HttpResponse.json({
            ...room,
            sequence: 4,
            latest_device_check: {
              check_version: 1,
              status: 'PASS',
              checked_at: '2026-08-11T09:00:00Z',
              valid_until: '2099-08-11T09:30:00Z',
              is_valid: true,
            },
          }),
        ),
        http.post('*/api/rooms/:roomId/ready', () =>
          HttpResponse.json({
            ...room,
            sequence: 5,
            viewer_ready: true,
            members: room.members.map((member) => ({ ...member, ready: true })),
          }),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    window.__JX_DEVICE_PROBE_OVERRIDE__ = async () => ({
      status: 'PASS',
      rttP95Ms: 76,
      packetLossP95Percent: 0.4,
      connectionQuality: 'excellent',
      samples: 6,
      inputPeak: 0.08,
      recordingBlob: new Blob(['storybook-probe'], { type: 'audio/webm' }),
    });
    const canvas = within(canvasElement);
    await (await canvas.findByRole('button', { name: /开始设备检测/ })).click();
    await expect((await canvas.findAllByText('已准备', { exact: true }))[0]).toBeVisible();
  },
};

export const DeviceWarningNeedsConfirmation: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: { handlers: [authenticated, terms, userAvatar, snapshot, noSeatSwapRequests] },
  },
  async play({ canvasElement }) {
    window.__JX_DEVICE_PROBE_OVERRIDE__ = async () => ({
      status: 'WARN',
      rttP95Ms: 260,
      packetLossP95Percent: 4.2,
      connectionQuality: 'good',
      samples: 6,
      inputPeak: 0.04,
      recordingBlob: new Blob(['storybook-probe'], { type: 'audio/webm' }),
    });
    const canvas = within(canvasElement);
    await (await canvas.findByRole('button', { name: /开始设备检测/ })).click();
    await expect(await canvas.findByText('网络指标存在波动，可能影响实时语音。')).toBeVisible();
    await expect(canvas.getByText('下一步：确认网络提示并完成准备')).toBeVisible();
    await expect(canvas.getByRole('button', { name: '确认网络警告并准备' })).toBeVisible();
  },
};

export const DeviceFailureCanRetry: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: { handlers: [authenticated, terms, userAvatar, snapshot, noSeatSwapRequests] },
  },
  async play({ canvasElement }) {
    window.__JX_DEVICE_PROBE_OVERRIDE__ = async () => ({
      status: 'FAIL',
      rttP95Ms: 480,
      packetLossP95Percent: 9.1,
      connectionQuality: 'poor',
      samples: 6,
      inputPeak: 0.001,
    });
    const canvas = within(canvasElement);
    await (await canvas.findByRole('button', { name: /开始设备检测/ })).click();
    await expect(await canvas.findByText(/检测未通过。请确认麦克风有声音/)).toBeVisible();
    await expect(await canvas.findByRole('button', { name: /重新检测/ })).toBeVisible();
  },
};

export const InvalidInviteCode: Story = {
  render: () => <JoinRoomPage roomCode="BAD" />,
  parameters: {
    msw: {
      handlers: [
        authenticated,
        http.get('*/api/rooms/lookup', () =>
          HttpResponse.json(
            { error: { code: 'room_code_invalid', message: '请输入 6 位有效房间号' } },
            { status: 422 },
          ),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: '无法进入这个房间' })).toBeVisible();
    await expect(canvas.getByText('加入失败')).toBeVisible();
    await expect(canvas.queryByText('BAD')).not.toBeInTheDocument();
  },
};

export const ReenteredWithValidDeviceCheck: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: {
      handlers: [
        authenticated,
        terms,
        userAvatar,
        noSeatSwapRequests,
        http.get('*/api/rooms/:roomId/snapshot', () =>
          HttpResponse.json({
            ...room,
            latest_device_check: {
              check_version: 7,
              status: 'PASS',
              checked_at: '2026-08-10T09:00:00Z',
              valid_until: '2099-08-10T09:30:00Z',
              is_valid: true,
            },
          }),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('上次检测仍有效')).toBeVisible();
    await expect(await canvas.findByRole('button', { name: '直接使用检测并准备' })).toBeVisible();
  },
};

export const RunningRoomSpectatorGateway: Story = {
  render: () => <RoomPage roomId={roomId} />,
  parameters: {
    toastPath: '/rooms/story',
    msw: {
      handlers: [
        authenticated,
        terms,
        noSeatSwapRequests,
        http.get('*/api/rooms/:roomId/snapshot', () =>
          HttpResponse.json({
            ...room,
            status: 'RUNNING',
            match_id: '90000000-0000-4000-8000-000000000001',
            viewer_membership_state: 'NONE',
            viewer_member_role: null,
            members: [],
            seats: room.seats.map((seat) =>
              seat.user_id === user.id
                ? {
                    ...seat,
                    occupant_type: 'AGENT',
                    user_id: null,
                    agent_profile_id: '60000000-0000-4000-8000-000000000003',
                    occupant_name: '明辨',
                    occupant_avatar_key: 'agent-03',
                  }
                : seat,
            ),
          }),
        ),
        http.post('*/api/rooms/:roomId/join', () =>
          HttpResponse.json({
            ...room,
            status: 'RUNNING',
            match_id: '90000000-0000-4000-8000-000000000001',
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
          }),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: room.title })).toBeVisible();
    await expect(await canvas.findByRole('button', { name: '作为观众进入比赛' })).toBeVisible();
  },
};
