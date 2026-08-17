'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Check, Eye, EyeOff, LoaderCircle, RefreshCcw, Upload } from 'lucide-react';
import Link from 'next/link';
import Image from 'next/image';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import {
  useForm,
  useWatch,
  type FieldValues,
  type Path,
  type UseFormRegister,
} from 'react-hook-form';
import { z } from 'zod';

import { ApiClientError, authApi } from '@/lib/auth-api';
import { useToast } from '@/components/ui/toast-provider';
import { authHrefWithReturnTo, sanitizeAuthReturnTo } from '@/lib/return-to';
import { avatarAssetUrl, HUMAN_AVATAR_KEYS } from '@/lib/avatar-catalog';

import { AuthShell } from './auth-shell';
import { authQueryKey } from './use-auth';

const inputClass =
  'mt-2 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100';
const buttonClass =
  'jx-disabled-command inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-slate-950 bg-slate-950 px-5 py-3 text-sm font-black text-white shadow-[0_12px_30px_rgba(15,23,42,0.18)] transition hover:border-blue-700 hover:bg-blue-700';

function errorMessage(error: unknown): string | null {
  if (error instanceof ApiClientError) return error.message;
  if (error instanceof Error) return error.message;
  return error ? '请求失败，请稍后重试。' : null;
}

function FieldError({ id, message }: Readonly<{ id: string; message?: string }>) {
  return message ? (
    <p className="mt-1.5 text-xs font-semibold text-rose-600" id={id} role="alert">
      {message}
    </p>
  ) : null;
}

