import { delay, http, HttpResponse } from 'msw';

/**
 * These types and routes are deliberately prototype-only. They describe the
 * smallest network boundary needed to exercise loading, error, and delayed
 * UI states in Storybook; they are not an OpenAPI or production API contract.
 */
export type PrototypeRoomSummary = {
  id: string;
  title: string;
  topic: string;
  format: string;
  status: 'waiting' | 'running' | 'paused';
  seats: { occupied: number; total: number };
};

export type PrototypeLeaderboardEntry = {
  rank: number;
  name: string;
  score: number;
  kind: 'human' | 'agent';
};

export type PrototypeHomeResponse = {
  rooms: PrototypeRoomSummary[];
  leaderboards: {
    humans: PrototypeLeaderboardEntry[];
    agents: PrototypeLeaderboardEntry[];
    updatedAt: string;
  };
};

export type PrototypeDebateResponse = {
  matchId: string;
  roomCode: string;
  title: string;
  topic: string;
  format: string;
  state:
    | 'Waiting'
    | 'HumanReadyToStart'
    | 'HumanSpeaking'
    | 'AgentThinking'
    | 'AgentSpeaking'
    | 'FreeDebateHandRaise'
    | 'Paused'
    | 'Disconnected'
    | 'ErrorDrawer'
    | 'Finished';
};

export const prototypeHomeResponse: PrototypeHomeResponse = {
  rooms: [
    {
      id: 'prototype-room-1',
      title: '声辩实验 · 春季场',
      topic: 'AI 的迅猛发展提升了人类创作者存在的意义',
      format: '新国辩 · 3v3',
      status: 'running',
      seats: { occupied: 5, total: 6 },
    },
    {
      id: 'prototype-room-2',
      title: '观点交锋 · 练习场',
      topic: '开放式 AI 是否应该拥有创作署名权',
      format: '自由辩论 · 1v1',
      status: 'waiting',
      seats: { occupied: 1, total: 2 },
    },
    {
      id: 'prototype-room-3',
      title: '暂停中的实验',
      topic: '技术进步是否必然带来更好的生活',
      format: '线性赛制 · 2v2',
      status: 'paused',
      seats: { occupied: 4, total: 4 },
    },
  ],
  leaderboards: {
    humans: [
      { rank: 1, name: '林知行', score: 1280, kind: 'human' },
      { rank: 2, name: '周予安', score: 1216, kind: 'human' },
      { rank: 3, name: '沈清和', score: 1168, kind: 'human' },
    ],
    agents: [
      { rank: 1, name: '乾元', score: 1364, kind: 'agent' },
      { rank: 2, name: '知衡', score: 1302, kind: 'agent' },
      { rank: 3, name: '未济', score: 1240, kind: 'agent' },
    ],
    updatedAt: '2026-08-03T00:00:00.000Z',
  },
};

export const prototypeDebateResponse: PrototypeDebateResponse = {
  matchId: 'prototype-match-1',
  roomCode: 'JX-2048',
  title: '声辩实验 · 春季场',
  topic: 'AI 的迅猛发展提升了人类创作者存在的意义',
  format: '新国辩 · 3v3',
  state: 'HumanReadyToStart',
};

const prototypeHomeEndpoint = '*/api/prototype/home';
const prototypeDebateEndpoint = '*/api/prototype/debate/:matchId';
const authMeEndpoint = '*/api/auth/me';

export const authUnauthenticatedHandler = http.get(authMeEndpoint, () =>
  HttpResponse.json({ error: { code: 'not_authenticated', message: '请先登录' } }, { status: 401 }),
);

export const prototypeHomeErrorHandler = http.get(prototypeHomeEndpoint, () => {
  return HttpResponse.json(
    {
      error: {
        code: 'prototype_fixture_unavailable',
        message: '演示数据暂时不可用，请重试。',
      },
    },
    { status: 503 },
  );
});

export const prototypeHomeDelayedHandler = http.get(prototypeHomeEndpoint, async () => {
  await delay(800);
  return HttpResponse.json(prototypeHomeResponse);
});

export const handlers = [
  authUnauthenticatedHandler,
  http.get(prototypeHomeEndpoint, () => HttpResponse.json(prototypeHomeResponse)),
  http.get(prototypeDebateEndpoint, ({ params }) => {
    const matchId =
      typeof params.matchId === 'string' ? params.matchId : prototypeDebateResponse.matchId;
    return HttpResponse.json({ ...prototypeDebateResponse, matchId });
  }),
];
