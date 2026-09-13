import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminUsersPage from './page';

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  username: 'participant01',
  real_name: '测试用户',
  role: 'USER',
  status: 'ACTIVE',
  match_count: 1,
  finished_count: 1,
  wins: 0,
  points: 0,
  average_personal_score: 0,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdminUsersPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('admin user password management', () => {
  afterEach(cleanup);

  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/api/admin/users?')) {
        return Promise.resolve({ items: [user], page: 1, page_size: 25, total: 1, total_pages: 1 });
      }
      if (path === `/api/admin/users/${user.id}/password` && options?.method === 'POST') {
        return Promise.resolve({ status: 'password_changed' });
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
  });

  it('sets an explicit password and never requests a temporary password', async () => {
    renderPage();
    await screen.findByText('测试用户');
    fireEvent.pointerDown(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(await screen.findByText('修改密码'));
    fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'NewPassword123' } });
    fireEvent.click(screen.getByRole('button', { name: '保存新密码' }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(`/api/admin/users/${user.id}/password`, {
        method: 'POST',
        body: JSON.stringify({ new_password: 'NewPassword123' }),
      }),
    );
    expect(
      requestJson.mock.calls.some(([path]) => String(path).includes('temporary-password')),
    ).toBe(false);
    expect(await screen.findByRole('status')).toHaveTextContent('旧会话已撤销');
  });
});
