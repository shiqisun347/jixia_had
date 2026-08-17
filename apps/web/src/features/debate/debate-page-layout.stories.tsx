import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { HttpResponse, http } from 'msw';
import { useEffect, useState, type ComponentProps } from 'react';
import { expect, userEvent, waitFor, within } from 'storybook/test';

import type { MatchSnapshot, MatchTranscript } from '@/lib/matches-api';
import type { RoomSnapshot } from '@/lib/rooms-api';

import { DebatePageLayout } from './debate-page-layout';

type LayoutProps = ComponentProps<typeof DebatePageLayout>;

function RuntimeModalHarness(args: LayoutProps) {
  const [drawerOpen, setDrawerOpen] = useState(args.drawerOpen);
  const [networkOpen, setNetworkOpen] = useState(Boolean(args.networkOpen));
  const [renderVersion, setRenderVersion] = useState(0);
  useEffect(() => {
    if (!drawerOpen && !networkOpen) return;
    const timer = window.setTimeout(() => setRenderVersion((current) => current + 1), 300);
    return () => window.clearTimeout(timer);
  }, [drawerOpen, networkOpen]);
  return (
    <>
      <output className="sr-only" data-testid="parent-render-version">
        {renderVersion}
      </output>
      <DebatePageLayout
        {...args}
        drawerOpen={drawerOpen}
        networkOpen={networkOpen}
        onCloseDrawer={() => setDrawerOpen(false)}
        onCloseNetwork={() => setNetworkOpen(false)}
        onOpenDrawer={() => setDrawerOpen(true)}
        onOpenNetwork={() => setNetworkOpen(true)}
      />
    </>
  );
}

const room = {
  id: 'room-013',
  code: 'JX8K2M',
  title: '4v4 正式辩论赛',
  status: 'RUNNING',
  organizer_user_id: 'user-013',
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
  members: [],
  seats: [
    {
      id: 'a1',
      side: 'AFFIRMATIVE',
      seat_no: 1,
      occupant_type: 'HUMAN',
      user_id: 'user-013',
      agent_profile_id: null,
      occupant_name: '林知夏',
      occupant_avatar_key: 'human-01',
      occupant_avatar_version: 0,
    },
    {
      id: 'a2',
      side: 'AFFIRMATIVE',
      seat_no: 2,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-a2',
      occupant_name: 'Agent·正二',
      occupant_avatar_key: 'agent-02',
    },
    {
      id: 'a3',
      side: 'AFFIRMATIVE',
      seat_no: 3,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-a3',
      occupant_name: 'Agent·正三',
      occupant_avatar_key: 'agent-03',
    },
    {
      id: 'a4',
      side: 'AFFIRMATIVE',
      seat_no: 4,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-a4',
      occupant_name: 'Agent·正四',
      occupant_avatar_key: 'agent-04',
    },
    {
      id: 'n1',
      side: 'NEGATIVE',
      seat_no: 1,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-n1',
      occupant_name: 'Agent·反一',
      occupant_avatar_key: 'agent-05',
    },
    {
      id: 'n2',
      side: 'NEGATIVE',
      seat_no: 2,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-n2',
      occupant_name: 'Agent·反二',
      occupant_avatar_key: 'agent-06',
    },
    {
      id: 'n3',
      side: 'NEGATIVE',
      seat_no: 3,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-n3',
      occupant_name: 'Agent·反三',
      occupant_avatar_key: 'agent-07',
    },
    {
      id: 'n4',
      side: 'NEGATIVE',
      seat_no: 4,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-n4',
      occupant_name: 'Agent·反四',
      occupant_avatar_key: 'agent-08',
    },
  ],
} as unknown as RoomSnapshot;

const snapshot = (actionState: string, overrides: Partial<MatchSnapshot> = {}) =>
  ({
    match_id: 'match-013',
    room_id: room.id,
    status: 'RUNNING',
    action_state: actionState,
    sequence: 12,
    current_action_index: 2,
    current_action: {
      stage_position: 1,
      action_position: 1,
      action_kind: 'HUMAN_SPEECH',
      duration_seconds: 30,
      side: 'AFFIRMATIVE',
      seat_no: 1,
      speaker_user_id: 'user-013',
      speaker_kind: 'HUMAN',
      host_audio_path: null,
    },
    current_speech_id: 'speech-013',
    current_speaker_user_id: 'user-013',
    current_agent_profile_id: null,
    speech_remaining_ms: 32_000,
    countdown_remaining_ms: null,
    current_speaker_side: 'AFFIRMATIVE',
    current_speaker_seat_no: 1,
    free_holder_side: null,
    free_affirmative_remaining_ms: null,
    free_negative_remaining_ms: null,
    hand_queue: [],
    agent_hand_queue: [],
    agent_selection_mode: null,
    agent_decisions: [],
    team_hand_queue: [],
    hand_window_open: false,
    error_code: null,
    offline_user_id: null,
    ...overrides,
  }) as unknown as MatchSnapshot;

