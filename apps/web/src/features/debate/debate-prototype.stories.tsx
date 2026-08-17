import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, userEvent, within } from 'storybook/test';

import { debatePrototypeFixtures } from '@/features/prototype-fixtures/debate';
import { DebatePrototype } from './debate-prototype';

const meta = {
  title: 'Debate/DebatePrototype',
  component: DebatePrototype,
  parameters: {
    layout: 'fullscreen',
  },
  args: {
    fixture: debatePrototypeFixtures.HumanReadyToStart,
  },
} satisfies Meta<typeof DebatePrototype>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Waiting: Story = {
  args: { fixture: debatePrototypeFixtures.Waiting },
};

export const HumanReadyToStart: Story = {
  args: { fixture: debatePrototypeFixtures.HumanReadyToStart },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('轮到你发言了！')).toBeVisible();
    await userEvent.click(canvas.getByRole('button', { name: /开始发言/ }));
    await expect(canvas.getByText('你的发言正在进行')).toBeVisible();
  },
};

export const HumanSpeaking: Story = {
  args: { fixture: debatePrototypeFixtures.HumanSpeaking },
};

export const AgentThinking: Story = {
  args: { fixture: debatePrototypeFixtures.AgentThinking },
};

export const AgentSpeaking: Story = {
  args: { fixture: debatePrototypeFixtures.AgentSpeaking },
};

export const FreeDebateHandRaise: Story = {
  args: { fixture: debatePrototypeFixtures.FreeDebateHandRaise },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText('申请发言顺序 1')).toBeVisible();
    await userEvent.click(canvas.getByRole('button', { name: /取消举手/ }));
    await expect(canvas.queryByLabelText('申请发言顺序 1')).not.toBeInTheDocument();
  },
};

export const Paused: Story = {
  args: { fixture: debatePrototypeFixtures.Paused },
};

export const Disconnected: Story = {
  args: { fixture: debatePrototypeFixtures.Disconnected },
};

export const ErrorDrawer: Story = {
  args: { fixture: debatePrototypeFixtures.ErrorDrawer },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    const errorPanel = canvas.getByRole('region', { name: '实时服务未恢复' });
    await expect(errorPanel).toHaveTextContent('TTS_STREAM_STALLED');
  },
};

export const Finished: Story = {
  args: { fixture: debatePrototypeFixtures.Finished },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('反方获胜')).toBeVisible();
    await userEvent.click(canvas.getByRole('button', { name: '关闭文字记录' }));
    await expect(canvas.queryByLabelText('文字记录')).not.toBeInTheDocument();
  },
};
