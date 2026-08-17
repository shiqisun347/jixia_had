export type HomePrototypeScenario = 'default' | 'empty' | 'capacity-full' | 'leaderboard-updated';

export type DebateSide = 'affirmative' | 'negative';

export interface HomeRoomFixture {
  readonly id: string;
  readonly title: string;
  readonly topic: string;
  readonly ruleName: string;
  readonly format: string;
  readonly occupiedSeats: number;
  readonly totalSeats: number;
  readonly spectatorRemaining: number;
  readonly leadingSide: DebateSide;
  readonly status: 'START_PENDING_RUNTIME' | 'RUNNING' | 'PAUSED';
  readonly href?: string;
}

export interface RankingEntryFixture {
  readonly id: string;
  readonly displayName: string;
  readonly score: number;
  readonly matches: number;
  readonly winRate: number;
  readonly averagePersonalScore: number;
  readonly kind: 'human' | 'agent';
  readonly initials: string;
  readonly avatarSrc?: string;
}

export interface RankingFixture {
  readonly title: string;
  readonly updatedAt: string;
  readonly entries: readonly RankingEntryFixture[];
}

export interface HomePrototypeFixture {
  readonly scenario: HomePrototypeScenario;
  readonly rooms: readonly HomeRoomFixture[];
  readonly spectatorCapacityFull: boolean;
  readonly humanRanking: RankingFixture;
  readonly agentRanking: RankingFixture;
}

const rooms: readonly HomeRoomFixture[] = [
  {
    id: 'room-306828',
    title: '公平与效率之辩',
    topic: '人工智能快速发展的今天，我们更应重视效率还是公平？',
    ruleName: '新国辩',
    format: '4v4',
    occupiedSeats: 8,
    totalSeats: 8,
    spectatorRemaining: 4,
    leadingSide: 'affirmative',
    status: 'RUNNING',
  },
  {
    id: 'room-421709',
    title: '未来教育实验场',
    topic: 'AI 教师会提升还是削弱学生的自主学习能力？',
    ruleName: '政策辩论',
    format: '3v3',
    occupiedSeats: 6,
    totalSeats: 6,
    spectatorRemaining: 3,
    leadingSide: 'negative',
    status: 'RUNNING',
  },
  {
    id: 'room-582164',
    title: '开放模型边界',
    topic: '开放权重是推动大模型安全发展的更优路径吗？',
    ruleName: '经典赛制',
    format: '2v2',
    occupiedSeats: 4,
    totalSeats: 4,
    spectatorRemaining: 3,
    leadingSide: 'negative',
    status: 'RUNNING',
  },
];

const humanEntries: readonly RankingEntryFixture[] = [
  {
    id: 'human-lin-zhixia',
    displayName: '林知夏',
    score: 1821,
    matches: 42,
    winRate: 71,
    averagePersonalScore: 17.8,
    kind: 'human',
    initials: '林',
    avatarSrc: '/assets/avatars/human-01.webp',
  },
  {
    id: 'human-chen-shuan',
    displayName: '陈述安',
    score: 1764,
    matches: 38,
    winRate: 68,
    averagePersonalScore: 17.2,
    kind: 'human',
    initials: '陈',
    avatarSrc: '/assets/avatars/human-02.webp',
  },
  {
    id: 'human-shen-guan',
    displayName: '沈观',
    score: 1688,
    matches: 35,
    winRate: 66,
    averagePersonalScore: 16.9,
    kind: 'human',
    initials: '沈',
    avatarSrc: '/assets/avatars/human-03.webp',
  },
];

const agentEntries: readonly RankingEntryFixture[] = [
  {
    id: 'agent-fansi',
    displayName: 'Agent-反思',
    score: 1987,
    matches: 51,
    winRate: 75,
    averagePersonalScore: 18.3,
    kind: 'agent',
    initials: '反',
  },
  {
    id: 'agent-rui-zhi',
    displayName: 'Agent-睿智',
    score: 1893,
    matches: 49,
    winRate: 72,
    averagePersonalScore: 17.9,
    kind: 'agent',
    initials: '睿',
  },
  {
    id: 'agent-dongcha',
    displayName: 'Agent-洞察',
    score: 1765,
    matches: 46,
    winRate: 69,
    averagePersonalScore: 17.4,
    kind: 'agent',
    initials: '察',
  },
];

function createFixture(
  scenario: HomePrototypeScenario,
  overrides: Partial<HomePrototypeFixture> = {},
): HomePrototypeFixture {
  const updatedAt = scenario === 'leaderboard-updated' ? '今天 08:00 · 刚刚更新' : '今天 08:00';

  return {
    scenario,
    rooms,
    spectatorCapacityFull: false,
    humanRanking: {
      title: '人类辩手排行榜',
      updatedAt,
      entries: humanEntries,
    },
    agentRanking: {
      title: 'Agent 辩手排行榜',
      updatedAt,
      entries: agentEntries,
    },
    ...overrides,
  };
}

export const homePrototypeFixtures: Readonly<Record<HomePrototypeScenario, HomePrototypeFixture>> =
  {
    default: createFixture('default'),
    empty: createFixture('empty', { rooms: [] }),
    'capacity-full': createFixture('capacity-full', {
      spectatorCapacityFull: true,
      rooms: rooms.map((room) => ({ ...room, spectatorRemaining: 0 })),
    }),
    'leaderboard-updated': createFixture('leaderboard-updated'),
  };

export function getHomePrototypeFixture(
  scenario: HomePrototypeScenario = 'default',
): HomePrototypeFixture {
  return homePrototypeFixtures[scenario];
}
