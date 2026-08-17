'use client';

import { useEffect, useRef, useState } from 'react';

import { requestJson } from '@/lib/auth-api';
import { AuthNavigation } from '@/features/auth/auth-navigation';
import { useCurrentUser } from '@/features/auth/use-auth';
import { type LobbyRoom } from '@/lib/rooms-api';
import { resolveRoomEntry } from '@/features/rooms/room-entry';

import { HomePrototype } from './home-prototype';
import { getHomePrototypeFixture, type HomePrototypeFixture } from '../prototype-fixtures/home';

type RankingRow = {
  participant_id: string;
  display_name: string;
  points: number;
  matches: number;
  wins: number;
  average_personal_score: number;
  avatar_key?: string | null;
};
type Leaderboards = { generated_at: string | null; human: RankingRow[]; agent: RankingRow[] };

function toFixture(rooms: LobbyRoom[], rankings: Leaderboards): HomePrototypeFixture {
  const updatedAt = rankings.generated_at
    ? new Date(rankings.generated_at).toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '暂无快照';
  const convertRanking = (rows: RankingRow[], kind: 'human' | 'agent') =>
    rows.slice(0, 3).map((row) => ({
      id: row.participant_id,
      displayName: row.display_name,
      score: row.points,
      matches: row.matches,
      winRate: row.matches ? Math.round((row.wins / row.matches) * 100) : 0,
      averagePersonalScore: row.average_personal_score,
      kind,
      initials: row.display_name.slice(0, 1),
      avatarSrc: row.avatar_key ? `/assets/avatars/${row.avatar_key}.webp` : undefined,
    }));
  return {
    ...getHomePrototypeFixture('default'),
    scenario: 'default',
    spectatorCapacityFull: rooms.some((room) => room.spectator_capacity_full),
    rooms: rooms
      .filter((room) => ['START_PENDING_RUNTIME', 'RUNNING', 'PAUSED'].includes(room.status))
      .map((room) => ({
        id: room.id,
        title: room.title,
        topic: room.topic_title,
        ruleName: room.rule_name,
        format: `${room.side_size}v${room.side_size}`,
        occupiedSeats: room.occupied_seats,
        totalSeats: room.side_size * 2,
        spectatorRemaining: room.spectator_remaining,
        leadingSide: 'affirmative',
        status: room.status as 'START_PENDING_RUNTIME' | 'RUNNING' | 'PAUSED',
        href: resolveRoomEntry(room).href,
      })),
    humanRanking: {
      title: '人类辩手排行榜',
      updatedAt,
      entries: convertRanking(rankings.human, 'human'),
    },
    agentRanking: {
      title: 'Agent 辩手排行榜',
      updatedAt,
      entries: convertRanking(rankings.agent, 'agent'),
    },
  };
}

export function HomeRuntime() {
  const currentUser = useCurrentUser();
  const [retryNonce, setRetryNonce] = useState(0);
  const roomsRef = useRef<LobbyRoom[]>([]);
  const rankingsRef = useRef<Leaderboards>({ generated_at: null, human: [], agent: [] });
  const [roomSyncIssue, setRoomSyncIssue] = useState(false);
  const [fixture, setFixture] = useState<HomePrototypeFixture>(() =>
    toFixture([], { generated_at: null, human: [], agent: [] }),
  );
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (currentUser.isLoading) return;
    let active = true;
    const roomRequest = currentUser.data
      ? requestJson<LobbyRoom[]>('/api/lobby/rooms')
      : Promise.resolve<LobbyRoom[]>([]);
    const rankingRequest = requestJson<Leaderboards>('/api/leaderboards');
    void Promise.allSettled([roomRequest, rankingRequest])
      .then(([roomResult, rankingResult]) => {
        if (!active) return;
        if (roomResult.status === 'fulfilled') {
          roomsRef.current = roomResult.value;
          setRoomSyncIssue(false);
        } else if (currentUser.data) {
          setRoomSyncIssue(true);
        }
        if (rankingResult.status === 'fulfilled') {
          rankingsRef.current = rankingResult.value;
        }
        setFixture(
          toFixture(
            roomResult.status === 'fulfilled' ? roomResult.value : roomsRef.current,
            rankingResult.status === 'fulfilled' ? rankingResult.value : rankingsRef.current,
          ),
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [currentUser.data, currentUser.isLoading, retryNonce]);
  return (
    <HomePrototype
      authNavigation={<AuthNavigation />}
      fixture={fixture}
      loading={currentUser.isLoading || loading}
      roomSyncIssue={roomSyncIssue}
      onRetryRooms={() => {
        setRoomSyncIssue(false);
        setLoading(true);
        setRetryNonce((value) => value + 1);
      }}
    />
  );
}
