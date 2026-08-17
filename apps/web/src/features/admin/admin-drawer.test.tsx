import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { AdminDrawer } from './admin-controls';

function DrawerFixture() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        打开抽屉
      </button>
      <AdminDrawer open={open} onOpenChange={setOpen} title="测试抽屉">
        抽屉内容
      </AdminDrawer>
    </>
  );
}

describe('AdminDrawer', () => {
  it('returns focus to the opening control after the drawer closes', async () => {
    render(<DrawerFixture />);
    const trigger = screen.getByRole('button', { name: '打开抽屉' });
    trigger.focus();
    fireEvent.focus(trigger);
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
