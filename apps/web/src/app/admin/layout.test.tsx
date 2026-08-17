import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClientError } from '@/lib/auth-api';

const replace = vi.fn();
const refetch = vi.fn();
let authState: {
  isLoading: boolean;
  data?: { user: { role: string; must_change_password: boolean } };
  error: unknown;
  refetch: typeof refetch;
};

vi.mock('@/features/auth/use-auth', () => ({ useCurrentUser: () => authState }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/admin/catalog',
  useRouter: () => ({ replace }),
}));

import AdminLayout from './layout';

describe('admin layout', () => {
  afterEach(cleanup);

  beforeEach(() => {
    replace.mockReset();
    refetch.mockReset();
    authState = {
      isLoading: false,
      data: { user: { role: 'ADMIN', must_change_password: false } },
      error: null,
      refetch,
    };
  });

  it('renders admin content for administrators', () => {
    render(<AdminLayout>管理内容</AdminLayout>);
    expect(screen.getByText('管理内容')).toBeVisible();
  });

  it('hides admin content from ordinary users', () => {
    authState.data = { user: { role: 'USER', must_change_password: false } };
    render(<AdminLayout>管理内容</AdminLayout>);
    expect(screen.queryByText('管理内容')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '需要管理员权限' })).toBeVisible();
  });

  it('redirects unauthenticated users to login with the original path', async () => {
    authState.data = undefined;
    authState.error = new ApiClientError(401, {});
    render(<AdminLayout>管理内容</AdminLayout>);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith('/login?return_to=%2Fadmin%2Fcatalog'),
    );
    expect(screen.getByRole('status')).toHaveTextContent('正在确认管理权限');
  });

  it('explains a temporary auth service failure and allows retry', () => {
    authState.data = undefined;
    authState.error = new ApiClientError(503, {});
    render(<AdminLayout>管理内容</AdminLayout>);

    expect(screen.getByRole('heading', { name: '无法确认管理权限' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    expect(refetch).toHaveBeenCalledOnce();
  });
});
