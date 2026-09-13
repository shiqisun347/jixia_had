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

describe('admin page password management', () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    requestJson.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/auth/me') return Promise.resolve({ user: { id: 'admin-1' } });
      if (path === '/api/admin/users/user-2/password' && options?.method === 'POST')
        return Promise.resolve({ status: 'password_changed' });
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

  it('sends the current administrator to the self-service password flow', async () => {
    renderPage();

    await screen.findByText('管理员');
    const actionMenus = screen.getAllByRole('button', { name: '更多操作' });
    expect(actionMenus).toHaveLength(2);
    fireEvent.pointerDown(actionMenus[0]);
    expect(await screen.findByText('修改自己的密码')).toBeVisible();
  });

  it('sets a chosen password and warns about revoked sessions', async () => {
    renderPage();

    fireEvent.pointerDown((await screen.findAllByRole('button', { name: '更多操作' }))[1]);
    fireEvent.click(await screen.findByText('修改密码'));
    fireEvent.change(screen.getByLabelText('新密码'), {
      target: { value: 'chosen-password-123' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存新密码' }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith('/api/admin/users/user-2/password', {
        method: 'POST',
        body: JSON.stringify({ new_password: 'chosen-password-123' }),
      }),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
