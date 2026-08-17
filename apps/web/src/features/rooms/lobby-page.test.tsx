import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LobbySyncStatus } from './lobby-page';

describe('LobbySyncStatus', () => {
  afterEach(cleanup);

  it('distinguishes fetching, success and interrupted states', () => {
    const { rerender } = render(<LobbySyncStatus isError={false} isFetching onRetry={vi.fn()} />);
    expect(screen.getByText('正在同步')).toBeVisible();

    rerender(<LobbySyncStatus isError={false} isFetching={false} onRetry={vi.fn()} />);
    expect(screen.getByText('已同步')).toBeVisible();

    rerender(<LobbySyncStatus isError isFetching={false} onRetry={vi.fn()} />);
    expect(screen.getByRole('button', { name: '同步中断 · 重新同步' })).toBeVisible();
  });

  it('offers a working retry action after an interruption', () => {
    const onRetry = vi.fn();
    render(<LobbySyncStatus isError isFetching={false} onRetry={onRetry} />);

    fireEvent.click(screen.getByRole('button', { name: '同步中断 · 重新同步' }));

    expect(onRetry).toHaveBeenCalledOnce();
  });
});
