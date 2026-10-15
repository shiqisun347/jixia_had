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
      center.getByRole('link', { name: status === 'FINISHED' ? '查看赛后记录' : '查看终止记录' }),
    ).toBeVisible();
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
    expect(screen.getByText('正方发言权')).toBeVisible();
    expect(screen.queryByText('反方发言权')).not.toBeInTheDocument();
  });

  it('retains a real timer for a current formal speech', () => {
    render(<DebatePageLayout {...stageFixture()} />);
    expect(screen.getByText('发言计时')).toBeVisible();
    expect(screen.getByText('00:12')).toBeVisible();
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
