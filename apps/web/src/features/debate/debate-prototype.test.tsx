import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import {
  createDebatePrototypeFixture,
  debatePrototypeFixtures,
} from '@/features/prototype-fixtures/debate';
import { DebatePrototype } from './debate-prototype';
import { DEBATE_VIEW_STATES } from './types';

describe('DebatePrototype', () => {
  afterEach(cleanup);

  it.each(DEBATE_VIEW_STATES)('renders the %s state from a typed fixture', (state) => {
    const { container } = render(<DebatePrototype fixture={debatePrototypeFixtures[state]} />);

    expect(container.querySelector(`[data-state="${state}"]`)).toBeInTheDocument();
    expect(screen.getByText('原型演示')).toBeVisible();
  });

  it('supports five seats on each side', () => {
    const fixture = createDebatePrototypeFixture('Waiting', 5);
    render(<DebatePrototype fixture={fixture} />);

    expect(fixture.affirmative.seats).toHaveLength(5);
    expect(fixture.negative.seats).toHaveLength(5);
    expect(screen.getByRole('list', { name: '正方席位' }).children).toHaveLength(5);
    expect(screen.getByRole('list', { name: '反方席位' }).children).toHaveLength(5);
  });

  it('keeps the current speaker inside the rendered 1v1 seats', () => {
    const fixture = createDebatePrototypeFixture('AgentSpeaking', 1);

    expect(fixture.negative.seats.map((seat) => seat.id)).toContain(fixture.currentSpeakerId);
  });

  it('requires an explicit click before a human speech starts', () => {
    render(<DebatePrototype fixture={debatePrototypeFixtures.HumanReadyToStart} />);

    expect(screen.getByText('轮到你发言了！')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /开始发言/ }));
    expect(screen.getByText('你的发言正在进行')).toBeVisible();
    expect(screen.queryByRole('button', { name: /开始发言/ })).not.toBeInTheDocument();
  });

  it('confirms pause and displays generic recovery requirements', () => {
    render(<DebatePrototype fixture={debatePrototypeFixtures.HumanSpeaking} />);

    fireEvent.click(screen.getByRole('button', { name: /暂停比赛/ }));
    expect(screen.getByRole('alertdialog', { name: '暂停整场比赛？' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '确认暂停' }));

    expect(screen.getAllByText('比赛已暂停')).not.toHaveLength(0);
    expect(screen.getByText('麦克风与扬声器可用')).toBeVisible();
    expect(screen.getByRole('button', { name: /申请恢复/ })).toBeVisible();
  });

  it('lets a finished transcript drawer close without changing the result', () => {
    render(<DebatePrototype fixture={debatePrototypeFixtures.Finished} />);

    expect(screen.getByLabelText('文字记录')).toBeVisible();
    fireEvent.click(screen.getByTitle('关闭右侧抽屉'));

    expect(screen.queryByLabelText('文字记录')).not.toBeInTheDocument();
    expect(screen.getByText('反方获胜')).toBeVisible();
  });
});