function PasswordInput<TFieldValues extends FieldValues>({
  name,
  label,
  register,
  error,
}: Readonly<{
  name: Path<TFieldValues>;
  label: string;
  register: UseFormRegister<TFieldValues>;
  error?: string;
}>) {
  const [visible, setVisible] = useState(false);
  const fieldName = String(name);
  const inputId = `auth-${fieldName}`;
  const errorId = `${fieldName}-error`;
  const fieldError = error ? { 'aria-invalid': true, 'aria-describedby': errorId } : {};
  return (
    <div>
      <label className="block text-sm font-bold text-slate-700" htmlFor={inputId}>
        {label}
      </label>
      <span className="relative block">
        <input
          {...register(name)}
          {...fieldError}
          className={`${inputClass} pr-12`}
          id={inputId}
          type={visible ? 'text' : 'password'}
        />
        <button
          className="absolute right-2 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-lg text-slate-500 hover:bg-slate-50 hover:text-slate-700"
          onClick={() => setVisible((current) => !current)}
          type="button"
          aria-label={visible ? '隐藏密码' : '显示密码'}
        >
          {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </span>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

const loginSchema = z.object({
  username: z.string().trim().min(3, '请输入 3–32 个字符的用户名').max(32),
  password: z.string().min(1, '请输入密码').max(64),
});
type LoginValues = z.infer<typeof loginSchema>;

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [submitting, setSubmitting] = useState(false);
  const sessionExpiredNoticeShown = useRef(false);
  const returnTo = sanitizeAuthReturnTo(searchParams.get('return_to'));
  const registerHref =
    returnTo === '/' ? '/register' : `/register?return_to=${encodeURIComponent(returnTo)}`;
  const form = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  useEffect(() => {
    if (searchParams.get('reason') === 'session_expired' && !sessionExpiredNoticeShown.current) {
      sessionExpiredNoticeShown.current = true;
      showToast({ message: '登录状态已失效，请重新登录。', tone: 'error' });
    }
  }, [searchParams, showToast]);

  return (
    <AuthShell
      eyebrow="WELCOME BACK"
      title="继续你的辩论"
      description="登录后即可进入公开大厅、参加比赛或查看属于你的记录。"
      footer={
        <p className="text-center text-sm text-slate-500">
          还没有账号？{' '}
          <Link
            className="font-black text-blue-700 hover:text-blue-900"
            href={registerHref}
            prefetch={false}
          >
            创建账号
          </Link>
        </p>
      }
    >
      <form
        className="space-y-5"
        onSubmit={form.handleSubmit(async (values) => {
          setSubmitting(true);
          try {
            const result = await authApi.login({
              ...values,
              ...(returnTo !== '/' ? { return_to: returnTo } : {}),
            });
            queryClient.setQueryData(authQueryKey, result);
            router.replace(
              result.user.must_change_password
                ? `/change-password?return_to=${encodeURIComponent(returnTo)}`
                : returnTo,
            );
          } catch (error) {
            const message = errorMessage(error);
            if (message) showToast({ message, tone: 'error' });
          } finally {
            setSubmitting(false);
          }
        })}
      >
        <label className="block text-sm font-bold text-slate-700">
          用户名
          <input
            {...form.register('username')}
            aria-describedby={form.formState.errors.username ? 'login-username-error' : undefined}
            aria-invalid={Boolean(form.formState.errors.username)}
            className={inputClass}
            autoComplete="username"
          />
          <FieldError id="login-username-error" message={form.formState.errors.username?.message} />
        </label>
        <PasswordInput
          name="password"
          label="密码"
          register={form.register}
          error={form.formState.errors.password?.message}
        />
        <button className={buttonClass} disabled={submitting} type="submit">
          {submitting ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <ArrowRight className="size-4" />
          )}
          {submitting ? '正在登录…' : '登录并进入'}
        </button>
      </form>
    </AuthShell>
  );
}

const registerSchema = z
  .object({
    username: z.string().trim().min(3, '请输入 3–32 个字符的用户名').max(32),
    real_name: z.string().trim().min(2, '请输入真实姓名').max(30),
    password: z.string().min(8, '密码至少 8 个字符').max(64),
    confirm_password: z.string().min(1, '请再次输入密码'),
    avatar_key: z.string().regex(/^human-(0[1-9]|1[0-6])$/, '请选择头像'),
    accepted: z.boolean().refine((value) => value, '请先阅读并同意平台条款'),
  })
  .refine((values) => values.password === values.confirm_password, {
    path: ['confirm_password'],
    message: '两次输入的密码不一致',
  });
type RegisterValues = z.infer<typeof registerSchema>;

export function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const termsQuery = useQuery({
    queryKey: ['legal', 'platform-terms'],
    queryFn: authApi.currentTerms,
  });
  const [file, setFile] = useState<File | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const returnTo = sanitizeAuthReturnTo(searchParams.get('return_to'));
  const loginHref = authHrefWithReturnTo('/login', returnTo);
  const termsHref = authHrefWithReturnTo('/terms', returnTo);
  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { accepted: false, avatar_key: 'human-01' },
  });
  const selectedAvatar = useWatch({ control: form.control, name: 'avatar_key' });

  return (
    <AuthShell
      eyebrow="JOIN THE FIELD"
      title="创建你的账号"
      description="使用真实姓名加入稷下。用户名只用于登录，比赛页面只展示真实姓名。"
      footer={
        <p className="text-center text-sm text-slate-500">
          已有账号？{' '}
          <Link
            className="font-black text-blue-700 hover:text-blue-900"
            href={loginHref}
            prefetch={false}
          >
            返回登录
          </Link>
        </p>
      }
    >
      <form
        className="space-y-4"
        onSubmit={form.handleSubmit(async (values) => {
          if (!termsQuery.data) return;
          setAvatarError(null);
          setSubmitting(true);
          try {
            const result = await authApi.register({
              username: values.username,
              real_name: values.real_name,
              password: values.password,
              platform_terms_version: termsQuery.data.version,
              avatar_key: values.avatar_key,
            });
            queryClient.setQueryData(authQueryKey, result);
            if (file) {
              try {
                const avatarResult = await authApi.uploadAvatar(file);
                queryClient.setQueryData(authQueryKey, avatarResult);
              } catch (error) {
                const message = errorMessage(error) ?? '头像上传失败，可稍后在资料页重试。';
                setAvatarError(message);
                showToast({ message, tone: 'error' });
                setSubmitting(false);
                return;
              }
            }
            router.replace(returnTo);
          } catch (error) {
            const message = errorMessage(error);
            if (message) showToast({ message, tone: 'error' });
          } finally {
            setSubmitting(false);
          }
        })}
      >
        <label className="block text-sm font-bold text-slate-700">
          用户名
          <input
            {...form.register('username')}
            aria-describedby={
              form.formState.errors.username ? 'register-username-error' : undefined
            }
            aria-invalid={Boolean(form.formState.errors.username)}
            className={inputClass}
            autoComplete="username"
          />
          <FieldError
            id="register-username-error"
            message={form.formState.errors.username?.message}
          />
        </label>
        <label className="block text-sm font-bold text-slate-700">
          真实姓名
          <input
            {...form.register('real_name')}
            aria-describedby={
              form.formState.errors.real_name ? 'register-real-name-error' : undefined
            }
            aria-invalid={Boolean(form.formState.errors.real_name)}
            className={inputClass}
            autoComplete="name"
          />
          <FieldError
            id="register-real-name-error"
            message={form.formState.errors.real_name?.message}
          />
        </label>
        <PasswordInput
          name="password"
          label="密码"
          register={form.register}
          error={form.formState.errors.password?.message}
        />
        <PasswordInput
          name="confirm_password"
          label="确认密码"
          register={form.register}
          error={form.formState.errors.confirm_password?.message}
        />
        <fieldset>
          <legend className="text-sm font-bold text-slate-700">选择头像</legend>
          <p className="mt-1 text-xs text-slate-500">注册后仍可在“我的页面”修改。</p>
          <div
            aria-describedby={
              form.formState.errors.avatar_key ? 'register-avatar-error' : undefined
            }
            aria-invalid={Boolean(form.formState.errors.avatar_key)}
            className="mt-3 grid grid-cols-8 gap-2"
            role="radiogroup"
            aria-label="选择头像"
          >
            {HUMAN_AVATAR_KEYS.map((key, index) => {
              const selected = selectedAvatar === key;
              const avatarName = `头像 ${index + 1}`;
              return (
                <label className={`avatar-preset ${selected ? 'is-selected' : ''}`} key={key}>
                  <input
                    {...form.register('avatar_key')}
                    aria-label={avatarName}
                    className="absolute inset-0 z-10 size-full cursor-pointer opacity-0"
                    defaultChecked={key === 'human-01'}
                    type="radio"
                    value={key}
                  />
                  <Image
                    alt=""
                    className="jx-identity-avatar pointer-events-none size-full rounded-full object-cover"
                    height={64}
                    src={avatarAssetUrl(key)}
                    width={64}
                  />
                </label>
              );
            })}
          </div>
          <FieldError
            id="register-avatar-error"
            message={form.formState.errors.avatar_key?.message}
          />
        </fieldset>
        <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-blue-200 bg-blue-50/50 px-4 py-3 text-sm font-semibold text-slate-600">
          <Upload className="size-4 text-blue-600" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">
            {file ? file.name : '可选：上传头像（JPEG、PNG、WebP，2 MB 内）'}
          </span>
          <input
            className="sr-only"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <label className="flex items-start gap-3 text-sm leading-6 text-slate-600">
          <input
            {...form.register('accepted')}
            aria-describedby={
              form.formState.errors.accepted ? 'register-accepted-error' : undefined
            }
            aria-invalid={Boolean(form.formState.errors.accepted)}
            className="mt-1 size-4 accent-blue-600"
            type="checkbox"
          />
          <span>
            我已阅读并同意{' '}
            <Link
              className="font-bold text-blue-700 underline-offset-4 hover:underline"
              href={termsHref}
              prefetch={false}
              target="_blank"
            >
              平台条款
            </Link>
          </span>
        </label>
        <FieldError
          id="register-accepted-error"
          message={form.formState.errors.accepted?.message}
        />
        {termsQuery.isError ? (
          <div
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-800"
            data-testid="register-terms-error"
            role="alert"
          >
            <span>平台条款暂时无法加载，创建账号已暂停。</span>
            <button
              className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-rose-300 bg-white px-3 text-xs font-black text-rose-800 hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-rose-100 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
              disabled={termsQuery.isFetching}
              onClick={() => void termsQuery.refetch()}
              type="button"
            >
              <RefreshCcw
                aria-hidden="true"
                className={`size-3.5 ${termsQuery.isFetching ? 'animate-spin' : ''}`}
              />
              {termsQuery.isFetching ? '正在加载' : '重新加载条款'}
            </button>
          </div>
        ) : null}
        {avatarError ? (
          <div
            className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800"
            role="status"
          >
            账号已创建，头像可稍后在资料页补充。{' '}
            <Link className="underline" href="/profile" prefetch={false}>
              去资料页重试
            </Link>
          </div>
        ) : null}
        <button className={buttonClass} disabled={submitting || !termsQuery.data} type="submit">
          {submitting ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <Check className="size-4" />
          )}
          {submitting ? '正在创建…' : '创建账号'}
        </button>
      </form>
    </AuthShell>
  );
}