const freeDebateAction = {
  stage_position: 3,
  action_position: 0,
  action_kind: 'FREE_DEBATE',
  duration_seconds: 300,
  side: null,
  seat_no: null,
  speaker_user_id: null,
  speaker_kind: 'HUMAN',
  agent_profile_id: null,
  host_audio_path: null,
} as const;

const transcript = {
  match_id: 'match-013',
  context_version: 4,
  speeches: [
    {
      id: 'speech-final-013',
      action_key: '1:1',
      match_id: 'match-013',
      user_id: 'user-013',
      speaker_kind: 'HUMAN',
      agent_profile_id: null,
      generation_id: null,
      side: 'AFFIRMATIVE',
      seat_no: 1,
      status: 'FINALIZED',
      asr_raw_final_text: '效率与公平需要同时被看见。',
      display_text: '效率与公平需要同时被看见。',
      audio_duration_ms: 10_000,
      finalized_at: new Date().toISOString(),
      audio_truncated: false,
    },
  ],
} as unknown as MatchTranscript;

const baseArgs: LayoutProps = {
  matchId: 'match-013',
  room,
  snapshot: snapshot('HUMAN_READY_TO_START'),
  runtime: {
    socketStatus: 'open',
    socketError: null,
    interimText: '',
    resumeReasons: [],
    commandReady: true,
  },
  presentation: {
    eyebrow: '当前发言席位已就绪',
    title: '轮到你发言了！',
    detail: '点击开始后才会开启麦克风并启动正式计时。',
  },
  transcript,
  transcriptLoading: false,
  transcriptError: false,
  onRetryTranscript: () => undefined,
  currentUserId: 'user-013',
  currentSeat: room.seats[0],
  isCurrentSpeaker: true,
  isOrganizer: true,
  isDebater: true,
  handQueue: [],
  myHandIndex: -1,
  canRaiseHand: false,
  audioStatus: 'ready',
  audioError: null,
  outputMuted: false,
  commandPending: false,
  leaving: false,
  editingSpeechId: null,
  draftText: '',
  savingSpeechId: null,
  drawerOpen: false,
  onCommand: () => undefined,
  onStartSpeech: () => undefined,
  onFinishSpeech: () => undefined,
  onResetSpeech: () => undefined,
  onPause: () => undefined,
  onTerminate: () => undefined,
  onLeave: () => undefined,
  onEnableAudio: () => undefined,
  onToggleOutputMuted: () => undefined,
  onOpenDrawer: () => undefined,
  onCloseDrawer: () => undefined,
  onEditSpeech: () => undefined,
  onSaveSpeech: () => undefined,
  onDraftTextChange: () => undefined,
};

const meta = {
  title: 'Debate/DebatePageLayout',
  component: DebatePageLayout,
  parameters: {
    layout: 'fullscreen',
    msw: {
      handlers: [
        http.get('*/api/users/:userId/avatar', ({ request }) =>
          HttpResponse.redirect(
            new URL('/assets/avatars/human-01.webp', request.url).toString(),
            302,
          ),
        ),
      ],
    },
  },
  args: baseArgs,
} satisfies Meta<typeof DebatePageLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

