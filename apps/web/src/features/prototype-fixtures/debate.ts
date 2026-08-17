import type {
  DebatePrototypeFixture,
  DebatePrototypePermissions,
  DebateSeat,
  DebateTranscriptEntry,
  DebateViewState,
} from '@/features/debate/types';

const affirmativeSeats: DebateSeat[] = [
  {
    id: 'affirmative-1',
    side: 'affirmative',
    name: '林知夏',
    position: '正方一辩',
    kind: 'human',
    status: 'online',
    avatarTone: 'crimson',
  },
  {
    id: 'affirmative-2',
    side: 'affirmative',
    name: '陈述安',
    position: '正方二辩',
    kind: 'human',
    status: 'online',
    avatarTone: 'ink',
  },
  {
    id: 'affirmative-3',
    side: 'affirmative',
    name: 'Agent·正三',
    position: '正方三辩',
    kind: 'agent',
    status: 'online',
    avatarTone: 'blue',
  },
  {
    id: 'affirmative-4',
    side: 'affirmative',
    name: 'Agent·正四',
    position: '正方四辩',
    kind: 'agent',
    status: 'online',
    avatarTone: 'silver',
  },
  {
    id: 'affirmative-5',
    side: 'affirmative',
    name: '周闻道',
    position: '正方五辩',
    kind: 'human',
    status: 'online',
    avatarTone: 'amber',
  },
];

const negativeSeats: DebateSeat[] = [
  {
    id: 'negative-1',
    side: 'negative',
    name: 'Agent·反一',
    position: '反方一辩',
    kind: 'agent',
    status: 'online',
    avatarTone: 'violet',
  },
  {
    id: 'negative-2',
    side: 'negative',
    name: '沈观',
    position: '反方二辩',
    kind: 'human',
    status: 'online',
    avatarTone: 'blue',
  },
  {
    id: 'negative-3',
    side: 'negative',
    name: 'Agent·反三',
    position: '反方三辩',
    kind: 'agent',
    status: 'online',
    avatarTone: 'ink',
  },
  {
    id: 'negative-4',
    side: 'negative',
    name: 'Agent·反四',
    position: '反方四辩',
    kind: 'agent',
    status: 'online',
    avatarTone: 'amber',
  },
  {
    id: 'negative-5',
    side: 'negative',
    name: 'Agent·反五',
    position: '反方五辩',
    kind: 'agent',
    status: 'online',
    avatarTone: 'silver',
  },
];

const transcript: DebateTranscriptEntry[] = [
  {
    id: 'speech-01',
    stage: '开篇立论',
    timestamp: '10:02',
    speakerId: 'affirmative-1',
    speakerName: '林知夏',
    position: '正方一辩',
    side: 'affirmative',
    status: 'final',
    editableByViewer: true,
    content: '效率并不天然排斥公平。真正需要讨论的，是技术红利能否通过制度设计被更多人共享。',
  },
  {
    id: 'speech-02',
    stage: '开篇立论',
    timestamp: '10:06',
    speakerId: 'negative-1',
    speakerName: 'Agent·反一',
    position: '反方一辩',
    side: 'negative',
    status: 'final',
    content: '当效率成为唯一尺度，资源会快速向少数掌握技术的人聚集，公平的起点反而被拉得更远。',
  },
  {
    id: 'speech-03',
    stage: '攻辩小结',
    timestamp: '10:18',
    speakerId: 'affirmative-2',
    speakerName: '陈述安',
    position: '正方二辩',
    side: 'affirmative',
    status: 'final',
    content: '对方把工具造成的短期分化等同于长期不公，却忽略了生产力提升扩大公共投入空间的事实。',
  },
  {
    id: 'speech-04',
    stage: '攻辩小结',
    timestamp: '10:21',
    speakerId: 'negative-2',
    speakerName: '沈观',
    position: '反方二辩',
    side: 'negative',
    status: 'final',
    content: '分配机制不是效率提升后自动出现的结果。没有约束，新增收益仍会沿既有优势持续累积。',
  },
  {
    id: 'speech-05',
    stage: '自由辩论',
    timestamp: '10:28',
    speakerId: 'affirmative-1',
    speakerName: '林知夏',
    position: '正方一辩',
    side: 'affirmative',
    status: 'live',
    editableByViewer: false,
    content: '首先，效率提升让更多人享受到科技带来的便利，从而推动社会公共服务的可及性……',
  },
  {
    id: 'speech-06',
    stage: '自由辩论',
    timestamp: '10:30',
    speakerId: 'negative-3',
    speakerName: 'Agent·反三',
    position: '反方三辩',
    side: 'negative',
    status: 'final',
    content: '便利的普及与机会的公平不是同一件事。我们要回答的是，新增价值究竟由谁决定、由谁获得。',
  },
];

const enabled = { visible: true, enabled: true } as const;

const participantPermissions: DebatePrototypePermissions = {
  startSpeech: enabled,
  endSpeech: enabled,
  resetSpeech: enabled,
  pauseMatch: enabled,
  resumeMatch: enabled,
  raiseHand: enabled,
  viewTranscript: enabled,
  exportTranscript: enabled,
};

function cloneSeats(seats: DebateSeat[]): DebateSeat[] {
  return seats.map((seat) => ({ ...seat }));
}