export function TermsPageView() {
  const searchParams = useSearchParams();
  const returnTo = sanitizeAuthReturnTo(searchParams.get('return_to'));
  const registerHref = authHrefWithReturnTo('/register', returnTo);
  const query = useQuery({ queryKey: ['legal', 'platform-terms'], queryFn: authApi.currentTerms });
  return (
    <AuthShell
      eyebrow="PLATFORM TERMS"
      title="平台条款"
      description="注册前请了解平台如何使用账号资料与比赛记录。"
      footer={
        <p className="text-center text-sm text-slate-500">
          <Link className="font-bold text-blue-700" href={registerHref} prefetch={false}>
            返回注册
          </Link>
        </p>
      }
    >
      {query.isLoading ? <div className="h-40 animate-pulse rounded-2xl bg-slate-100" /> : null}
      {query.isError ? (
        <div
          className="rounded-2xl border border-rose-200 bg-rose-50 p-5 text-sm font-semibold text-rose-800"
          data-testid="terms-page-error"
          role="alert"
        >
          <p>条款暂时无法加载，请重试。</p>
          <button
            className="mt-4 inline-flex min-h-10 items-center gap-2 rounded-xl border border-rose-300 bg-white px-4 text-sm font-black text-rose-800 hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-rose-100 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
            type="button"
          >
            <RefreshCcw
              aria-hidden="true"
              className={`size-4 ${query.isFetching ? 'animate-spin' : ''}`}
            />
            {query.isFetching ? '正在加载' : '重新加载条款'}
          </button>
        </div>
      ) : null}
      {query.data ? (
        <article className="rounded-2xl border border-slate-100 bg-slate-50/80 p-5 text-sm leading-7 text-slate-700">
          <p className="mb-3 text-xs font-black tracking-[0.12em] text-blue-700">
            版本 {query.data.version}
          </p>
          <h2 className="mb-3 text-lg font-black text-slate-950">{query.data.title}</h2>
          <p>{query.data.body}</p>
        </article>
      ) : null}
    </AuthShell>
  );
}