export const HumanReady: Story = { args: baseArgs };
export const LocalOutputMuted: Story = {
  args: { ...baseArgs, outputMuted: true },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('button', { name: '恢复本机声音' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  },
};
export const HumanSpeaking: Story = {
  args: {
    ...baseArgs,
    snapshot: snapshot('HUMAN_SPEAKING'),
    presentation: {
      eyebrow: '实时发言中',
      title: '麦克风已开启',
      detail: '服务端正在控制发言时长，你可以提前结束。',
    },
    runtime: { ...baseArgs.runtime, interimText: '这是当前正在识别的实时发言' },
  },
};
export const AgentThinking: Story = {
  args: {
    ...baseArgs,
    currentSeat: room.seats[4],
    isCurrentSpeaker: false,
    presentation: {
      eyebrow: 'Agent 准备中',
      title: 'Agent 正在思考中',
      detail: '正在生成正式发言并建立实时语音。',
    },
    snapshot: snapshot('AGENT_PREPARING', {
      current_speaker_user_id: null,
      current_agent_profile_id: 'agent-n1',
      current_speaker_side: 'NEGATIVE',
      current_speaker_seat_no: 1,
    }),
  },
};
export const AgentSpeaking: Story = {
  args: {
    ...AgentThinking.args,
    presentation: {
      eyebrow: 'Agent 实时发言',
      title: 'Agent 正在发言',
      detail: '语音正在实时播放，文字记录同步更新。',
    },
    snapshot: snapshot('AGENT_SPEAKING', {
      current_speaker_user_id: null,
      current_agent_profile_id: 'agent-n2',
      current_speaker_side: 'NEGATIVE',
      current_speaker_seat_no: 2,
    }),
    currentSeat: room.seats[5],
    runtime: { ...baseArgs.runtime, interimText: 'Agent 的实时播放文字只出现在文字记录中。' },
  },
};
export const TranscriptReadingOrder: Story = {
  render: (args) => <RuntimeModalHarness {...args} />,
  args: {
    ...baseArgs,
    drawerOpen: true,
    currentSeat: undefined,
    isCurrentSpeaker: false,
    runtime: { ...baseArgs.runtime, interimText: '这段内容正在实时识别，应该固定在记录底部。' },
    snapshot: snapshot('HUMAN_SPEAKING', {
      current_speaker_user_id: 'user-013',
      current_speaker_side: 'AFFIRMATIVE',
      current_speaker_seat_no: 1,
    }),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const page = within(document.body);
    const dialog = page.getByRole('dialog', { name: '文字记录' });
    await expect(within(dialog).getByText('正方一辩立论')).toBeVisible();
    await expect(within(dialog).getByText('正在进行 · 正方 1 辩')).toBeVisible();
    await expect(within(dialog).getByRole('button', { name: '复制全部记录' })).toBeVisible();
    const closeButton = within(dialog).getByRole('button', { name: '关闭文字记录' });
    await expect(closeButton).toHaveFocus();
    const editButton = within(dialog).getByRole('button', { name: '修改我的文字' });
    const renderVersion = canvas.getByTestId('parent-render-version');
    const initialRenderVersion = Number(renderVersion.textContent);
    editButton.focus();
    await waitFor(() => {
      expect(Number(renderVersion.textContent)).toBeGreaterThan(initialRenderVersion);
    });
    await expect(editButton).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    await expect(page.queryByRole('dialog', { name: '文字记录' })).not.toBeInTheDocument();
    await expect(canvas.getByRole('button', { name: '文字记录' })).toHaveFocus();
  },
};

export const NetworkModalFocus: Story = {
  render: (args) => <RuntimeModalHarness {...args} />,
  args: {
    ...baseArgs,
    networkOpen: true,
    networkStats: {
      rttMs: 42,
      packetLossPercent: 0.3,
      sampledAt: Date.parse('2026-08-14T12:00:00+08:00'),
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const page = within(document.body);
    const dialog = page.getByRole('dialog', { name: '网络状态' });
    const closeButton = within(dialog).getByRole('button', { name: '关闭网络状态' });
    await expect(closeButton).toHaveFocus();
    const renderVersion = canvas.getByTestId('parent-render-version');
    const initialRenderVersion = Number(renderVersion.textContent);
    await waitFor(() => {
      expect(Number(renderVersion.textContent)).toBeGreaterThan(initialRenderVersion);
    });
    await expect(closeButton).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    await expect(page.queryByRole('dialog', { name: '网络状态' })).not.toBeInTheDocument();
    await expect(canvas.getByRole('button', { name: '查看网络状态' })).toHaveFocus();
  },
};

export const LongTranscriptScroll: Story = {
  args: {
    ...baseArgs,
    drawerOpen: true,
    transcript: {
      ...transcript,
      speeches: Array.from({ length: 14 }, (_, index) => ({
        ...transcript.speeches[0],
        id: `speech-long-${index + 1}`,
        action_key: `${(index % 3) + 1}:${index + 1}`,
        user_id: index % 2 === 0 ? 'user-013' : null,
        speaker_kind: index % 2 === 0 ? 'HUMAN' : 'AGENT',
        side: index % 2 === 0 ? 'AFFIRMATIVE' : 'NEGATIVE',
        seat_no: (index % 4) + 1,
        display_text: `第 ${index + 1} 段正式发言。这里使用足够长的辩论文字验证抽屉内部滚动，而不是推动整个比赛页面。`,
      })),
    } as unknown as MatchTranscript,
    runtime: {
      ...baseArgs.runtime,
      interimText: '这是当前正在识别的最新发言，应始终位于全部正式发言之后。',
    },
  },
  async play() {
    const page = within(document.body);
    const scrollRegion = page.getByTestId('transcript-drawer-scroll');
    await expect(scrollRegion.scrollHeight).toBeGreaterThan(scrollRegion.clientHeight);
    await expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(window.innerHeight + 1);
    const drawer = within(scrollRegion);
    const finalSpeech = drawer.getByText(/第 14 段正式发言/);
    const interim = drawer.getByText(/这是当前正在识别的最新发言/);
    await expect(
      finalSpeech.compareDocumentPosition(interim) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  },
};
export const StartCountdown: Story = {
  args: {
    ...baseArgs,
    snapshot: snapshot('NOT_STARTED', {
      status: 'START_COUNTDOWN',
      countdown_remaining_ms: 2_800,
      current_speaker_user_id: null,
      current_speaker_side: null,
      current_speaker_seat_no: null,
    }),
    currentSeat: undefined,
    isCurrentSpeaker: false,
    presentation: {
      eyebrow: '比赛启动',
      title: '比赛即将开始',
      detail: '请保持设备连接。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('开始倒计时')).toBeVisible();
    await expect(canvas.queryByText('START_COUNTDOWN')).not.toBeInTheDocument();
  },
};
export const HostAnnouncing: Story = {
  args: {
    ...baseArgs,
    currentSeat: undefined,
    snapshot: snapshot('HOST_ANNOUNCING', {
      current_action: {
        stage_position: 2,
        action_position: 0,
        action_kind: 'HOST_AUDIO',
        duration_seconds: 0,
        side: null,
        seat_no: null,
        speaker_user_id: null,
        speaker_kind: 'HUMAN',
        agent_profile_id: null,
        host_audio_path: '/api/matches/match-013/host-audio/2:0',
      },
      current_speaker_user_id: 'user-013',
      current_speaker_side: 'AFFIRMATIVE',
      current_speaker_seat_no: 1,
      speech_remaining_ms: 28_000,
    }),
    presentation: {
      eyebrow: '赛制播报',
      title: '主持音频播放中',
      detail: '播报结束后进入当前阶段。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const centerStage = canvas.getByTestId('debate-center-stage');
    const hostState = within(centerStage).getByTestId('host-announcement-state');
    await expect(hostState).toBeVisible();
    await expect(within(hostState).getByText('主持播报中')).toBeVisible();
    await expect(within(centerStage).queryByText('林知夏')).not.toBeInTheDocument();
    await expect(within(centerStage).queryByText('发言计时')).not.toBeInTheDocument();
    await expect(canvasElement.querySelector('.jx-active-seat-shadow')).not.toBeInTheDocument();
    await expect(canvas.getByTestId('current-debate-stage')).toHaveTextContent('反方一辩立论');
  },
};
export const FreeDebateHands: Story = {
  args: {
    ...baseArgs,
    canRaiseHand: true,
    myHandIndex: 0,
    handQueue: ['user-013'],
    snapshot: snapshot('FREE_SELECTING', {
      current_action: freeDebateAction,
      hand_queue: ['user-013'],
      team_hand_queue: [
        {
          speaker_kind: 'HUMAN',
          user_id: 'user-013',
          agent_profile_id: null,
          side: 'AFFIRMATIVE',
          seat_no: 1,
          rank: 1,
        },
      ],
      hand_window_open: true,
      free_holder_side: 'AFFIRMATIVE',
      free_affirmative_remaining_ms: 138_000,
      free_negative_remaining_ms: 162_000,
    }),
    presentation: {
      eyebrow: '自由辩论候选中',
      title: '申请下一次发言',
      detail: '人类举手优先；无人举手时由本方 Agent 决策。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('current-debate-stage')).toHaveTextContent('当前阶段');
    await expect(canvas.getByTestId('match-controls-audio')).toBeVisible();
    await expect(canvas.getByTestId('match-controls-speech')).toBeVisible();
    await expect(canvas.getByTestId('match-controls-system')).toBeVisible();
    await expect(canvas.getByRole('progressbar', { name: '正方自由辩论剩余时间' })).toHaveAttribute(
      'aria-valuenow',
      '46',
    );
    await expect(canvas.getByRole('progressbar', { name: '反方自由辩论剩余时间' })).toHaveAttribute(
      'aria-valuenow',
      '54',
    );
  },
};

export const FreeDebateAgentDecisionProgress: Story = {
  args: {
    ...baseArgs,
    currentSeat: undefined,
    isCurrentSpeaker: false,
    snapshot: snapshot('FREE_SELECTING', {
      current_action: freeDebateAction,
      current_speech_id: null,
      current_speaker_user_id: null,
      current_agent_profile_id: null,
      current_speaker_side: null,
      current_speaker_seat_no: null,
      speech_remaining_ms: null,
      hand_window_open: true,
      free_holder_side: 'NEGATIVE',
      free_affirmative_remaining_ms: 138_000,
      free_negative_remaining_ms: 162_000,
      agent_hand_queue: ['agent-n2'],
      agent_decisions: [
        {
          agent_profile_id: 'agent-n1',
          side: 'NEGATIVE',
          seat_no: 1,
          status: 'DECIDING',
          queue_rank: null,
        },
        {
          agent_profile_id: 'agent-n2',
          side: 'NEGATIVE',
          seat_no: 2,
          status: 'HAND',
          queue_rank: 1,
        },
        {
          agent_profile_id: 'agent-n3',
          side: 'NEGATIVE',
          seat_no: 3,
          status: 'SKIP',
          queue_rank: null,
        },
      ],
      team_hand_queue: [
        {
          speaker_kind: 'AGENT',
          user_id: null,
          agent_profile_id: 'agent-n2',
          side: 'NEGATIVE',
          seat_no: 2,
          rank: 1,
        },
      ],
    }),
    presentation: {
      eyebrow: 'Agent 决策实时同步',
      title: '本方正在形成举手队列',
      detail: '各 Agent 独立返回结果，最终在申请窗口关闭后锁定队列。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText('Agent 决策中')).toBeVisible();
    await expect(canvas.getByLabelText('Agent 举手第 1 名')).toBeVisible();
    await expect(canvas.getByText('跳过')).toBeVisible();
  },
};

export const FreeDebateAgentVolunteers: Story = {
  args: {
    ...baseArgs,
    currentSeat: room.seats[4],
    isCurrentSpeaker: false,
    agentHandQueue: ['agent-n1', 'agent-n2'],
    snapshot: snapshot('AGENT_PREPARING', {
      current_action: freeDebateAction,
      current_speaker_user_id: null,
      current_agent_profile_id: 'agent-n1',
      current_speaker_side: 'NEGATIVE',
      current_speaker_seat_no: 1,
      free_holder_side: 'NEGATIVE',
      agent_hand_queue: ['agent-n1', 'agent-n2'],
      agent_selection_mode: 'VOLUNTEER',
      agent_decisions: [
        {
          agent_profile_id: 'agent-n1',
          side: 'NEGATIVE',
          seat_no: 1,
          status: 'HAND',
          queue_rank: 1,
        },
        {
          agent_profile_id: 'agent-n2',
          side: 'NEGATIVE',
          seat_no: 2,
          status: 'HAND',
          queue_rank: 2,
        },
      ],
      team_hand_queue: [
        {
          speaker_kind: 'AGENT',
          user_id: null,
          agent_profile_id: 'agent-n1',
          side: 'NEGATIVE',
          seat_no: 1,
          rank: 1,
        },
        {
          speaker_kind: 'AGENT',
          user_id: null,
          agent_profile_id: 'agent-n2',
          side: 'NEGATIVE',
          seat_no: 2,
          rank: 2,
        },
      ],
    }),
    presentation: {
      eyebrow: 'Agent 已完成决策',
      title: 'Agent 正在准备发言',
      detail: '第一位 Agent 将发言，其他主动举手顺序已同步到席位卡。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('current-debate-stage')).toHaveTextContent('自由辩论');
    await expect(canvas.getByLabelText('Agent 举手第 1 名')).toHaveTextContent('将发言');
  },
};

export const FreeDebateAgentFallback: Story = {
  args: {
    ...baseArgs,
    currentSeat: room.seats[4],
    isCurrentSpeaker: false,
    snapshot: snapshot('AGENT_PREPARING', {
      current_action: freeDebateAction,
      current_speaker_user_id: null,
      current_agent_profile_id: 'agent-n1',
      current_speaker_side: 'NEGATIVE',
      current_speaker_seat_no: 1,
      free_holder_side: 'NEGATIVE',
      agent_hand_queue: [],
      agent_selection_mode: 'FALLBACK',
      agent_decisions: [
        {
          agent_profile_id: 'agent-n1',
          side: 'NEGATIVE',
          seat_no: 1,
          status: 'SKIP',
          queue_rank: null,
        },
        {
          agent_profile_id: 'agent-n2',
          side: 'NEGATIVE',
          seat_no: 2,
          status: 'SKIP',
          queue_rank: null,
        },
      ],
    }),
    presentation: {
      eyebrow: '无人主动申请',
      title: 'AI 补位发言',
      detail: '本方 Agent 均判断暂不发言，系统按确定性顺序补位。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('current-debate-stage')).toHaveTextContent('自由辩论');
    await expect(canvas.getByText('AI 补位')).toBeVisible();
  },
};

export const Paused: Story = {
  args: {
    ...baseArgs,
    snapshot: snapshot('RECOVERY_REQUIRED', {
      status: 'PAUSED',
      error_code: 'PLAYER_OFFLINE_TIMEOUT',
    }),
    runtime: { ...baseArgs.runtime, socketError: '恢复条件未满足：辩手 林知夏 当前离线' },
    presentation: {
      eyebrow: '系统恢复保护',
      title: '比赛已安全暂停',
      detail: '计时与实时语音均已冻结；满足在线和设备条件后可以申请恢复。',
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('runtime-frozen-state')).toBeVisible();
    await expect(canvas.queryByText('发言计时')).not.toBeInTheDocument();
    await expect(canvas.queryByText('个人剩余时间')).not.toBeInTheDocument();
    await expect(canvasElement.querySelector('.jx-active-seat-shadow')).not.toBeInTheDocument();
  },
};
export const ResumeCountdown: Story = {
  args: {
    ...baseArgs,
    snapshot: snapshot('RESUME_COUNTDOWN', { status: 'PAUSED', countdown_remaining_ms: 2_400 }),
    presentation: {
      eyebrow: '恢复条件已满足',
      title: '比赛即将恢复',
      detail: '预设提示播放完成后，服务端将在三秒倒计时结束时恢复比赛。',
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('恢复倒计时')).toBeVisible();
    await expect(canvas.getByText('恢复倒计时进行中')).toBeVisible();
    await expect(canvas.queryByRole('button', { name: '申请恢复' })).not.toBeInTheDocument();
  },
};
export const Terminated: Story = {
  args: {
    ...baseArgs,
    snapshot: snapshot('MATCH_FINISHED', {
      status: 'TERMINATED',
      current_speaker_user_id: null,
      current_speaker_side: null,
      current_speaker_seat_no: null,
    }),
    currentSeat: undefined,
    isCurrentSpeaker: false,
    audioStatus: 'ready',
    presentation: {
      eyebrow: '比赛终止',
      title: '本场比赛已终止',
      detail: '比赛已由房主终止，不能继续发言；现有文字记录仍可查看。',
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('本场比赛已终止')).toBeVisible();
    await expect(canvas.getByRole('link', { name: /查看终止记录/ })).toBeVisible();
    await expect(canvas.queryByRole('button', { name: /开启比赛声音/ })).not.toBeInTheDocument();
  },
};
export const SystemError: Story = {
  args: {
    ...baseArgs,
    snapshot: snapshot('RECOVERY_REQUIRED', {
      status: 'ERROR',
      error_code: 'REALTIME_SERVICE_FAILED',
    }),
    runtime: { ...baseArgs.runtime, socketError: '实时语音服务重试失败，比赛已安全暂停。' },
    presentation: {
      eyebrow: '实时服务异常',
      title: '比赛已安全暂停',
      detail: '检查在线状态后，可以重新申请恢复比赛。',
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('runtime-frozen-state')).toBeVisible();
    await expect(canvas.queryByText('发言计时')).not.toBeInTheDocument();
    await expect(canvas.queryByText('允许发言时长')).not.toBeInTheDocument();
    await expect(canvas.queryByRole('link', { name: '重新检测' })).not.toBeInTheDocument();
    await expect(canvasElement.querySelector('.jx-active-seat-shadow')).not.toBeInTheDocument();
  },
};
export const SpectatorTranscript: Story = {
  args: {
    ...baseArgs,
    isOrganizer: false,
    isDebater: false,
    isCurrentSpeaker: false,
    drawerOpen: true,
  },
  async play() {
    const page = within(document.body);
    const dialog = page.getByRole('dialog', { name: '文字记录' });
    await expect(within(dialog).getByText('文字记录')).toBeVisible();
    await expect(within(dialog).getByText('效率与公平需要同时被看见。')).toBeVisible();
  },
};
