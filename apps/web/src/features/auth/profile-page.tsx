'use client';

import { useQueryClient } from '@tanstack/react-query';
import { ImagePlus, LoaderCircle, LogOut, Trash2 } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';

import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { ApiClientError, authApi, avatarUrl } from '@/lib/auth-api';
import { useToast } from '@/components/ui/toast-provider';

import { AuthLoading } from './auth-loading';
import { AuthShell } from './auth-shell';
import { authQueryKey, useCurrentUser } from './use-auth';
import { useAvatarUpdate } from './use-avatar-update';
import { useLogout } from './use-logout';

export function ProfilePageView() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const query = useCurrentUser();
  const { showToast } = useToast();
  const { logout, isLoggingOut } = useLogout();
  const { isUpdatingAvatar, updateAvatar } = useAvatarUpdate();
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const [restoreAvatarOpen, setRestoreAvatarOpen] = useState(false);
  const [restoreAvatarError, setRestoreAvatarError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useForm<{ real_name: string }>();

  useEffect(() => {
    if (
      query.data === null ||
      (query.error instanceof ApiClientError && query.error.status === 401)
    ) {
      router.replace('/login?return_to=%2Fprofile');
    }
    if (query.data?.user.must_change_password) router.replace('/change-password');
  }, [query.data, query.error, router]);

  if (query.isLoading || !query.data) return <AuthLoading label="正在加载资料" />;
  const user = query.data.user;

  return (
    <AuthShell
      eyebrow="YOUR PROFILE"
      title="个人资料"
      description="比赛里只展示真实姓名。用户名和角色由系统保护，不能在这里修改。"
      footer={
        <p className="text-center text-sm text-slate-500">
          <Link className="font-bold text-blue-700" href="/">
            返回首页
          </Link>{' '}
          ·{' '}
          <Link className="font-bold text-blue-700" href="/change-password">
            修改密码
          </Link>
        </p>
      }
    >
      <div className="mb-7 flex items-center gap-4 rounded-2xl border border-blue-100 bg-blue-50/60 p-4">
        <Image
          className="size-16 rounded-full border border-white object-cover shadow-sm"
          src={avatarUrl(user)}
          alt=""
          width={64}
          height={64}
          unoptimized
        />
        <div className="min-w-0">
          <p className="truncate text-lg font-black text-slate-950">{user.real_name}</p>
          <p className="mt-1 text-xs font-semibold text-slate-500">
            @{user.username} · {user.role === 'ADMIN' ? '管理员' : '普通用户'}
          </p>
        </div>
      </div>
      <form
        className="space-y-5"
        onSubmit={form.handleSubmit(async (values) => {
          setBusy(true);
          try {
            const result = await authApi.updateProfile(values.real_name);
            queryClient.setQueryData(authQueryKey, result);
            showToast({ message: '资料已保存。', tone: 'success' });
          } catch (error) {
            showToast({
              message: error instanceof Error ? error.message : '保存失败，请稍后重试。',
              tone: 'error',
            });
          } finally {
            setBusy(false);
          }
        })}
      >
        <label className="block text-sm font-bold text-slate-700">
          真实姓名
          <input
            {...form.register('real_name', {
              required: '请输入真实姓名',
              minLength: { value: 2, message: '至少 2 个字符' },
            })}
            defaultValue={user.real_name}
            className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
            autoComplete="name"
          />
          {form.formState.errors.real_name ? (
            <span className="mt-1 block text-xs font-semibold text-rose-600">
              {form.formState.errors.real_name.message}
            </span>
          ) : null}
        </label>
        <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-blue-200 bg-blue-50/40 px-4 py-3 text-sm font-semibold text-slate-600">
          <ImagePlus className="size-5 text-blue-600" />
          <span className="min-w-0 flex-1 truncate">
            {file?.name ?? '更换头像（JPEG、PNG、WebP，2 MB 内）'}
          </span>
          <input
            className="sr-only"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {file ? (
          <button
            className="jx-disabled-command inline-flex items-center gap-2 rounded-lg border border-transparent px-2 py-1 text-xs font-bold text-blue-700 hover:bg-blue-50"
            disabled={busy || isUpdatingAvatar}
            type="button"
            onClick={() => {
              void updateAvatar(
                () => authApi.uploadAvatar(file),
                '头像已更新。',
                '头像上传失败。',
              ).then((succeeded) => {
                if (succeeded) setFile(null);
              });
            }}
          >
            {isUpdatingAvatar ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <ImagePlus className="size-4" />
            )}
            {isUpdatingAvatar ? '正在更新头像…' : '上传新头像'}
          </button>
        ) : null}
        <div className="flex gap-3">
          <button
            className="jx-disabled-command inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-slate-950 bg-slate-950 px-4 py-3 text-sm font-black text-white"
            disabled={busy || isUpdatingAvatar}
            type="submit"
          >
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : null}保存姓名
          </button>
          <button
            className="jx-disabled-command inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-rose-200 px-4 py-3 text-sm font-bold text-rose-700 hover:bg-rose-50"
            disabled={busy || isUpdatingAvatar}
            type="button"
            onClick={() => setRestoreAvatarOpen(true)}
          >
            {isUpdatingAvatar ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Trash2 className="size-4" />
            )}
            {isUpdatingAvatar ? '处理中…' : '恢复默认'}
          </button>
        </div>
      </form>
      <button
        className="jx-disabled-command mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 px-4 py-3 text-sm font-bold text-slate-600 hover:bg-slate-50"
        disabled={busy || isUpdatingAvatar || isLoggingOut}
        type="button"
        onClick={() => setLogoutConfirmOpen(true)}
      >
        <LogOut className="size-4" />
        退出登录
      </button>
      <ConfirmDialog
        confirmLabel="恢复默认头像"
        description={restoreAvatarError ?? '当前上传头像会被删除，并立即切换为已选择的默认头像。'}
        loading={isUpdatingAvatar}
        onConfirm={() =>
          void updateAvatar(authApi.deleteAvatar, '已恢复默认头像。', '恢复默认头像失败。').then(
            (succeeded) => {
              if (succeeded) {
                setRestoreAvatarError(null);
                setRestoreAvatarOpen(false);
              } else {
                setRestoreAvatarError('恢复失败，请检查网络后重试。');
              }
            },
          )
        }
        onOpenChange={(open) => {
          if (!open) setRestoreAvatarError(null);
          setRestoreAvatarOpen(open);
        }}
        open={restoreAvatarOpen}
        title="恢复默认头像？"
      />
      <ConfirmDialog
        confirmLabel="退出登录"
        description="退出后需要重新输入用户名和密码才能继续使用需要登录的功能。"
        loading={isLoggingOut}
        onConfirm={() => void logout()}
        onOpenChange={setLogoutConfirmOpen}
        open={logoutConfirmOpen}
        title="确认退出登录？"
      />
    </AuthShell>
  );
}
