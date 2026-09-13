import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import { HomePrototype } from './home-prototype';
import { getHomePrototypeFixture } from '../prototype-fixtures/home';

function renderHome(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['experiments', 'capabilities'], {
    creation_enabled: true,
    history_readable: true,
    target_version: '2.1.0',
  });
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

describe('HomePrototype', () => {
  afterEach(cleanup);

  it('renders the default home structure with typed room and ranking fixtures', () => {
    renderHome(<HomePrototype />);

    expect(
      screen.getByRole('heading', {
        name: /人机共辩，\s*百家之言各得鸣。/,
      }),
    ).toBeVisible();
    expect(screen.getByRole('heading', { name: '正在进行' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '人类辩手排行榜' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Agent 辩手排行榜' })).toBeVisible();
    expect(screen.queryByText('多人 · 多智能体 · 实时辩论')).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /查看比赛：/ })).toHaveLength(3);
    expect(screen.queryByRole('heading', { name: '排行榜' })).not.toBeInTheDocument();
    expect(screen.queryByText('每日根据正常完赛评分更新')).not.toBeInTheDocument();
    expect(screen.queryByTestId('home-prototype-note')).not.toBeInTheDocument();
  });

  it('renders a useful empty lobby state', () => {
    renderHome(<HomePrototype scenario="empty" />);

    expect(screen.getByRole('heading', { name: '目前没有进行中的比赛' })).toBeVisible();
    expect(screen.queryByRole('link', { name: /查看比赛：/ })).not.toBeInTheDocument();
  });

  it('explains when the global spectator capacity is full', () => {
    renderHome(<HomePrototype scenario="capacity-full" />);

    expect(screen.getByRole('status')).toHaveTextContent(
      '观战席已满。 当前全平台观众已达上限，请稍后重试。',
    );
    expect(screen.getAllByText('观战席已满')).toHaveLength(3);
  });

  it('surfaces the updated leaderboard timestamp', () => {
    renderHome(<HomePrototype scenario="leaderboard-updated" />);

    expect(screen.getAllByText(/刚刚更新/)).toHaveLength(2);
  });

  it('labels active room cards with their real server status', () => {
    const base = getHomePrototypeFixture('default');
    renderHome(
      <HomePrototype
        fixture={{
          ...base,
          rooms: [
            { ...base.rooms[0], id: 'running', status: 'RUNNING' },
            { ...base.rooms[1], id: 'paused', status: 'PAUSED' },
            { ...base.rooms[2], id: 'starting', status: 'START_PENDING_RUNTIME' },
          ],
        }}
      />,
    );

    expect(screen.getByText('LIVE')).toBeVisible();
    expect(screen.getByText('已暂停')).toBeVisible();
    expect(screen.getByText('启动中')).toBeVisible();
  });

  it.each([0, 1, 2, 3])('keeps a stable podium for %i ranked entries', (entryCount) => {
    const base = getHomePrototypeFixture('empty');
    renderHome(
      <HomePrototype
        fixture={{
          ...base,
          humanRanking: {
            ...base.humanRanking,
            entries: base.humanRanking.entries.slice(0, entryCount),
          },
          agentRanking: {
            ...base.agentRanking,
            entries: base.agentRanking.entries.slice(0, entryCount),
          },
        }}
      />,
    );

    if (entryCount === 0) {
      expect(screen.getAllByText('完成首场比赛后将在这里上榜')).toHaveLength(2);
      expect(screen.queryByTestId('human-podium')).not.toBeInTheDocument();
      return;
    }

    const podium = within(screen.getByTestId('human-podium'));
    expect(podium.getByLabelText('第 1 名')).toBeVisible();
    expect(podium.getByRole('img', { name: '第 1 名' })).toBeVisible();
    if (entryCount >= 2) expect(podium.getByLabelText('第 2 名')).toBeVisible();
    else expect(podium.queryByLabelText('第 2 名')).not.toBeInTheDocument();
    if (entryCount >= 3) expect(podium.getByLabelText('第 3 名')).toBeVisible();
    else expect(podium.queryByLabelText('第 3 名')).not.toBeInTheDocument();
  });
});
