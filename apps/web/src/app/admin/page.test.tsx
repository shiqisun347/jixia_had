import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));

vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminUsersPage from './users/page';

const targetUser = {
  id: 'user-2',
  username: 'debater',
  real_name: '测试辩手',
  role: 'USER',
  status: 'ACTIVE',
  match_count: 0,
  finished_count: 0,
  wins: 0,
  points: 0,
  average_personal_score: 0,
};

describe('admin page temporary password reset', () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/auth/me') return Promise.resolve({ user: { id: 'admin-1' } });
      if (path === '/api/admin/users/user-2/temporary-password')
        return Promise.resolve({
          temporary_password: 'one-time-secret',
          must_change_password: true,
        });
      if (path.startsWith('/api/admin/users'))
        return Promise.resolve({
          items: [
            { ...targetUser, id: 'admin-1', username: 'admin', real_name: '管理员', role: 'ADMIN' },
            targetUser,
          ],
          page: 1,
          page_size: 25,
          total: 2,
          total_pages: 1,
        });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
  });

  function renderPage() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <AdminUsersPage />
      </QueryClientProvider>,
    );
  }

  it('does not offer password reset for the current administrator', async () => {
    renderPage();

    await screen.findByText('管理员');
    const actionMenus = screen.getAllByRole('button', { name: '更多操作' });
    expect(actionMenus).toHaveLength(2);
    expect(actionMenus[0]).toHaveTextContent('操作');
  });

  it('shows the generated password once and warns about revoked sessions', async () => {
    renderPage();

    fireEvent.pointerDown((await screen.findAllByRole('button', { name: '更多操作' }))[1]);
    fireEvent.click(await screen.findByText('重置密码'));
    fireEvent.click(await screen.findByRole('button', { name: '确认重置' }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith('/api/admin/users/user-2/temporary-password', {
        method: 'POST',
        body: '{}',
      }),
    );
    expect(screen.getByRole('dialog')).toHaveTextContent('旧会话已撤销');
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: '临时密码' })).toHaveValue('one-time-secret'),
    );

    fireEvent.click(screen.getByRole('button', { name: '我已安全记录，关闭' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
