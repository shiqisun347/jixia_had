import { cleanup, render, screen, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import type { MatchSnapshot } from '@/lib/matches-api';
import type { RoomSnapshot } from '@/lib/rooms-api';

import { DebatePageLayout } from './debate-page-layout';

type Props = ComponentProps<typeof DebatePageLayout>;
const noop = () => undefined;

export function stageFixture(overrides: Partial<MatchSnapshot> = {}): Props {
  const room = {
    id: 'room-stage',
    code: '123456',
    topic: { title: '效率与公平如何平衡', affirmative_text: '效率优先', negative_text: '公平优先' },
    rule: { name: 'YXH', side_size: 4, stages: [{ position: 1, name: '立论' }] },
    members: [],
    seats: ['AFFIRMATIVE', 'NEGATIVE'].flatMap((side) =>
      [1, 2, 3, 4].map((seat_no) => ({
        id: `${side}-${seat_no}`,
        side,
        seat_no,
        occupant_type: 'HUMAN',
        user_id: `${side}-${seat_no}`,
        agent_profile_id: null,
        occupant_name: `${side === 'AFFIRMATIVE' ? '正' : '反'}方${seat_no}辩`,
      })),
    ),
  } as unknown as RoomSnapshot;
  const snapshot = {
    match_id: 'match-stage',
    status: 'RUNNING',
    action_state: 'HUMAN_SPEAKING',
    current_action: { stage_position: 1, action_kind: 'HUMAN_SPEECH', duration_seconds: 30 },
    speech_remaining_ms: 12000,
    countdown_remaining_ms: null,
    free_affirmative_remaining_ms: null,
    free_negative_remaining_ms: null,
    free_holder_side: null,
    team_hand_queue: [],
    agent_decisions: [],
    ...overrides,
  } as unknown as MatchSnapshot;
  return {
    matchId: 'match-stage',
    room,
    snapshot,
    runtime: {
      socketStatus: 'open',
      socketError: null,
      interimText: '',
      resumeReasons: [],
      commandReady: true,
    },
    presentation: {
      title: '本场辩论已完成',
      eyebrow: '比赛状态',
      detail: '查看本场记录与评分进度。',
    },
    currentUserId: 'viewer',
    currentSeat: room.seats[0],
    isCurrentSpeaker: false,
    isOrganizer: false,
    isDebater: false,
    handQueue: [],
    myHandIndex: -1,
    canRaiseHand: false,
    audioStatus: 'ready',
    playbackStatus: 'ready',
    audioError: null,
    outputMuted: false,
    mutedHumanUserIds: [],
    commandPending: false,
    leaving: false,
    editingSpeechId: null,
    draftText: '',
    savingSpeechId: null,
    drawerOpen: false,
    transcript: undefined,
    transcriptLoading: false,
    transcriptError: false,
    onRetryTranscript: noop,
    onCommand: noop,
    onStartSpeech: noop,
    onFinishSpeech: noop,
    onResetSpeech: noop,
    onPause: noop,
    onTerminate: noop,
    onLeave: noop,
    onEnableAudio: noop,
    onToggleOutputMuted: noop,
    onToggleAllHumanOutputMuted: noop,
    onToggleHumanOutputMuted: noop,
    onOpenDrawer: noop,
    onCloseDrawer: noop,
    onEditSpeech: noop,
    onSaveSpeech: noop,
    onDraftTextChange: noop,
  };
}

afterEach(cleanup);

describe('opening and host presentation', () => {
  it.each([false, true])(
    'shows the opening without changing organizer rights (%s)',
    (isOrganizer) => {
      const args = stageFixture({ status: 'START_PENDING_RUNTIME', current_action: null });
      render(<DebatePageLayout {...args} isOrganizer={isOrganizer} />);
      const center = within(screen.getByTestId('debate-center-stage'));
      expect(center.getByRole('heading', { level: 2, name: '开场介绍' })).toBeVisible();
      expect(center.getByRole('heading', { name: '欢迎参加人机辩论赛！' })).toBeVisible();
      expect(center.getByText('请主持人进行比赛介绍')).toBeVisible();
      expect(center.queryByText(/候场中|设备与席位|辩手已完成准备/)).not.toBeInTheDocument();
      expect(center.queryByRole('button', { name: '正式开始比赛' }) !== null).toBe(isOrganizer);
      expect(center.queryByTestId('center-timer')).not.toBeInTheDocument();
    },
  );

  it('reserves an empty status row without displaying placeholder text or changing idle avatars', () => {
    const args = stageFixture({ status: 'START_PENDING_RUNTIME', current_action: null });
    const { container } = render(<DebatePageLayout {...args} />);
    const states = container.querySelectorAll('.debate-seat-state');
    expect(states).toHaveLength(8);
    states.forEach((state) => {
      expect(state.textContent?.trim()).toBe('');
      expect(state).toHaveAttribute('aria-hidden', 'true');
    });
    expect(container.querySelectorAll('.debate-seat-card img')).toHaveLength(8);
  });

  it('keeps the original start pending disabled state', () => {
    const args = stageFixture({ status: 'START_PENDING_RUNTIME', current_action: null });
    render(<DebatePageLayout {...args} isOrganizer runtimeStartPending />);
    expect(within(screen.getByTestId('match-waiting-stage')).getByRole('button')).toBeDisabled();
  });

  it.each(['FIXED_SPEECH', 'FREE_DEBATE', 'PREPARATION', 'END'])(
    'never exposes %s host script text',
    (stage_kind) => {
      const args = stageFixture({ action_state: 'HOST_ANNOUNCING' });
      args.room.rule.stages = [
        {
          position: 1,
          name: '当前阶段',
          stage_kind,
          start_host_text: '只用于主持音频的开场正文',
          end_host_text: '只用于主持音频的结束正文',
        },
      ];
      render(<DebatePageLayout {...args} />);
      const host = within(screen.getByTestId('host-announcement-state'));
      expect(host.getByText('主持人串场')).toBeVisible();
      expect(host.getByAltText('稷下')).toBeInTheDocument();
      expect(host.queryByText(/主持词|节选|只用于主持音频/)).not.toBeInTheDocument();
      expect(screen.queryByTestId('host-script')).not.toBeInTheDocument();
      expect(screen.queryByTestId('center-timer')).not.toBeInTheDocument();
    },
  );
});

describe('authoritative debate center', () => {
  it.each(['FINISHED', 'TERMINATED'])('removes stale speech and free timers at %s', (status) => {
    const args = stageFixture({
      status,
      action_state: 'MATCH_FINISHED',
      speech_remaining_ms: null,
      free_holder_side: 'NEGATIVE',
      free_affirmative_remaining_ms: 1000,
      free_negative_remaining_ms: 2000,
    });
    render(<DebatePageLayout {...args} />);
    const center = within(screen.getByTestId('debate-center-stage'));
    expect(center.queryByText('等待发言者')).not.toBeInTheDocument();
    expect(center.queryByText('--:--')).not.toBeInTheDocument();
    expect(center.queryByText('发言计时')).not.toBeInTheDocument();
    expect(center.queryByTestId('free-debate-clock')).not.toBeInTheDocument();
    expect(
      center.getByRole('link', { name: status === 'FINISHED' ? '观众投票' : '查看终止记录' }),
    ).toHaveAttribute(
      'href',
      status === 'FINISHED' ? 'https://showcase.vsagents.online/' : '/matches/match-stage',
    );
  });

  it('gives a paused host precedence over residual announcing action', () => {
    render(
      <DebatePageLayout {...stageFixture({ status: 'PAUSED', action_state: 'HOST_ANNOUNCING' })} />,
    );
    expect(screen.getByTestId('runtime-frozen-state')).toBeVisible();
    expect(screen.queryByTestId('host-announcement-state')).not.toBeInTheDocument();
  });

  it.each([
    'AGENT_PREPARING',
    'SPEECH_FINALIZING',
    'AGENT_FINALIZING',
    'FREE_SELECTING',
    'PREPARING',
  ])('does not reuse a speech timer during %s', (action_state) => {
    render(<DebatePageLayout {...stageFixture({ action_state })} />);
    expect(
      within(screen.getByTestId('debate-center-stage')).queryByText('发言计时'),
    ).not.toBeInTheDocument();
  });

  it('hides free-debate residue once the rule advances to a fixed speech', () => {
    render(
      <DebatePageLayout
        {...stageFixture({ free_holder_side: 'NEGATIVE', free_affirmative_remaining_ms: 1000 })}
      />,
    );
    expect(screen.queryByTestId('free-debate-clock')).not.toBeInTheDocument();
  });

  it('uses the actual current speaker side as the floor holder while the opponent may apply', () => {
    render(
      <DebatePageLayout
        {...stageFixture({
          current_action: {
            action_kind: 'FREE_DEBATE',
            duration_seconds: 300,
          } as MatchSnapshot['current_action'],
          current_speaker_side: 'AFFIRMATIVE',
          free_holder_side: 'NEGATIVE',
          free_affirmative_remaining_ms: 12000,
          free_negative_remaining_ms: 15000,
        })}
      />,
    );
    expect(screen.getByRole('group', { name: '正方发言权' })).toBeVisible();
    expect(screen.queryByRole('group', { name: '反方发言权' })).not.toBeInTheDocument();
    expect(screen.getByTestId('free-debate-clock')).not.toHaveTextContent('/');
  });

  it('retains a real timer for a current formal speech', () => {
    render(<DebatePageLayout {...stageFixture()} />);
    expect(screen.getByText('发言计时')).toBeVisible();
    expect(screen.getByText('00:12')).toBeVisible();
  });

  it('uses the stage as its heading and omits the host voice identity and mechanical captions', () => {
    const args = stageFixture({ action_state: 'HOST_ANNOUNCING' });
    args.room.rule.host_identity = { name: '龙风月瑶', provider_voice: 'longanwen_v3.1' };
    render(<DebatePageLayout {...args} />);
    const center = within(screen.getByTestId('debate-center-stage'));
    expect(center.getByRole('heading', { level: 2, name: '立论' })).toBeVisible();
    expect(center.getByText('主持人串场')).toBeVisible();
    expect(center.queryByText(/龙风月瑶|主持音频播放中|请听取本阶段提示/)).not.toBeInTheDocument();
  });

  it('keeps the Agent portrait while thinking and gives speaking precedence over a stale decision', () => {
    const args = stageFixture({
      action_state: 'AGENT_PREPARING',
      current_agent_profile_id: 'thinking-agent',
      agent_decisions: [
        {
          agent_profile_id: 'thinking-agent',
          side: 'AFFIRMATIVE',
          seat_no: 2,
          status: 'DECIDING',
          queue_rank: null,
        },
      ],
    });
    const agent = {
      ...args.room.seats[1]!,
      occupant_type: 'AGENT' as const,
      user_id: null,
      agent_profile_id: 'thinking-agent',
      occupant_avatar_key: 'agent-02',
    };
    args.room.seats[1] = agent;
    args.currentSeat = agent;
    const view = render(<DebatePageLayout {...args} />);
    const seat = view.container.querySelector('[data-seat-id="AFFIRMATIVE-2"]')!;
    const avatarSrc = seat.querySelector('img')?.getAttribute('src');
    expect(avatarSrc).toBe('/assets/avatars/agent-02.webp');
    expect(seat).toHaveAttribute('data-speaking', 'false');
    expect(seat.querySelector('.debate-seat-activity')).not.toBeInTheDocument();
    expect(screen.getByTestId('agent-preparing-stage').querySelector('img')).toHaveAttribute(
      'src',
      avatarSrc,
    );
    const preparing = screen.getByTestId('agent-preparing-stage');
    const status = within(preparing).getByText('Agent 正在思考中');
    expect(status).toBeVisible();
    expect(
      status.compareDocumentPosition(preparing.querySelector('img')!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const seatState = within(seat as HTMLElement).getByText('决策中');
    expect(
      seatState.compareDocumentPosition(seat.querySelector('img')!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByText(/正式发言计时尚未开始/)).not.toBeInTheDocument();
    view.rerender(
      <DebatePageLayout
        {...args}
        snapshot={{ ...args.snapshot, action_state: 'AGENT_SPEAKING' }}
      />,
    );
    expect(seat.querySelector('img')).toHaveAttribute('src', avatarSrc);
    expect(seat).toHaveAttribute('data-speaking', 'true');
    const speakingState = within(seat as HTMLElement).getByText('Agent 实时发言中');
    expect(
      speakingState.compareDocumentPosition(seat.querySelector('img')!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(seat.querySelector('.debate-seat-activity')).toBeInTheDocument();
    expect(within(seat as HTMLElement).queryByText('决策中')).not.toBeInTheDocument();
    expect(screen.getByTestId('current-speaker-state').querySelector('img')).toBeInTheDocument();
    expect(screen.getByTestId('speaking-status')).toHaveTextContent('Agent 实时发言中');
    for (const [status, action_state] of [
      ['RUNNING', 'HOST_ANNOUNCING'],
      ['START_COUNTDOWN', 'HOST_ANNOUNCING'],
      ['RUNNING', 'FREE_SELECTING'],
      ['RUNNING', 'AGENT_FINALIZING'],
      ['PAUSED', 'AGENT_SPEAKING'],
      ['PAUSED', 'RESUME_COUNTDOWN'],
      ['FINISHED', 'MATCH_FINISHED'],
      ['TERMINATED', 'MATCH_FINISHED'],
    ]) {
      view.rerender(
        <DebatePageLayout
          {...args}
          snapshot={{ ...args.snapshot, status, action_state } as MatchSnapshot}
        />,
      );
      expect(seat.querySelector('img')).toHaveAttribute('src', avatarSrc);
      expect(seat).toHaveAttribute('data-speaking', 'false');
      expect(screen.queryByTestId('speaking-status')).not.toBeInTheDocument();
    }
  });

  it('labels a human speech and keeps the configured human avatar version', () => {
    const args = stageFixture();
    args.currentSeat!.occupant_avatar_version = 7;
    render(<DebatePageLayout {...args} />);
    expect(screen.getByTestId('speaking-status')).toHaveTextContent('人类实时发言中');
    expect(screen.getByTestId('current-speaker-state').querySelector('img')).toHaveAttribute(
      'src',
      '/api/users/AFFIRMATIVE-1/avatar?v=7',
    );
  });

  it('uses only the authoritative countdown and distinguishes recovery', () => {
    const args = stageFixture({ status: 'START_COUNTDOWN', countdown_remaining_ms: 2100 });
    const view = render(<DebatePageLayout {...args} />);
    const countdown = within(screen.getByTestId('countdown-stage'));
    expect(countdown.getByText('比赛即将开始')).toBeVisible();
    expect(countdown.getByText('3')).toBeVisible();
    expect(screen.queryByTestId('center-timer')).not.toBeInTheDocument();
    view.rerender(
      <DebatePageLayout
        {...args}
        snapshot={{
          ...args.snapshot,
          status: 'PAUSED',
          action_state: 'RESUME_COUNTDOWN',
          countdown_remaining_ms: 1000,
        }}
      />,
    );
    expect(countdown.getByText('比赛即将恢复')).toBeVisible();
    expect(countdown.getByText('1')).toBeVisible();
  });

  it('includes the official brand within the presentation layer', () => {
    render(<DebatePageLayout {...stageFixture()} presentationMode />);
    expect(screen.getByRole('img', { name: '稷下' })).toBeVisible();
    expect(screen.queryByTestId('match-controls-speech')).not.toBeInTheDocument();
  });

  it('labels an administrator placeholder and prevents editing it as a human quote', () => {
    const args = stageFixture();
    render(
      <DebatePageLayout
        {...args}
        currentUserId="speaker"
        editingSpeechId="placeholder"
        transcript={
          {
            speeches: [
              {
                id: 'placeholder',
                user_id: 'speaker',
                status: 'FINALIZED',
                origin: 'ADMIN_PLACEHOLDER',
                side: 'AFFIRMATIVE',
                seat_no: 1,
                action_key: '1:1',
                display_text: '管理员辅助占位内容。',
              },
            ],
          } as unknown as Props['transcript']
        }
      />,
    );
    expect(screen.getByText('管理员 AI 辅助占位 · 未实际播报')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '修改本人发言文字' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '修改我的文字' })).not.toBeInTheDocument();
  });

  it('explains a pending handoff without showing a running timer or connection fault', () => {
    const args = stageFixture({ action_state: 'HUMAN_READY_TO_START' });
    render(
      <DebatePageLayout
        {...args}
        runtime={{
          ...args.runtime,
          commandErrorCode: 'asr_finalization_pending',
          socketError: 'previous final pending',
        }}
      />,
    );
    expect(screen.getByTestId('handoff-pending-state')).toHaveTextContent('麦克风尚未开启');
    expect(screen.getByRole('heading', { name: '等待上一段整理完成' })).toBeVisible();
    expect(screen.queryByText('实时连接提示：previous final pending')).not.toBeInTheDocument();
    expect(screen.queryByTestId('center-timer')).not.toBeInTheDocument();
  });
});