function currentSpeakerFor(state: DebateViewState): string | undefined {
  switch (state) {
    case 'HumanReadyToStart':
    case 'HumanSpeaking':
    case 'FreeDebateHandRaise':
      return 'affirmative-1';
    case 'AgentThinking':
    case 'AgentSpeaking':
    case 'ErrorDrawer':
      return 'negative-3';
    case 'Paused':
      return 'affirmative-2';
    default:
      return undefined;
  }
}

function stageFor(state: DebateViewState): string {
  if (
    state === 'FreeDebateHandRaise' ||
    state === 'AgentThinking' ||
    state === 'AgentSpeaking' ||
    state === 'ErrorDrawer'
  ) {
    return '自由辩论';
  }
  if (state === 'Finished') {
    return '比赛结束';
  }
  return '正方一辩立论';
}

export function createDebatePrototypeFixture(
  state: DebateViewState,
  seatCount = 4,
): DebatePrototypeFixture {
  const boundedSeatCount = Number.isFinite(seatCount)
    ? Math.min(5, Math.max(1, Math.trunc(seatCount)))
    : 4;
  const positive = cloneSeats(affirmativeSeats).slice(0, boundedSeatCount);
  const negative = cloneSeats(negativeSeats).slice(0, boundedSeatCount);

  if (state === 'FreeDebateHandRaise') {
    const viewer = positive.find((seat) => seat.id === 'affirmative-1');
    if (viewer) viewer.handOrder = 1;
  }
  if (state === 'Disconnected') {
    const disconnected = positive.find((seat) => seat.id === 'affirmative-2') ?? positive[0];
    if (disconnected) disconnected.status = 'offline';
  }

  const requestedCurrentSpeakerId = currentSpeakerFor(state);
  const availableSeatIds = new Set([...positive, ...negative].map((seat) => seat.id));
  const currentSpeakerId = requestedCurrentSpeakerId
    ? availableSeatIds.has(requestedCurrentSpeakerId)
      ? requestedCurrentSpeakerId
      : state === 'AgentThinking' || state === 'AgentSpeaking' || state === 'ErrorDrawer'
        ? negative[0]?.id
        : positive[0]?.id
    : undefined;

  return {
    state,
    match: {
      roomCode: '306828',
      formatName: `新国辩 · ${boundedSeatCount}v${boundedSeatCount}`,
      matchLabel: '训练赛 · 原型演示',
      topic: '在人工智能快速发展的今天，我们更应重视效率还是公平？',
      stage: stageFor(state),
      stageIndex: state === 'Finished' ? 6 : 3,
      stageCount: 6,
      timerSeconds: state === 'Finished' ? 0 : 32,
      networkLabel: state === 'Disconnected' ? '连接恢复中' : '网络良好',
    },
    affirmative: {
      side: 'affirmative',
      name: '正方',
      stance: '我们更应重视公平',
      seats: positive,
    },
    negative: {
      side: 'negative',
      name: '反方',
      stance: '我们更应重视效率',
      seats: negative,
    },
    currentSpeakerId,
    viewerSeatId: 'affirmative-1',
    transcript,
    permissions: participantPermissions,
    transcriptInitiallyOpen: state === 'ErrorDrawer' || state === 'Finished',
    error:
      state === 'ErrorDrawer'
        ? {
            code: 'TTS_STREAM_STALLED',
            userMessage: '语音合成连续 10 秒没有返回音频，重试后仍未恢复。',
            retryLabel: '已自动重试 1 次',
            nextStep: '比赛已暂停。请检查模型服务后，由暂停发起者或房主申请恢复。',
          }
        : undefined,
    pause:
      state === 'Paused' || state === 'Disconnected' || state === 'ErrorDrawer'
        ? {
            title: state === 'Disconnected' ? '等待离线辩手返回' : '比赛已暂停',
            initiatedBy: state === 'Disconnected' ? '系统自动暂停' : '林知夏发起暂停',
            requirements: ['全部人类选手在线', '麦克风与扬声器可用', '所有选手仍在房间'],
            unmetReasons:
              state === 'Disconnected' ? ['陈述安当前离线，暂时不能恢复比赛'] : undefined,
          }
        : undefined,
    result:
      state === 'Finished'
        ? {
            winner: 'negative',
            winnerLabel: '反方获胜',
            affirmativeScore: 87,
            negativeScore: 91,
            summary: '反方对“效率红利如何转化为机会公平”的追问更完整，回应也更集中。',
          }
        : undefined,
  };
}

export const getDebatePrototypeFixture = createDebatePrototypeFixture;

export const debatePrototypeFixtures = {
  Waiting: createDebatePrototypeFixture('Waiting'),
  HumanReadyToStart: createDebatePrototypeFixture('HumanReadyToStart'),
  HumanSpeaking: createDebatePrototypeFixture('HumanSpeaking'),
  AgentThinking: createDebatePrototypeFixture('AgentThinking'),
  AgentSpeaking: createDebatePrototypeFixture('AgentSpeaking'),
  FreeDebateHandRaise: createDebatePrototypeFixture('FreeDebateHandRaise'),
  Paused: createDebatePrototypeFixture('Paused'),
  Disconnected: createDebatePrototypeFixture('Disconnected'),
  ErrorDrawer: createDebatePrototypeFixture('ErrorDrawer'),
  Finished: createDebatePrototypeFixture('Finished'),
} satisfies Record<DebateViewState, DebatePrototypeFixture>;
