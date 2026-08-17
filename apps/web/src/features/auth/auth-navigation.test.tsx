import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiClientError, authApi, type AuthResponse } from '@/lib/auth-api';

import { authQueryKey } from './use-auth';
import { AuthNavigation } from './auth-navigation';

let pathname = '/';
let search = '';

vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(search),
}));

vi.mock('@/lib/auth-api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/auth-api')>();
  return {
    ...original,
    authApi: { ...original.authApi, currentUser: vi.fn(), logout: vi.fn() },
  };
});

function renderNavigation(role: 'USER' | 'ADMIN') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
  client.setQueryData(authQueryKey, {
    user: {
      id: `user-${role.toLowerCase()}`,
      username: role.toLowerCase(),
      real_name: role === 'ADMIN' ? '平台管理员' : '林知夏',
      role,
      must_change_password: false,
      avatar_version: 0,
      default_avatar_key: 'human-01',
      has_custom_avatar: false,
    },
  } satisfies AuthResponse);
  return render(
    <QueryClientProvider client={client}>
      <AuthNavigation />
    </QueryClientProvider>,
  );
}

describe('AuthNavigation', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    pathname = '/';
    search = '';
  });

  it('keeps the room-entry query when the login state is temporarily unavailable', () => {
    pathname = '/lobby';
    search = 'join=1';
    render(<AuthNavigation />);

    expect(screen.getByRole('link', { name: '登录' })).toHaveAttribute(
      'href',
      '/login?return_to=%2Flobby%3Fjoin%3D1',
    );
    expect(screen.getByRole('link', { name: '注册' })).toHaveAttribute(
      'href',
      '/register?return_to=%2Flobby%3Fjoin%3D1',
    );
  });

  it('shows the normal anonymous navigation without an error retry', async () => {
    vi.mocked(authApi.currentUser).mockRejectedValue(
      new ApiClientError(401, {
        error: { code: 'not_authenticated', message: '请先登录' },
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <AuthNavigation />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('link', { name: '登录' })).toBeVisible();
    expect(screen.getByRole('link', { name: '注册' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '重试登录状态' })).not.toBeInTheDocument();
  });

  it('links a regular user directly to their page', () => {
    renderNavigation('USER');

    expect(screen.getByRole('link', { name: '进入林知夏的个人页面' })).toHaveAttribute(
      'href',
      '/me',
    );
    expect(screen.queryByText('管理后台')).not.toBeInTheDocument();
    expect(screen.queryByText('修改密码')).not.toBeInTheDocument();
  });

  it('keeps the administrator role menu without a duplicate password entry', () => {
    renderNavigation('ADMIN');

    expect(screen.getByRole('button', { name: '打开平台管理员的账号菜单' })).toBeVisible();
    expect(screen.getByRole('link', { name: '我的页面' })).toHaveAttribute('href', '/me');
    expect(screen.getByRole('link', { name: '管理后台' })).toHaveAttribute('href', '/admin');
    expect(screen.queryByText('修改密码')).not.toBeInTheDocument();
  });
});
