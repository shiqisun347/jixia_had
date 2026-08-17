'use client';

import { useQueryClient } from '@tanstack/react-query';
import { ArrowRight, LoaderCircle } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';

import { ApiClientError, authApi } from '@/lib/auth-api';
import { useToast } from '@/components/ui/toast-provider';
import { sanitizeReturnTo } from '@/lib/return-to';

import { AuthLoading } from './auth-loading';
import { AuthShell } from './auth-shell';
import { authQueryKey, useCurrentUser } from './use-auth';

export function ChangePasswordPageView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const query = useCurrentUser();
  const { showToast } = useToast();
  const [busy, setBusy] = useState(false);
  const requestedReturnTo = searchParams.get('return_to');
  const returnTo = sanitizeReturnTo(requestedReturnTo, '/me');
  const form = useForm<{
    current_password: string;
    new_password: string;
    confirm_password: string;
  }>();
  useEffect(() => {
    if (
      query.data === null ||
      (query.error instanceof ApiClientError && query.error.status === 401)
    )
      router.replace('/login?return_to=%2Fchange-password');
  }, [query.data, query.error, router]);
  if (query.isLoading || !query.data) return <AuthLoading label="正在加载改密页面" />;
  return (
    <AuthShell
      eyebrow="SECURE YOUR ACCOUNT"
      title="修改密码"
      description={
        query.data.user.must_change_password
          ? '这是管理员发放的临时密码，请先设置一个只有你知道的新密码。'
          : '修改密码后，其他设备上的登录状态会立即失效。'
      }
      footer={
        <p className="text-center text-sm text-slate-500">
          <Link className="font-bold text-blue-700" href="/profile">
            返回资料
          </Link>
        </p>
      }
    >
      <form
        className="space-y-5"
        onSubmit={form.handleSubmit(async (values) => {
          form.clearErrors('confirm_password');
          if (values.new_password !== values.confirm_password) {
            form.setError('confirm_password', {
              type: 'validate',
              message: '两次输入的新密码不一致。',
            });
            return;
          }
          setBusy(true);
          try {
            const result = await authApi.changePassword({
              current_password: values.current_password,
              new_password: values.new_password,
            });
            queryClient.setQueryData(authQueryKey, result);
            router.replace(returnTo);
          } catch (reason) {
            showToast({
              message: reason instanceof Error ? reason.message : '修改失败，请稍后重试。',
              tone: 'error',
            });
          } finally {
            setBusy(false);
          }
        })}
      >
        <label className="block text-sm font-bold text-slate-700">
          当前密码
          <input
            {...form.register('current_password', { required: '请输入当前密码' })}
            className="mt-2 block w-full rounded-xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
            type="password"
            autoComplete="current-password"
          />
          {form.formState.errors.current_password ? (
            <span className="mt-1.5 block text-xs font-semibold text-rose-600">
              {form.formState.errors.current_password.message}
            </span>
          ) : null}
        </label>
        <label className="block text-sm font-bold text-slate-700">
          新密码
          <input
            {...form.register('new_password', {
              required: '请输入新密码',
              minLength: { value: 8, message: '至少 8 个字符' },
            })}
            className="mt-2 block w-full rounded-xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
            type="password"
            autoComplete="new-password"
          />
          {form.formState.errors.new_password ? (
            <span className="mt-1.5 block text-xs font-semibold text-rose-600">
              {form.formState.errors.new_password.message}
            </span>
          ) : null}
        </label>
        <label className="block text-sm font-bold text-slate-700">
          确认新密码
          <input
            {...form.register('confirm_password', { required: '请再次输入新密码' })}
            className="mt-2 block w-full rounded-xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
            type="password"
            autoComplete="new-password"
          />
          {form.formState.errors.confirm_password ? (
            <span className="mt-1.5 block text-xs font-semibold text-rose-600">
              {form.formState.errors.confirm_password.message}
            </span>
          ) : null}
        </label>
        <button
          className="jx-disabled-command inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-slate-950 bg-slate-950 px-5 py-3 text-sm font-black text-white"
          disabled={busy}
          type="submit"
        >
          {busy ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <ArrowRight className="size-4" />
          )}
          保存新密码
        </button>
      </form>
    </AuthShell>
  );
}
