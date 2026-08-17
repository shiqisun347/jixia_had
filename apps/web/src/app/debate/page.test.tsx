import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/features/auth/use-auth', () => ({
  useCurrentUser: () => ({
    isLoading: false,
    data: { user: { must_change_password: false } },
    error: null,
  }),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/debate',
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

import DebatePage from './page';

describe('debate route', () => {
  it('does not render prototype match state without a real match id', () => {
    render(<DebatePage />);

    expect(screen.getByRole('heading', { name: '请选择一场比赛' })).toBeVisible();
    expect(screen.getByRole('link', { name: '返回公开大厅' })).toHaveAttribute('href', '/lobby');
  });
});
