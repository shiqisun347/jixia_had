import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, within } from 'storybook/test';
import { http, HttpResponse } from 'msw';

import GuidePage from '@/app/guide/page';
import { SiteHeader } from '@/components/layout/site-header';

import { MePageView } from './me-page';

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  username: 'linzhi',
  real_name: '林知夏',
  role: 'USER',
  avatar_version: 2,
  default_avatar_key: 'human-03',
  has_custom_avatar: false,
  must_change_password: false,
};

const meta = {
  title: 'Experience/017a Foundation',
  parameters: { layout: 'fullscreen', nextjs: { appDirectory: true } },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const Header: Story = {
  render: () => <SiteHeader />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: '/guide' } },
    msw: {
      handlers: [
        http.get('*/api/auth/me', () => HttpResponse.json({ user })),
        http.get('*/api/users/:userId/avatar', () => new HttpResponse(null, { status: 204 })),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('navigation', { name: '主导航' })).toBeVisible();
    await expect(canvas.getByRole('link', { name: '使用指南' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  },
};

export const Guide: Story = {
  render: () => <GuidePage />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('heading', { name: '一页掌握一场 4v4 辩论' })).toBeVisible();
    await expect(canvas.getByRole('link', { name: /进入比赛大厅/ })).toBeVisible();
  },
};

export const MyPage: Story = {
  render: () => <MePageView />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: '/me' } },
    msw: {
      handlers: [
        http.get('*/api/auth/me', () => HttpResponse.json({ user })),
        http.get('*/api/users/me/summary', () =>
          HttpResponse.json({
            current_match: null,
            matches: 5,
            finished_matches: 4,
            wins: 2,
            average_score: 86.4,
            leaderboard_rank: 8,
            recent_matches: [],
            latest_device_check: null,
          }),
        ),
        http.get('*/api/users/:userId/avatar', () => new HttpResponse(null, { status: 204 })),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const page = within(document.body);
    await expect(await canvas.findByRole('heading', { name: '林知夏' })).toBeVisible();
    await expect(canvas.getByRole('heading', { name: '最近比赛' })).toBeVisible();
    await expect(canvas.queryByRole('heading', { name: '默认头像' })).not.toBeInTheDocument();
    await canvas.getByRole('button', { name: /编辑资料/ }).click();
    await expect(await page.findByRole('dialog', { name: '编辑资料与头像' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '默认头像' })).toBeVisible();
    await page.getByRole('button', { name: '修改密码' }).click();
    await expect(await page.findByRole('dialog', { name: '修改密码' })).toBeVisible();
    await expect(page.getByText('保存后，其他设备上的登录状态会立即失效。')).toBeVisible();
    await page.getByRole('button', { name: '关闭修改密码' }).click();
    await expect(page.queryByRole('dialog', { name: '修改密码' })).not.toBeInTheDocument();
    await expect(page.getByRole('dialog', { name: '编辑资料与头像' })).toBeVisible();
  },
};

export const MyPageWithUploadedAvatar: Story = {
  render: () => <MePageView />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: '/me' } },
    msw: {
      handlers: [
        http.get('*/api/auth/me', () =>
          HttpResponse.json({ user: { ...user, has_custom_avatar: true } }),
        ),
        http.get('*/api/users/me/summary', () =>
          HttpResponse.json({
            current_match: null,
            matches: 0,
            finished_matches: 0,
            wins: 0,
            average_score: 0,
            leaderboard_rank: null,
            recent_matches: [],
            latest_device_check: null,
          }),
        ),
        http.get('*/api/users/:userId/avatar', () => new HttpResponse(null, { status: 204 })),
        http.patch('*/api/users/me/avatar-preset', async ({ request }) => {
          const body = (await request.json()) as { avatar_key: string };
          return HttpResponse.json({
            user: {
              ...user,
              avatar_version: 3,
              default_avatar_key: body.avatar_key,
              has_custom_avatar: true,
            },
          });
        }),
        http.delete('*/api/users/me/avatar', () =>
          HttpResponse.json(
            { error: { code: 'avatar_unavailable', message: '头像服务暂时不可用' } },
            { status: 503 },
          ),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const page = within(document.body);
    await (await canvas.findByRole('button', { name: /编辑资料/ })).click();
    await expect(
      await page.findByText('当前显示上传头像，预设将在删除上传头像后生效。'),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: /^头像 3$/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await page.getByRole('button', { name: /^头像 4$/ }).click();
    await expect(await page.findByText('默认头像已更新；删除上传头像后生效。')).toBeVisible();
    await expect(page.getByRole('button', { name: /^头像 4$/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await page.getByRole('button', { name: '恢复默认头像' }).click();
    const confirm = await page.findByRole('alertdialog', { name: '恢复默认头像？' });
    await expect(confirm).toBeVisible();
    await within(confirm).getByRole('button', { name: '恢复默认头像' }).click();
    await expect(await page.findByText('头像服务暂时不可用')).toBeVisible();
    await expect(confirm).toBeVisible();
  },
};

export const AdminMyPageUsesDebaterIdentity: Story = {
  render: () => <MePageView />,
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: '/me' } },
    msw: {
      handlers: [
        http.get('*/api/auth/me', () => HttpResponse.json({ user: { ...user, role: 'ADMIN' } })),
        http.get('*/api/users/me/summary', () =>
          HttpResponse.json({
            current_match: null,
            matches: 1,
            finished_matches: 1,
            wins: 1,
            average_score: 88,
            leaderboard_rank: 1,
            recent_matches: [],
            latest_device_check: null,
          }),
        ),
        http.get('*/api/users/:userId/avatar', () => new HttpResponse(null, { status: 204 })),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('@linzhi · 辩手')).toBeVisible();
    await expect(canvas.queryByText('@linzhi · 管理员')).not.toBeInTheDocument();
  },
};
