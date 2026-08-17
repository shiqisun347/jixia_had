import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AdminRefreshButton } from './admin-controls';

describe('AdminRefreshButton', () => {
  it('runs one refresh for synchronous repeated clicks and restores the button', async () => {
    let resolveRefresh: (() => void) | undefined;
    const onRefresh = vi.fn(() => new Promise<void>((resolve) => (resolveRefresh = resolve)));
    render(<AdminRefreshButton label="重新加载" onRefresh={onRefresh} />);

    const button = screen.getByRole('button', { name: '重新加载' });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');

    await act(async () => resolveRefresh?.());
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute('aria-busy', 'false');
  });

  it('restores the button when refresh rejects', async () => {
    const onRefresh = vi.fn(() => Promise.reject(new Error('offline')));
    render(<AdminRefreshButton onRefresh={onRefresh} />);

    await act(async () => fireEvent.click(screen.getByRole('button', { name: '刷新' })));

    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled();
  });
});
