import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

let pathname = '/admin';

vi.mock('next/navigation', () => ({ usePathname: () => pathname }));

import { AdminShell } from './admin-shell';

afterEach(cleanup);

const administrator = {
  avatar_version: 0,
  default_avatar_key: 'human-01',
  has_custom_avatar: false,
  id: '00000000-0000-4000-8000-000000000001',
  must_change_password: false,
  real_name: '系统管理员',
  role: 'ADMIN',
  username: 'admin',
};

describe('AdminShell', () => {
  it('exposes the approved desktop information architecture', () => {
    pathname = '/admin';
    render(<AdminShell user={administrator}>管理内容</AdminShell>);

    expect(screen.getByRole('navigation', { name: '后台导航' })).toBeVisible();
    for (const label of [
      '运营概览',
      '用户',
      '模型',
      '音色',
      '辩题',
      '赛制规则',
      'Agent 管理',
      '比赛与数据',
      '裁判结果',
      'API 请求日志',
      '事故',
      '后台任务',
      '审计日志',
      '系统设置',
    ]) {
      expect(screen.getByRole('link', { name: label })).toBeVisible();
    }
    expect(screen.queryByRole('button', { name: '打开后台导航' })).not.toBeInTheDocument();
  });

  it('marks nested route navigation as active', () => {
    pathname = '/admin/rules/formal-4v4';
    render(<AdminShell user={administrator}>赛制内容</AdminShell>);

    expect(screen.getByRole('link', { name: '赛制规则' })).toHaveAttribute('aria-current', 'page');
  });
});
