import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, within } from 'storybook/test';

import {
  prototypeHomeDelayedHandler,
  prototypeHomeErrorHandler,
  type PrototypeHomeResponse,
} from '../../mocks/handlers';
import { HomePrototype } from './home-prototype';
import { getHomePrototypeFixture } from '../prototype-fixtures/home';

function sparseLeaderboard(entryCount: number) {
  const base = getHomePrototypeFixture('empty');
  return {
    ...base,
    humanRanking: {
      ...base.humanRanking,
      entries: base.humanRanking.entries.slice(0, entryCount),
    },
    agentRanking: {
      ...base.agentRanking,
      entries: base.agentRanking.entries.slice(0, entryCount),
    },
  };
}

const meta = {
  title: 'Home/HomePrototype',
  component: HomePrototype,
  parameters: {
    layout: 'fullscreen',
  },
  args: {},
} satisfies Meta<typeof HomePrototype>;

export default meta;
type Story = StoryObj<typeof meta>;

export const HomeDefault: Story = {
  args: {
    scenario: 'default',
  },
  async play({ canvasElement }) {
    const response = await fetch('/api/prototype/home');
    const payload = (await response.json()) as PrototypeHomeResponse;
    const canvas = within(canvasElement);

    await expect(response.ok).toBe(true);
    await expect(payload.rooms).toHaveLength(3);
    await expect(
      canvas.getByRole('heading', {
        name: /让人类与 Agent，\s*在声音中交锋与共创/,
      }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('link', { name: /查看比赛：/ })).toHaveLength(3);
    await expect(
      within(canvas.getByTestId('human-podium')).getByLabelText('第 3 名'),
    ).toBeVisible();
  },
};

export const HomeNetworkError: Story = {
  args: {
    scenario: 'empty',
  },
  parameters: {
    msw: {
      handlers: [prototypeHomeErrorHandler],
    },
  },
  async play({ canvasElement }) {
    const response = await fetch('/api/prototype/home');
    const payload = (await response.json()) as {
      error: { code: string; message: string };
    };
    const canvas = within(canvasElement);

    await expect(response.status).toBe(503);
    await expect(payload.error.code).toBe('prototype_fixture_unavailable');
    await expect(canvas.getByRole('heading', { name: '目前没有进行中的比赛' })).toBeVisible();
  },
};

export const HomeEmptyLobby: Story = {
  args: {
    scenario: 'empty',
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);

    await expect(canvas.getByRole('heading', { name: '目前没有进行中的比赛' })).toBeVisible();
  },
};

export const HomeCapacityFull: Story = {
  args: {
    scenario: 'capacity-full',
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);

    await expect(canvas.getByRole('status')).toHaveTextContent('观战席已满');
  },
};

export const HomeLeaderboardUpdated: Story = {
  args: {
    scenario: 'leaderboard-updated',
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);

    await expect(canvas.getAllByText(/刚刚更新/)).toHaveLength(2);
  },
};

export const HomeLeaderboardEmpty: Story = {
  args: { fixture: sparseLeaderboard(0) },
  async play({ canvasElement }) {
    await expect(within(canvasElement).getAllByText('完成首场比赛后将在这里上榜')).toHaveLength(2);
  },
};

export const HomeLeaderboardOneEntry: Story = {
  args: { fixture: sparseLeaderboard(1) },
  async play({ canvasElement }) {
    const podium = within(within(canvasElement).getByTestId('human-podium'));
    await expect(podium.getByLabelText('第 1 名')).toBeVisible();
    await expect(podium.queryByLabelText('第 2 名')).not.toBeInTheDocument();
  },
};

export const HomeLeaderboardTwoEntries: Story = {
  args: { fixture: sparseLeaderboard(2) },
  async play({ canvasElement }) {
    const podium = within(within(canvasElement).getByTestId('human-podium'));
    await expect(podium.getByLabelText('第 1 名')).toBeVisible();
    await expect(podium.getByLabelText('第 2 名')).toBeVisible();
    await expect(podium.queryByLabelText('第 3 名')).not.toBeInTheDocument();
  },
};

/**
 * Exercises the shared story-level MSW override without coupling the pure
 * presentation component to a fetch lifecycle.
 */
export const HomeDelayedNetworkBoundary: Story = {
  args: {
    scenario: 'default',
  },
  parameters: {
    msw: {
      handlers: [prototypeHomeDelayedHandler],
    },
  },
  async play({ canvasElement }) {
    const response = await fetch('/api/prototype/home');
    const payload = (await response.json()) as PrototypeHomeResponse;
    const canvas = within(canvasElement);

    await expect(response.ok).toBe(true);
    await expect(payload.rooms).toHaveLength(3);
    await expect(canvas.getByRole('heading', { name: '人类辩手排行榜' })).toBeVisible();
    await expect(canvas.queryByTestId('home-prototype-note')).not.toBeInTheDocument();
  },
};
