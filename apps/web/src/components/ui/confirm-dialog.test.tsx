import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ConfirmDialog } from './confirm-dialog';

function ConfirmFixture() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        打开确认框
      </button>
      <ConfirmDialog
        description="确认焦点回收。"
        onConfirm={() => setOpen(false)}
        onOpenChange={setOpen}
        open={open}
        title="确认操作？"
      />
    </>
  );
}

describe('ConfirmDialog', () => {
  it('returns focus to the opening control after cancellation', async () => {
    render(<ConfirmFixture />);
    const trigger = screen.getByRole('button', { name: '打开确认框' });
    trigger.focus();
    fireEvent.focus(trigger);
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it('does not execute the action when the user cancels', () => {
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <ConfirmDialog
        description="这项操作需要确认。"
        onConfirm={onConfirm}
        onOpenChange={onOpenChange}
        open
        title="确认操作？"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('blocks synchronous duplicate confirmation clicks', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        confirmLabel="执行操作"
        description="这项操作只应执行一次。"
        onConfirm={onConfirm}
        onOpenChange={() => undefined}
        open
        title="确认操作？"
      />,
    );
    const button = screen.getByRole('button', { name: '执行操作' });

    fireEvent.click(button);
    fireEvent.click(button);

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('locks dismissal and exposes progress while loading', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        description="正在提交操作。"
        loading
        onConfirm={onConfirm}
        onOpenChange={() => undefined}
        open
        title="确认操作？"
      />,
    );

    expect(screen.getByRole('button', { name: '正在处理' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  });
});
