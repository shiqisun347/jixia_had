import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, within } from 'storybook/test';
import { http, HttpResponse } from 'msw';

import { LoginForm, ProfilePageView, RegisterForm, TermsPageView } from './index';

const user = {
  id: '00000000-0000-0000-0000-000000000003',
  username: 'demo_user',
  real_name: '演示用户',
  role: 'USER' as const,
  status: 'ACTIVE' as const,
  must_change_password: false,
  avatar_version: 0,
};

const termsHandler = http.get('*/api/legal/platform-terms/current', () =>
  HttpResponse.json({
    version: '2026-08-01',
    title: '稷下平台条款',
    body: '本平台用于人机辩论实验。登录用户可查看正常完赛的文字记录和评分。',
  }),
);

const authenticatedHandler = http.get('*/api/auth/me', () => HttpResponse.json({ user }));

const avatarHandler = http.get(
  '*/api/users/:userId/avatar',
  () => new HttpResponse(null, { status: 204 }),
);

const meta = {
  title: 'Auth/Flows',
  parameters: { layout: 'fullscreen', nextjs: { appDirectory: true } },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const Login: Story = {
  render: () => <LoginForm />,
  async play({ canvasElement }) {
    await expect(
      await within(canvasElement).findByRole('heading', { name: '继续你的辩论' }),
    ).toBeVisible();
    await expect(
      await within(canvasElement).findByRole('button', { name: '登录并进入' }),
    ).toBeVisible();
  },
};

export const Register: Story = {
  render: () => <RegisterForm />,
  parameters: { msw: { handlers: [termsHandler] } },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('heading', { name: '创建你的账号' })).toBeVisible();
    await expect(canvas.getByText('我已阅读并同意')).toBeVisible();
  },
};

export const Profile: Story = {
  render: () => <ProfilePageView />,
  parameters: { msw: { handlers: [authenticatedHandler, avatarHandler] } },
  async play({ canvasElement }) {
    await expect(
      await within(canvasElement).findByRole('heading', { name: '个人资料' }),
    ).toBeVisible();
    await expect(await within(canvasElement).findByText('演示用户')).toBeVisible();
  },
};

export const Terms: Story = {
  render: () => <TermsPageView />,
  parameters: { msw: { handlers: [termsHandler] } },
  async play({ canvasElement }) {
    await expect(within(canvasElement).getByRole('heading', { name: '平台条款' })).toBeVisible();
    await expect(await within(canvasElement).findByText('版本 2026-08-01')).toBeVisible();
  },
};

export const RegisterTermsFailure: Story = {
  render: () => <RegisterForm />,
  parameters: {
    msw: {
      handlers: [
        http.get('*/api/legal/platform-terms/current', () =>
          HttpResponse.json({ error: { message: '条款暂时不可用' } }, { status: 503 }),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByTestId('register-terms-error')).toBeVisible();
    await expect(await canvas.findByRole('button', { name: '重新加载条款' })).toBeVisible();
    await expect(await canvas.findByRole('button', { name: '创建账号' })).toBeDisabled();
  },
};

export const TermsFailure: Story = {
  render: () => <TermsPageView />,
  parameters: {
    msw: {
      handlers: [
        http.get('*/api/legal/platform-terms/current', () =>
          HttpResponse.json({ error: { message: '条款暂时不可用' } }, { status: 503 }),
        ),
      ],
    },
  },
  async play({ canvasElement }) {
    const canvas = within(canvasElement);
    await expect(await canvas.findByTestId('terms-page-error')).toBeVisible();
    await expect(await canvas.findByRole('button', { name: '重新加载条款' })).toBeVisible();
  },
};
