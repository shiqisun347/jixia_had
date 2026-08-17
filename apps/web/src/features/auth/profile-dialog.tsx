'use client';

import { ImagePlus, KeyRound, LoaderCircle, Save, Trash2, X } from 'lucide-react';
import Image from 'next/image';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Dialog } from 'radix-ui';
import { useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { avatarAssetUrl, HUMAN_AVATAR_KEYS } from '@/lib/avatar-catalog';
import { authApi, avatarUrl, type ApiUser } from '@/lib/auth-api';

import { authQueryKey } from './use-auth';
import { useAvatarUpdate } from './use-avatar-update';

export function ProfileDialog({
  open,
  onOpenChange,
  user,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: ApiUser;
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { isUpdatingAvatar, updateAvatar } = useAvatarUpdate();
  const [file, setFile] = useState<File | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [restoreAvatarOpen, setRestoreAvatarOpen] = useState(false);
  const [restoreAvatarError, setRestoreAvatarError] = useState<string | null>(null);
  const form = useForm<{ real_name: string }>({ defaultValues: { real_name: user.real_name } });
  const passwordForm = useForm<{
    current_password: string;
    new_password: string;
    confirm_password: string;
  }>();
  const busy = savingProfile || isUpdatingAvatar;
  const passwordBusy = savingPassword;

  async function saveProfile(values: { real_name: string }) {
    if (busy) return;
    setSavingProfile(true);
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
      setSavingProfile(false);
    }
  }

  return (
    <>
      <Dialog.Root
        open={open}
        onOpenChange={(nextOpen) => {
          if (busy) return;
          if (!nextOpen) {
            form.reset({ real_name: user.real_name });
            setFile(null);
          }
          onOpenChange(nextOpen);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[100] bg-slate-950/35 backdrop-blur-[2px] data-[state=closed]:animate-out data-[state=open]:animate-in" />
          <Dialog.Content className="fixed inset-y-0 right-0 z-[101] flex w-full max-w-xl flex-col border-l border-blue-100 bg-[#fbfdff] shadow-[-18px_0_55px_rgba(15,23,42,0.16)] outline-none">
            <header className="flex shrink-0 items-start justify-between gap-5 border-b border-blue-100 bg-white px-6 py-5">
              <div>
                <p className="text-xs font-black text-blue-600">个人资料</p>
                <Dialog.Title className="mt-1 text-xl font-black text-slate-950">
                  编辑资料与头像
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-sm leading-6 text-slate-500">
                  比赛中只展示真实姓名和当前头像。
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="关闭资料编辑" disabled={busy} size="icon" variant="ghost">
                  <X className="size-5" aria-hidden="true" />
                </Button>
              </Dialog.Close>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
              <div className="flex items-center gap-4 rounded-2xl border border-blue-100 bg-white p-4 shadow-sm">
                <Image
                  alt={`${user.real_name}的头像`}
                  className="jx-identity-avatar size-20 rounded-full border border-blue-100 object-cover"
                  height={80}
                  src={avatarUrl(user)}
                  unoptimized
                  width={80}
                />
                <div className="min-w-0">
                  <p className="truncate text-lg font-black text-slate-950">{user.real_name}</p>
                  <p className="mt-1 text-sm text-slate-500">@{user.username}</p>
                  <p className="mt-2 text-xs font-bold text-blue-700">用户端身份：辩手</p>
                </div>
              </div>

              <form className="mt-6" onSubmit={form.handleSubmit(saveProfile)}>
                <label className="block text-sm font-bold text-slate-700">
                  真实姓名
                  <input
                    {...form.register('real_name', {
                      required: '请输入真实姓名',
                      minLength: { value: 2, message: '至少 2 个字符' },
                    })}
                    aria-describedby={
                      form.formState.errors.real_name ? 'profile-real-name-error' : undefined
                    }
                    aria-invalid={form.formState.errors.real_name ? true : undefined}
                    autoComplete="name"
                    className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                  />
                </label>
                {form.formState.errors.real_name ? (
                  <p
                    className="mt-1 text-xs font-semibold text-red-600"
                    id="profile-real-name-error"
                    role="alert"
                  >
                    {form.formState.errors.real_name.message}
                  </p>
                ) : null}
                <Button className="mt-3" disabled={busy} type="submit" variant="primary">
                  {savingProfile ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Save className="size-4" aria-hidden="true" />
                  )}
                  {savingProfile ? '正在保存' : '保存姓名'}
                </Button>
              </form>

              <section
                className="mt-8 border-t border-slate-200 pt-6"
                aria-labelledby="avatar-title"
              >
                <h2 id="avatar-title" className="text-lg font-black text-slate-950">
                  默认头像
                </h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">
                  上传头像会优先显示；删除后恢复这里选择的预设。
                </p>
                {user.has_custom_avatar ? (
                  <p className="mt-2 text-xs font-bold text-blue-700">
                    当前显示上传头像，预设将在删除上传头像后生效。
                  </p>
                ) : null}
                <div className="mt-4 grid grid-cols-4 gap-3 sm:grid-cols-8">
                  {HUMAN_AVATAR_KEYS.map((key, index) => (
                    <button
                      aria-label={`头像 ${index + 1}`}
                      aria-pressed={user.default_avatar_key === key}
                      className={`avatar-preset ${user.default_avatar_key === key ? 'is-selected' : ''}`}
                      disabled={busy}
                      key={key}
                      onClick={() =>
                        void updateAvatar(
                          () => authApi.selectAvatar(key),
                          user.has_custom_avatar
                            ? '默认头像已更新；删除上传头像后生效。'
                            : '默认头像已更新。',
                          '头像更新失败。',
                        )
                      }
                      type="button"
                    >
                      <Image
                        alt=""
                        className="jx-identity-avatar size-full rounded-full object-cover"
                        height={72}
                        src={avatarAssetUrl(key)}
                        width={72}
                      />
                    </button>
                  ))}
                </div>
              </section>

              <section
                className="mt-8 border-t border-slate-200 pt-6"
                aria-labelledby="upload-title"
              >
                <h2 id="upload-title" className="text-lg font-black text-slate-950">
                  上传头像
                </h2>
                <label className="mt-4 flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-blue-200 bg-blue-50/50 px-4 py-3 text-sm font-semibold text-slate-600">
                  <ImagePlus className="size-5 shrink-0 text-blue-600" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">
                    {file?.name ?? '选择 JPEG、PNG 或 WebP，最大 2 MB'}
                  </span>
                  <input
                    accept="image/jpeg,image/png,image/webp"
                    className="sr-only"
                    disabled={busy}
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                    type="file"
                  />
                </label>
                <div className="mt-3 flex flex-wrap gap-2">
                  {file ? (
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void updateAvatar(
                          () => authApi.uploadAvatar(file),
                          '头像已更新。',
                          '头像上传失败。',
                        ).then((succeeded) => {
                          if (succeeded) setFile(null);
                        })
                      }
                      variant="primary"
                    >
                      {isUpdatingAvatar ? (
                        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <ImagePlus className="size-4" aria-hidden="true" />
                      )}
                      上传新头像
                    </Button>
                  ) : null}
                  {user.has_custom_avatar ? (
                    <Button
                      disabled={busy}
                      onClick={() => setRestoreAvatarOpen(true)}
                      variant="danger"
                    >
                      <Trash2 className="size-4" aria-hidden="true" /> 恢复默认头像
                    </Button>
                  ) : null}
                </div>
              </section>

              <section className="mt-8 border-t border-slate-200 pt-6">
                <h2 className="text-lg font-black text-slate-950">账号安全</h2>
                <Button
                  className="mt-3"
                  onClick={() => setPasswordOpen(true)}
                  type="button"
                  variant="secondary"
                >
                  <KeyRound className="size-4" aria-hidden="true" /> 修改密码
                </Button>
              </section>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
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
      <Dialog.Root
        open={passwordOpen}
        onOpenChange={(open) => {
          if (passwordBusy) return;
          if (!open) passwordForm.reset();
          setPasswordOpen(open);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[110] bg-slate-950/40 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[111] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-blue-100 bg-[#fbfdff] p-6 shadow-[0_24px_80px_rgba(15,23,42,0.22)] outline-none sm:p-8">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-black text-blue-600">账号安全</p>
                <Dialog.Title className="mt-1 text-xl font-black text-slate-950">
                  修改密码
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-sm leading-6 text-slate-500">
                  保存后，其他设备上的登录状态会立即失效。
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button
                  aria-label="关闭修改密码"
                  disabled={passwordBusy}
                  size="icon"
                  variant="ghost"
                >
                  <X className="size-5" aria-hidden="true" />
                </Button>
              </Dialog.Close>
            </div>
            <form
              className="mt-6 space-y-4"
              onSubmit={passwordForm.handleSubmit(async (values) => {
                passwordForm.clearErrors('confirm_password');
                if (values.new_password !== values.confirm_password) {
                  passwordForm.setError('confirm_password', {
                    type: 'validate',
                    message: '两次输入的新密码不一致。',
                  });
                  return;
                }
                setSavingPassword(true);
                try {
                  const result = await authApi.changePassword({
                    current_password: values.current_password,
                    new_password: values.new_password,
                  });
                  queryClient.setQueryData(authQueryKey, result);
                  passwordForm.reset();
                  setPasswordOpen(false);
                  showToast({ message: '密码已更新。', tone: 'success' });
                } catch (error) {
                  showToast({
                    message: error instanceof Error ? error.message : '修改失败，请稍后重试。',
                    tone: 'error',
                  });
                } finally {
                  setSavingPassword(false);
                }
              })}
            >
              <div>
                <label className="block text-sm font-bold text-slate-700">
                  当前密码
                  <input
                    {...passwordForm.register('current_password', { required: '请输入当前密码' })}
                    aria-describedby={
                      passwordForm.formState.errors.current_password
                        ? 'profile-current-password-error'
                        : undefined
                    }
                    aria-invalid={passwordForm.formState.errors.current_password ? true : undefined}
                    autoComplete="current-password"
                    className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                    type="password"
                  />
                </label>
                {passwordForm.formState.errors.current_password ? (
                  <span
                    className="mt-1 block text-xs font-semibold text-rose-600"
                    id="profile-current-password-error"
                    role="alert"
                  >
                    {passwordForm.formState.errors.current_password.message}
                  </span>
                ) : null}
              </div>
              <div>
                <label className="block text-sm font-bold text-slate-700">
                  新密码
                  <input
                    {...passwordForm.register('new_password', {
                      required: '请输入新密码',
                      minLength: { value: 8, message: '至少 8 个字符' },
                    })}
                    aria-describedby={
                      passwordForm.formState.errors.new_password
                        ? 'profile-new-password-error'
                        : undefined
                    }
                    aria-invalid={passwordForm.formState.errors.new_password ? true : undefined}
                    autoComplete="new-password"
                    className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                    type="password"
                  />
                </label>
                {passwordForm.formState.errors.new_password ? (
                  <span
                    className="mt-1 block text-xs font-semibold text-rose-600"
                    id="profile-new-password-error"
                    role="alert"
                  >
                    {passwordForm.formState.errors.new_password.message}
                  </span>
                ) : null}
              </div>
              <div>
                <label className="block text-sm font-bold text-slate-700">
                  确认新密码
                  <input
                    {...passwordForm.register('confirm_password', {
                      required: '请再次输入新密码',
                    })}
                    aria-describedby={
                      passwordForm.formState.errors.confirm_password
                        ? 'profile-confirm-password-error'
                        : undefined
                    }
                    aria-invalid={passwordForm.formState.errors.confirm_password ? true : undefined}
                    autoComplete="new-password"
                    className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                    type="password"
                  />
                </label>
                {passwordForm.formState.errors.confirm_password ? (
                  <span
                    className="mt-1 block text-xs font-semibold text-rose-600"
                    id="profile-confirm-password-error"
                    role="alert"
                  >
                    {passwordForm.formState.errors.confirm_password.message}
                  </span>
                ) : null}
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <Button
                  disabled={passwordBusy}
                  onClick={() => setPasswordOpen(false)}
                  type="button"
                  variant="ghost"
                >
                  取消
                </Button>
                <Button disabled={passwordBusy} type="submit" variant="primary">
                  {passwordBusy ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <KeyRound className="size-4" />
                  )}
                  {passwordBusy ? '正在保存' : '保存新密码'}
                </Button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
